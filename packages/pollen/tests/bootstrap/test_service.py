"""Test pollen.bootstrap.service: swarm.colonize is refused, answered and bounded as specified.

The service is driven the way the dispatcher drives an executor: ``prepare`` checks the order,
``run`` hands it to a task and returns, and the answer is a ``swarm.colonized`` sent durably and
correlated to the order. The Warden is the stub of ``support/runtime_kit.py``.

Fits into the Hive:
    Mirrors src/pollen/bootstrap/service.py (codingrules section 3).

Key invariants:
    - Every test that starts a Warden ends it, and leaves the task scope it entered.

See Also:
    - pollen.bootstrap.service for the module under test.
    - waggle.messages.swarm.ladder for the Colonize and Colonized messages.
"""

from __future__ import annotations

import os
import signal
import stat
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from executor_kit import Kit, make_kit
from runtime_kit import STUB_VERSION, build_wheelhouse, fake_runtime, wait_for_file
from states import frame_from

from pollen.bootstrap import BootstrapTimings, ColonizeService, WardenKeeper, installed_version
from pollen.errors import InvalidRequestError
from pollen.state import InstallSettings
from waggle.clock import Clock
from waggle.messages.swarm import Colonize, ColonizeAction, Colonized, ColonizeOutcome, RuntimeLevel

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the stub Warden and interpreters are POSIX scripts"
)

URL = "ws://127.0.0.1:8720"
FAST = BootstrapTimings(settle_s=0.5, stop_grace_s=3.0, watch_interval_s=0.05)


def _order(kit: Kit, action: ColonizeAction, **changes: object) -> Colonize:
    """A valid order for the kit's device; ``changes`` replace fields (None clears one)."""
    state = kit.state
    fields: dict[str, object] = {
        "cell_id": state.cell_id,
        "device_id": state.device_id,
        "action": action,
        "warden_id": state.warden_id,
        "runtime_version": STUB_VERSION if action is not ColonizeAction.START else None,
        "warden_url": URL if action is ColonizeAction.START else None,
        "deadline_s": 20.0,
        "reason": "The test asks.",
    }
    if action is ColonizeAction.WITHDRAW:
        fields["runtime_version"] = None
    fields.update(changes)
    return Colonize(**fields)


@pytest.fixture
async def served(tmp_path: Path, clock: Clock) -> AsyncIterator[tuple[ColonizeService, Kit]]:
    """A service over a device with a finished runtime and a device manifest; scope entered."""
    manifest = tmp_path / "warden.toml"
    manifest.write_text("[llm]\n", encoding="utf-8")
    install = InstallSettings(warden_manifest=str(manifest))
    kit = make_kit(tmp_path, clock, install=install)
    fake_runtime(kit.layout, kit.deps.shim)
    service = ColonizeService(kit.deps, WardenKeeper(kit.deps, FAST))
    async with kit.deps.scope:
        try:
            yield service, kit
        finally:
            record = kit.deps.state.current.warden
            if record is not None:
                os.killpg(record.pid, signal.SIGKILL)


async def _ask(service: ColonizeService, kit: Kit, order: Colonize, clock: Clock) -> Colonized:
    """Deliver ``order`` as the dispatcher would and wait for the answer."""
    envelope = frame_from(kit.state, order, clock)
    prepared = service.prepare(envelope)
    await prepared.run()
    reply = await kit.reporter.wait_for(Colonized, lambda c: c.action is order.action)
    sent = kit.reporter.sent[-1]
    assert sent.is_durable and sent.correlation_id == envelope.id  # Delivered after a reconnect.
    assert sent.to == envelope.sender
    return reply


# ──────────────────────────────────────────────────────────────────────────────
# Refused outright
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["cell_id", "device_id", "warden_id"])
async def test_an_order_for_another_device_cell_or_warden_is_refused(
    served: tuple[ColonizeService, Kit], clock: Clock, field: str
) -> None:
    service, kit = served
    other = make_kit(kit.layout.root.parent / "other", clock).state
    order = _order(kit, ColonizeAction.PREPARE, **{field: getattr(other, field)})

    with pytest.raises(InvalidRequestError, match="another device, Cell or Warden"):
        service.prepare(frame_from(kit.state, order, clock))


async def test_a_pinned_version_that_is_not_a_version_is_refused(
    served: tuple[ColonizeService, Kit], clock: Clock
) -> None:
    service, kit = served
    order = _order(kit, ColonizeAction.PREPARE, runtime_version="1.0 --index-url http://x")

    with pytest.raises(InvalidRequestError, match="not a version"):
        service.prepare(frame_from(kit.state, order, clock))


async def test_a_listener_address_a_warden_may_not_dial_is_refused(
    served: tuple[ColonizeService, Kit], clock: Clock
) -> None:
    service, kit = served
    order = _order(kit, ColonizeAction.START, warden_url="ws://hive.example.org:8720")

    with pytest.raises(InvalidRequestError, match="may not dial"):
        service.prepare(frame_from(kit.state, order, clock))


# ──────────────────────────────────────────────────────────────────────────────
# The three actions
# ──────────────────────────────────────────────────────────────────────────────


async def test_prepare_of_a_runtime_already_there_is_answered_prepared(
    served: tuple[ColonizeService, Kit], clock: Clock
) -> None:
    service, kit = served

    reply = await _ask(service, kit, _order(kit, ColonizeAction.PREPARE), clock)

    assert reply.outcome is ColonizeOutcome.PREPARED
    assert (reply.runtime_version, reply.warden_pid) == (STUB_VERSION, None)
    assert reply.level is RuntimeLevel.GATEWAY  # Preparing starts nothing.


async def test_start_then_withdraw_move_the_device_up_the_ladder_and_down_again(
    served: tuple[ColonizeService, Kit], clock: Clock
) -> None:
    service, kit = served

    started = await _ask(service, kit, _order(kit, ColonizeAction.START), clock)
    await wait_for_file(kit.layout.warden_state / "ready")
    withdrawn = await _ask(service, kit, _order(kit, ColonizeAction.WITHDRAW), clock)

    record = kit.deps.state.current.warden
    assert started.outcome is ColonizeOutcome.STARTED
    assert started.level is RuntimeLevel.COLONIZED
    assert started.runtime_version == STUB_VERSION
    assert started.warden_pid is not None and started.warden_pid > 0
    assert record is None  # Withdrawn since.
    assert withdrawn.outcome is ColonizeOutcome.WITHDRAWN
    assert (withdrawn.level, withdrawn.runtime_version, withdrawn.warden_pid) == (
        RuntimeLevel.GATEWAY,
        None,
        None,
    )


async def test_a_step_that_cannot_be_done_is_answered_failed_with_the_level_it_leaves(
    tmp_path: Path, clock: Clock
) -> None:
    kit = make_kit(tmp_path, clock, install=InstallSettings())  # No source, no manifest recorded.
    service = ColonizeService(kit.deps, WardenKeeper(kit.deps, FAST))
    async with kit.deps.scope:
        prepare = await _ask(service, kit, _order(kit, ColonizeAction.PREPARE), clock)
        start = await _ask(service, kit, _order(kit, ColonizeAction.START), clock)

    assert prepare.outcome is start.outcome is ColonizeOutcome.FAILED
    assert "No runtime source was recorded" in prepare.reason
    assert "has not been prepared" in start.reason
    assert (prepare.level, start.level) == (RuntimeLevel.GATEWAY, RuntimeLevel.GATEWAY)
    assert (prepare.runtime_version, start.warden_pid) == (None, None)


# ──────────────────────────────────────────────────────────────────────────────
# Bounded, and one at a time
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_step_the_disk_refuses_is_answered_failed_and_names_no_path(
    tmp_path: Path, clock: Clock
) -> None:
    wheels = build_wheelhouse(tmp_path / "wheels")
    kit = make_kit(tmp_path, clock, install=InstallSettings(runtime_wheelhouse=str(wheels)))
    kit.layout.runtime.write_text("a file where the directory should be", encoding="utf-8")
    service = ColonizeService(kit.deps, WardenKeeper(kit.deps, FAST))

    async with kit.deps.scope:
        reply = await _ask(service, kit, _order(kit, ColonizeAction.PREPARE), clock)

    assert reply.outcome is ColonizeOutcome.FAILED
    assert "refused a file the step needs" in reply.reason
    assert str(tmp_path) not in reply.reason


def _hanging_python(tmp_path: Path) -> Path:
    """A stand-in interpreter that passes the version probe and hangs on every other command."""
    path = tmp_path / "python3.12"
    path.write_text(
        f"#!{sys.executable}\nimport os, sys, time\n"
        "if sys.argv[1] == '-c':\n    print('3 12')\nelse:\n"
        "    open(os.environ['PIDFILE'], 'w').write(str(os.getpid()))\n    time.sleep(120)\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


async def test_run_returns_at_once_and_a_step_past_its_deadline_is_answered_failed(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIDFILE", str(tmp_path / "pid"))
    wheels = build_wheelhouse(tmp_path / "wheels")
    install = InstallSettings(
        runtime_wheelhouse=str(wheels), runtime_python=str(_hanging_python(tmp_path))
    )
    kit = make_kit(tmp_path, clock, install=install)
    service = ColonizeService(kit.deps, WardenKeeper(kit.deps, FAST))
    order = _order(kit, ColonizeAction.PREPARE, deadline_s=0.5)
    async with kit.deps.scope:
        prepared = service.prepare(frame_from(kit.state, order, clock))

        await prepared.run()  # Returns while the step is still running: the reader is free.
        assert not kit.reporter.of(Colonized)
        reply = await kit.reporter.wait_for(Colonized)

    assert reply.outcome is ColonizeOutcome.FAILED
    assert "longer than 0.5 s" in reply.reason
    assert await installed_version(kit.deps) is None


async def test_an_order_that_arrives_while_a_step_runs_is_answered_failed_at_once(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "pid"
    monkeypatch.setenv("PIDFILE", str(pidfile))
    wheels = build_wheelhouse(tmp_path / "wheels")
    install = InstallSettings(
        runtime_wheelhouse=str(wheels), runtime_python=str(_hanging_python(tmp_path))
    )
    kit = make_kit(tmp_path, clock, install=install)
    service = ColonizeService(kit.deps, WardenKeeper(kit.deps, FAST))
    async with kit.deps.scope:
        first = service.prepare(frame_from(kit.state, _order(kit, ColonizeAction.PREPARE), clock))
        await first.run()
        await wait_for_file(pidfile)  # The first step is now running.

        second = await _ask(service, kit, _order(kit, ColonizeAction.WITHDRAW), clock)

    assert second.outcome is ColonizeOutcome.FAILED
    assert "Another colonization step is still running" in second.reason
