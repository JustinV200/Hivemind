"""Test watch mode: read-only collectors, no lease, a watch grant required, an answer that fits.

A Warden (the per-Cell supervisor) watches an idle Real Cell (a borrowed device) without a lease by
sending ``swarm.observe`` to its Pollen Packet (gateway-contract section 9.4, ADR-0054). The packet
answers from fixed collectors that read the platform's own facilities and never run a command a
request could shape: the process list, resource use, the tail of log files and file changes under
roots. The first group of tests runs a real packet and checks that each kind answers, that nothing
is collected without the ``watch`` capability for the Cell and ``fs:read`` for every root, that a
request naming another device is refused as invalid, and that the answer stamps its own clock. The
second group drives the executor directly to check what a packet cannot easily be made to do: an
answer too big for one frame is halved until it fits (or given up on), and a collector the
platform refuses is named in the reason while the others still answer.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.observe.executor, end to end
    through a running packet and directly; no lease is opened, because observing needs none.

Key invariants:
    - Observing changes nothing on the device: the tests check the files they watch are untouched.
    - A refusal is a ``control.error`` with the contract's code, never a closed connection.

See Also:
    - docs/waggle/gateway-contract.md section 9.4.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import psutil
import pytest
from device_kit import Device, Terms, make_device, running
from executor_kit import RecordingReporter, make_kit
from fast_clock import FastClock
from queen_double import QueenDouble, QueenSession
from scenarios import eventually
from states import frame_from

from pollen.executors import ExecutorDeps
from pollen.executors.observe import ObserveExecutor
from pollen.executors.observe.executor import MAX_SHRINKS
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.errors import FrameTooLargeError
from waggle.messages.base import WaggleMessage
from waggle.messages.control import ErrorMessage
from waggle.messages.labels import AccessLevel
from waggle.messages.swarm import FileChangeKind, Observation, ObservationKind, Observe


def _request(device: Device, **fields: object) -> Observe:
    """Build a ``swarm.observe`` for ``device`` that asks for resource use unless told otherwise."""
    values: dict[str, object] = {
        "device_id": device.state.device_id,
        "kinds": (ObservationKind.RESOURCE_USE,),
        "roots": (),
        "since_at": None,
        "reason": "A test looks at the device.",
    }
    values.update(fields)
    return Observe(**values)


async def _observe(
    queen: QueenDouble, session: QueenSession, device: Device, **fields: object
) -> Observation:
    """Send a ``swarm.observe`` as the Warden and return the observation that answered it."""
    sent = await session.send(_request(device, **fields), sender=queen.warden_id)
    answer = await session.expect(Observation, where=lambda e: e.correlation_id == sent.id)
    return answer.payload


async def _refused(
    queen: QueenDouble, session: QueenSession, device: Device, **fields: object
) -> str:
    """Send a ``swarm.observe`` as the Warden and return the code of the error that refused it."""
    sent = await session.send(_request(device, **fields), sender=queen.warden_id)
    answer = await session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
    return answer.payload.code


async def test_the_process_list_and_resource_use_are_reported(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        kinds = (ObservationKind.PROCESS_LIST, ObservationKind.RESOURCE_USE)
        seen = await _observe(queen, session, device, kinds=kinds, max_items=500)
        assert os.getpid() in {p.pid for p in seen.processes}
        assert seen.resources is not None and seen.resources.memory_bytes > 0
        assert seen.observed_at <= clock.now()
        assert seen.log_lines == () and seen.file_changes == ()


async def test_a_kind_that_was_not_asked_for_is_left_empty(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        seen = await _observe(queen, session, device)
        assert seen.processes == () and seen.resources is not None


async def test_log_lines_come_from_the_tail_of_the_files_under_a_root(
    queen: QueenDouble, device: Device, clock: Clock, tmp_path: Path
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text("first\nsecond\nthird\n")
    async with running(device, clock):
        session = await queen.next_session()
        kinds, roots = (ObservationKind.LOGS,), (str(logs),)
        seen = await _observe(queen, session, device, kinds=kinds, roots=roots)
        assert [line.line for line in seen.log_lines][-3:] == ["first", "second", "third"]
        assert (logs / "app.log").read_text() == "first\nsecond\nthird\n"


async def test_file_changes_are_found_by_comparing_with_the_last_look(
    queen: QueenDouble, device: Device, clock: Clock, tmp_path: Path
) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "old.txt").write_text("old")
    async with running(device, clock):
        session = await queen.next_session()
        kinds, roots = (ObservationKind.FILE_CHANGES,), (str(watched),)
        await _observe(queen, session, device, kinds=kinds, roots=roots)  # Takes the baseline.
        (watched / "new.txt").write_text("new")
        (watched / "old.txt").unlink()
        seen = await _observe(queen, session, device, kinds=kinds, roots=roots)
        changes = {Path(c.path).name: c.change for c in seen.file_changes}
        assert changes == {"new.txt": FileChangeKind.CREATED, "old.txt": FileChangeKind.DELETED}


async def test_a_device_without_the_watch_grant_refuses(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    terms = Terms(access=AccessLevel.READ_ONLY, capabilities=["fs:read:**"])
    device = make_device(queen, tmp_path / "data", clock, terms)
    async with running(device, clock):
        session = await queen.next_session()
        assert await _refused(queen, session, device) == "pollen.capability_denied"


async def test_a_root_without_fs_read_refuses(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    grants = [f"watch:{queen.cell_id}", f"fs:read:{tmp_path}/allowed/**"]
    device = make_device(queen, tmp_path / "data", clock, Terms(capabilities=grants))
    (tmp_path / "forbidden").mkdir()
    async with running(device, clock):
        session = await queen.next_session()
        code = await _refused(
            queen,
            session,
            device,
            kinds=(ObservationKind.LOGS,),
            roots=(str(tmp_path / "forbidden"),),
        )
        assert code == "pollen.capability_denied"


async def test_a_request_naming_another_device_is_invalid(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        other = device.state.device_id[:-1] + ("A" if device.state.device_id[-1] != "A" else "B")
        code = await _refused(queen, session, device, device_id=other)
        assert code == "waggle.codec.invalid_payload"


async def test_a_relative_root_is_refused(queen: QueenDouble, device: Device, clock: Clock) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        code = await _refused(
            queen, session, device, kinds=(ObservationKind.LOGS,), roots=("relative/dir",)
        )
        assert code == "pollen.path_refused"


class _SizedReporter(RecordingReporter):
    """A reporter whose frame holds at most ``max_lines`` log lines: a bigger answer is refused."""

    def __init__(self, max_lines: int) -> None:
        """Choose the frame size, and start with nothing refused."""
        super().__init__()
        self.refused = 0
        self._max_lines = max_lines

    async def send(
        self, payload: WaggleMessage, *, to: str, correlation_id: str | None = None
    ) -> bool:
        """Refuse an observation that does not fit, as the codec refuses an oversized frame."""
        if isinstance(payload, Observation) and len(payload.log_lines) > self._max_lines:
            self.refused += 1
            raise FrameTooLargeError("The frame is over the limit.")
        return await super().send(payload, to=to, correlation_id=correlation_id)


def _direct(
    tmp_path: Path,
    reporter: RecordingReporter,
    kinds: tuple[ObservationKind, ...],
    roots: tuple[str, ...] = (),
) -> tuple[ObserveExecutor, Envelope, ExecutorDeps]:
    """Build an executor over a kit using ``reporter``, and the Warden's request for ``kinds``."""
    clock = FastClock()
    kit = make_kit(tmp_path, clock)
    deps = replace(kit.deps, reporter=reporter)
    executor = ObserveExecutor(deps)
    request = Observe(
        device_id=kit.state.device_id,
        kinds=kinds,
        roots=roots,
        since_at=None,
        reason="A test looks at the device.",
    )
    return executor, frame_from(kit.state, request, clock, sender=kit.state.warden_id), deps


async def _answer(executor: ObserveExecutor, request: Envelope) -> None:
    """Prepare a request and run it, leaving the answer's task to the executor's scope."""
    await executor.prepare(request).run()


async def test_an_answer_too_big_for_one_frame_is_halved_until_it_fits(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text("".join(f"line {n}\n" for n in range(8)))
    sized = _SizedReporter(max_lines=1)
    executor, request, deps = _direct(tmp_path, sized, (ObservationKind.LOGS,), (str(logs),))
    async with deps.scope:
        await _answer(executor, request)
        answer = await sized.wait_for(Observation)
    assert len(answer.log_lines) == 1 and answer.is_truncated  # 8 lines, then 4, 2 and 1.
    assert sized.refused == 3 and answer.log_lines[0].line == "line 0"


async def test_an_answer_that_never_fits_is_given_up_after_a_few_tries(tmp_path: Path) -> None:
    sized = _SizedReporter(max_lines=-1)  # Even an empty answer is refused.
    executor, request, deps = _direct(tmp_path, sized, (ObservationKind.RESOURCE_USE,))
    async with deps.scope:
        await _answer(executor, request)
        await eventually(lambda: sized.refused == MAX_SHRINKS)
    assert sized.of(Observation) == []  # Nothing was sent, and nothing raised.


async def test_a_collector_the_platform_refuses_is_named_and_the_others_still_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_processes(_max_items: int) -> object:
        raise PermissionError

    def refuse_resources(_root: Path) -> object:
        raise psutil.AccessDenied

    monkeypatch.setattr("pollen.executors.observe.executor.collect_processes", refuse_processes)
    monkeypatch.setattr("pollen.executors.observe.executor.collect_resources", refuse_resources)
    kinds = (ObservationKind.PROCESS_LIST, ObservationKind.RESOURCE_USE, ObservationKind.LOGS)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text("still readable\n")
    reporter = RecordingReporter()
    executor, request, deps = _direct(tmp_path, reporter, kinds, (str(logs),))
    async with deps.scope:
        await _answer(executor, request)
        answer = await reporter.wait_for(Observation)
    assert answer.processes == () and answer.resources is None
    assert [line.line for line in answer.log_lines] == ["still readable"]
    assert answer.reason == "Not collected: PROCESS_LIST, RESOURCE_USE."
