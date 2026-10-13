"""Test frame admission and routing: who the packet listens to, and what it does with a request.

The Pollen Packet (the thin gateway program on a device the Hive borrows) acts on a frame only if
it is addressed to the device, comes from the Queen or the Cell's Warden, is a Queen-only kind only
when the Queen sent it, is neither stale nor from the future, and has not been seen (gateway-
contract sections 6 and 7); anything else is dropped and answered with nothing. An admitted frame
is routed by its payload type to an executor, its needs are checked against the capabilities held
*now*, and only then does the work run; a refusal is a ``control.error`` with a code from the wire
table, a request kind nobody serves draws ``pollen.unsupported`` and a bug inside the packet is
reported as ``pollen.internal_error`` without a traceback. The pure rule is tested directly; the
routing is tested with fake executors and a recording courier; a few cases run through a real
packet to prove the same rules hold on the wire.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.dispatch.

Key invariants:
    - A dropped frame produces no reply, no state change and no executor call.
    - No executor's work runs before the needs of its request are covered.

See Also:
    - docs/waggle/gateway-contract.md sections 6, 7, 9.5, 10 and 15.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from device_kit import Device, Live, Terms, live_lease, make_device, running
from frame_overrides import Overrides
from queen_double import QueenDouble
from scenarios import exec_message, python_argv
from states import RecordingCourier, frame_from, sample_state

from pollen.agent import DispatchDeps, Dispatcher, DropReason, admit, hive_nodes
from pollen.errors import InvalidRequestError, StateError
from pollen.executors import Need, Prepared
from pollen.state import StateStore
from waggle.clock import Clock, FakeClock
from waggle.envelope import Envelope
from waggle.errors import InvalidPayloadError
from waggle.messages.base import MAX_CLOCK_SKEW_S, MAX_MESSAGE_AGE_S, WaggleMessage
from waggle.messages.cell import CellSnapshotRequest
from waggle.messages.control import ErrorMessage, Ping, Pong, Wake
from waggle.messages.labels import AccessLevel
from waggle.messages.swarm import Colonize, ColonizeAction, NodeUpdate, RuntimeLevel


class ScriptedExecutor:
    """An executor whose behaviour a test scripts: what it needs, and what it does."""

    handles: ClassVar[tuple[type[WaggleMessage], ...]] = (Ping,)

    def __init__(self, needs: tuple[Need, ...] = (), failure: Exception | None = None) -> None:
        """Script what ``prepare`` asks for, or the failure it raises."""
        self.needs = needs
        self.failure = failure
        self.ran: list[str] = []

    def prepare(self, envelope: Envelope) -> Prepared:
        """Return a request that records itself when run, or raise the scripted failure."""

        async def run() -> None:
            self.ran.append(envelope.id)

        if self.failure is not None:
            raise self.failure
        return Prepared(run=run, needs=self.needs)


class Rig:
    """A dispatcher over a recording courier, with counters for what it reported."""

    def __init__(self, tmp_path: Path, executors: list[ScriptedExecutor], clock: Clock) -> None:
        """Build the dispatcher over ``executors`` with counters instead of a packet."""
        self.clock = clock
        self.state = sample_state(clock, granted_capabilities=("fs:read:**",))
        self.store = StateStore(tmp_path / "state.json", self.state)
        self.courier = RecordingCourier(self.state, clock, tmp_path / "outbox.jsonl")
        self.drops: Counter[str] = Counter()
        self.heard = 0
        self.admitted = 0
        deps = DispatchDeps(
            state=self.store,
            clock=clock,
            courier=self.courier,
            heard=self._hear,
            on_drop=self._drop,
            on_admit=self._admit,
        )
        self.dispatcher = Dispatcher(deps, executors)

    def _hear(self) -> None:
        """Count a moment the Queen was heard from."""
        self.heard += 1

    def _drop(self, reason: str) -> None:
        """Count a dropped frame by its reason."""
        self.drops[reason] += 1

    def _admit(self) -> None:
        """Count an admitted frame."""
        self.admitted += 1

    def frame(
        self,
        payload: WaggleMessage,
        sender: str | None = None,
        overrides: Overrides | None = None,
    ) -> Envelope:
        """Wrap ``payload`` as a frame to the rig's device."""
        return frame_from(self.state, payload, self.clock, sender, overrides)


@pytest.fixture
def fake_clock() -> FakeClock:
    """A clock the test owns."""
    return FakeClock()


# ──────────────────────────────────────────────────────────────────────────────
# The pure rule
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sender", "odd", "shift_s", "reason"),
    [
        (None, Overrides(), None, None),
        (
            None,
            Overrides(recipient="device_01M4TJ4GG06CSK6CSK6CSK6CSK"),
            None,
            DropReason.MISADDRESSED,
        ),
        ("warden_01M4TJ4GG048H248H248H248H2", Overrides(), None, DropReason.SENDER),
        (None, Overrides(node_id="node_01M4TJ4GG0248H248H248H248H"), None, DropReason.SENDER),
        (None, Overrides(), -(MAX_MESSAGE_AGE_S + 1), DropReason.STALE),
        (None, Overrides(), MAX_CLOCK_SKEW_S + 1, DropReason.FUTURE),
        (None, Overrides(), -MAX_MESSAGE_AGE_S + 5, None),
        (None, Overrides(), MAX_CLOCK_SKEW_S - 5, None),
    ],
)
def test_admit_applies_the_sender_and_freshness_rules(
    fake_clock: FakeClock,
    sender: str | None,
    odd: Overrides,
    shift_s: float | None,
    reason: DropReason | None,
) -> None:
    state = sample_state(fake_clock)
    now = fake_clock.now()
    if shift_s is not None:
        odd = replace(odd, sent_at=now + timedelta(seconds=shift_s))
    frame = frame_from(state, Ping(), fake_clock, sender, odd)
    assert admit(frame, state, now, hive_nodes(state, now)) is reason


def test_a_queen_only_kind_from_the_warden_is_dropped(fake_clock: FakeClock) -> None:
    state = sample_state(fake_clock)
    now = fake_clock.now()
    update = NodeUpdate(
        device_id=state.device_id,
        cell_id=state.cell_id,
        warden_id=state.warden_id,
        level=RuntimeLevel.GATEWAY,
        access_level=AccessLevel.FULL,
        comb_shield=state.comb_shield,
        granted_capabilities=("exec:*",),
        offline_limit_s=60.0,
        heartbeat_interval_s=10.0,
        reason="x",
    )
    from_warden = frame_from(state, update, fake_clock, sender=state.warden_id)
    from_queen = frame_from(state, update, fake_clock)
    nodes = hive_nodes(state, now)
    assert admit(from_warden, state, now, nodes) is DropReason.QUEEN_ONLY
    assert admit(from_queen, state, now, nodes) is None


# ──────────────────────────────────────────────────────────────────────────────
# Routing with fake executors
# ──────────────────────────────────────────────────────────────────────────────


async def test_an_admitted_frame_runs_its_executor_and_marks_the_queen_heard(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    executor = ScriptedExecutor()
    rig = Rig(tmp_path, [executor], fake_clock)
    frame = rig.frame(Ping())
    await rig.dispatcher.handle(frame)
    assert (executor.ran, rig.admitted, rig.heard) == ([frame.id], 1, 1)


async def test_a_duplicate_is_dropped_and_counted(tmp_path: Path, fake_clock: FakeClock) -> None:
    executor = ScriptedExecutor()
    rig = Rig(tmp_path, [executor], fake_clock)
    frame = rig.frame(Ping())
    await rig.dispatcher.handle(frame)
    await rig.dispatcher.handle(frame)
    assert len(executor.ran) == 1
    assert rig.drops == Counter({"DUPLICATE": 1})


async def test_a_frame_dropped_by_a_rule_is_not_remembered_as_seen(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    executor = ScriptedExecutor()
    rig = Rig(tmp_path, [executor], fake_clock)
    age = timedelta(seconds=MAX_MESSAGE_AGE_S + 9)
    stale = rig.frame(Ping(), overrides=Overrides(sent_at=fake_clock.now() - age))
    await rig.dispatcher.handle(stale)
    fresh = stale.model_copy(update={"sent_at": fake_clock.now()})
    await rig.dispatcher.handle(fresh)
    assert rig.drops == Counter({"STALE": 1})
    assert executor.ran == [stale.id]


async def test_a_frame_from_the_warden_is_acted_on_but_does_not_count_as_the_queen_heard(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    executor = ScriptedExecutor()
    rig = Rig(tmp_path, [executor], fake_clock)
    node = "node_01M4TJ4GG0248H248H248H248H"
    warden_on_the_device = rig.frame(
        Ping(), sender=rig.state.warden_id, overrides=Overrides(node_id=node)
    )
    await rig.dispatcher.handle(warden_on_the_device)
    assert (len(executor.ran), rig.heard) == (1, 0)


async def test_needs_are_checked_before_the_work_runs(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    executor = ScriptedExecutor(needs=(Need("exec", "/bin/sh"),))
    rig = Rig(tmp_path, [executor], fake_clock)
    frame = rig.frame(Ping())
    await rig.dispatcher.handle(frame)
    assert executor.ran == []
    (reply,) = rig.courier.sent
    assert isinstance(reply.payload, ErrorMessage)
    assert reply.payload.code == "pollen.capability_denied"
    assert (reply.to, reply.correlation_id) == (rig.state.hive_id, frame.id)
    assert "/bin/sh" not in reply.payload.message


async def test_grants_are_read_afresh_for_every_request(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    executor = ScriptedExecutor(needs=(Need("exec", "/bin/sh"),))
    rig = Rig(tmp_path, [executor], fake_clock)
    await rig.dispatcher.handle(rig.frame(Ping()))
    await rig.store.update(granted_capabilities=("exec:*",))
    await rig.dispatcher.handle(rig.frame(Ping()))
    assert len(executor.ran) == 1


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (InvalidRequestError("bad"), "waggle.codec.invalid_payload"),
        (StateError("disk"), "pollen.internal_error"),
        (RuntimeError("a bug with /secret/path in it"), "pollen.internal_error"),
    ],
)
async def test_a_failure_is_answered_with_a_wire_code_and_no_detail(
    tmp_path: Path, fake_clock: FakeClock, failure: Exception, code: str
) -> None:
    rig = Rig(tmp_path, [ScriptedExecutor(failure=failure)], fake_clock)
    await rig.dispatcher.handle(rig.frame(Ping()))
    (reply,) = rig.courier.sent
    assert isinstance(reply.payload, ErrorMessage)
    assert reply.payload.code == code
    assert "secret" not in reply.payload.message and "Traceback" not in reply.payload.message


async def test_a_request_nobody_serves_draws_unsupported_and_an_event_is_dropped(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    rig = Rig(tmp_path, [ScriptedExecutor()], fake_clock)
    colonize = Colonize(
        cell_id=rig.state.cell_id,
        device_id=rig.state.device_id,
        action=ColonizeAction.PREPARE,
        warden_id=rig.state.warden_id,
        runtime_version="1.0.0",
        warden_url=None,
        deadline_s=10.0,
        reason="x",
    )
    await rig.dispatcher.handle(rig.frame(colonize))
    await rig.dispatcher.handle(rig.frame(Wake(provider=None, reason="x")))
    (reply,) = rig.courier.sent
    assert isinstance(reply.payload, ErrorMessage)
    assert (reply.payload.code, reply.payload.failed_kind) == (
        "pollen.unsupported",
        "swarm.colonize",
    )


def test_two_executors_may_not_claim_the_same_payload_type(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValueError, match="Two executors handle Ping"):
        Rig(tmp_path, [ScriptedExecutor(), ScriptedExecutor()], fake_clock)


async def test_a_frame_with_a_bad_payload_is_answered_only_if_the_queen_or_warden_sent_it(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    rig = Rig(tmp_path, [ScriptedExecutor()], fake_clock)
    ids = {"message_id": "msg_01M4TJ4GG0ANANANANANANANAN", "kind": "session.exec"}
    await rig.dispatcher.refuse_invalid(InvalidPayloadError("bad", sender=rig.state.hive_id, **ids))
    await rig.dispatcher.refuse_invalid(InvalidPayloadError("bad", sender="warden_x", **ids))
    await rig.dispatcher.refuse_invalid(
        InvalidPayloadError("bad", sender=rig.state.hive_id, message_id="nonsense")
    )
    (reply,) = rig.courier.sent
    assert isinstance(reply.payload, ErrorMessage)
    assert reply.payload.code == "waggle.codec.invalid_payload"
    assert (reply.correlation_id, reply.payload.failed_kind) == (ids["message_id"], "session.exec")


# ──────────────────────────────────────────────────────────────────────────────
# The same rules on the wire
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_frame_from_a_stranger_or_for_someone_else_is_ignored_on_the_wire(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with running(device, clock):
        session = await queen.next_session()
        await session.send(Ping(), sender="warden_01M4TJ4GG048H248H248H248H2")
        elsewhere = Overrides(recipient="device_01M4TJ4GG06CSK6CSK6CSK6CSK")
        await session.send(Ping(), overrides=elsewhere)
        await session.send(Ping(), overrides=Overrides(sent_at=clock.now() - timedelta(days=8)))
        await session.send(Ping(), overrides=Overrides(sent_at=clock.now() + timedelta(minutes=10)))
        await session.expect_none(Pong)
        await session.send(Ping())
        await session.expect(Pong)


async def test_a_read_only_device_runs_no_command_at_all(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    marker = tmp_path / "ran"
    device = make_device(queen, tmp_path / "data", clock, Terms(access=AccessLevel.READ_ONLY))
    async with live_lease(queen, device, clock, access=AccessLevel.READ_ONLY) as live:
        code = f"open({str(marker)!r}, 'w').close()"
        message = exec_message(live.lease.lease_id, python_argv(code))
        sent = await live.session.send(message, sender=queen.warden_id)
        refused = await live.session.expect(
            ErrorMessage, where=lambda e: e.correlation_id == sent.id
        )
        assert refused.payload.code == "pollen.capability_denied"
        assert not marker.exists()


async def test_a_request_kind_the_packet_does_not_implement_draws_unsupported_on_the_wire(
    live: Live,
) -> None:
    unsupported = CellSnapshotRequest(cell_id=live.lease.cell_id, purpose="A test asks.")
    sent = await live.session.send(unsupported, sender=live.queen.warden_id)
    refused = await live.session.expect(ErrorMessage, where=lambda e: e.correlation_id == sent.id)
    assert refused.payload.code == "pollen.unsupported"
