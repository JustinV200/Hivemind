"""Tests for the Queen's liveness at each Warden's own cadence.

Wardens do not share one heartbeat interval: a Virtual Cell's in-Cell Warden beats every 15 s, the
Hive Stand's keeps the manifest's, and a Swarm device keeps whatever it declares. The Queen used to
judge every one of them against the manifest's interval, so any Warden slower than it drew a false
`CELL_UNREACHABLE`; she now judges each against the interval its newest Heartbeat declared, never
less than the manifest's. Every test runs on a FakeClock (builders' defaults: a 5 s manifest
interval and a miss limit of 3, so a 15 s window) and waits on state, never on a timer.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/liveness.py and queen.py (codingrules section 3); split by
    feature (14.2) from test_queen_liveness.py and test_queen_liveness_stall.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.liveness for record_heartbeat, check_liveness and the judged interval.
    - hivemind.queen.inbox.links for Pulse, what a link's reader hears of each Heartbeat.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from builders.cells import make_cell
from builders.queen import make_queen_deps
from builders.supervision import make_telemetry

from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.inbox import Pulse
from hivemind.queen.queen import Queen
from hivemind.queen.ticks.liveness import WardenLiveness, check_liveness, record_heartbeat
from hivemind.supervision import AlarmKind
from waggle.clock import Clock, FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.ids import WardenId, new_warden_id
from waggle.messages.supervision import Heartbeat, WardenState
from waggle.transport.memory import MemoryTransport

_IN_CELL_S = 15.0  # hivemind.cli.in_cell.config's own default: three times the manifest's here.
_POLL_LIMIT = 4_000  # Loop turns a state wait may take before the test fails instead of hanging.


@dataclass(frozen=True, slots=True)
class _WardenPair:
    """One attachable Warden link, and the Warden's own end to send its Heartbeats from."""

    link: WardenLink
    transport: MemoryTransport
    hop: Hop

    async def beat(self, clock: Clock, interval_s: float) -> None:
        """Send one Heartbeat stamped by `clock`, declaring `interval_s` as its cadence."""
        heartbeat = Heartbeat(
            telemetry=make_telemetry(),
            task_id=None,
            worker_state=None,
            warden_state=WardenState.ACTIVE,
            children=(),
            grant_id=None,
            grant_spend=None,
            interval_s=interval_s,
        )
        await self.transport.send(wrap(heartbeat, self.hop, clock=clock))


def _pair(deps: QueenDeps) -> _WardenPair:
    """Build one more Warden link over a fresh MemoryTransport pair."""
    queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
    warden_id = new_warden_id(deps.clock)
    node_id = deps.identity.node_id
    link = WardenLink(
        warden_id=warden_id,
        cell=make_cell(clock=deps.clock),
        transport=queen_end,
        hop=Hop(sender=deps.identity.hive_id, recipient=warden_id, node_id=node_id),
    )
    hop = Hop(sender=warden_id, recipient=deps.identity.hive_id, node_id=node_id)
    return _WardenPair(link=link, transport=warden_end, hop=hop)


async def _wait_until(condition: Callable[[], bool]) -> None:
    """Yield the event loop until `condition()` holds, or fail instead of hanging."""
    for _ in range(_POLL_LIMIT):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")


def _unreachable(inbox: HumanInbox) -> list[str]:
    """Every CELL_UNREACHABLE Alarm escalated to the human, by origin."""
    return [a.origin for a in inbox.alarms if a.kind is AlarmKind.CELL_UNREACHABLE]


def _heard_at(queen: Queen, warden_id: WardenId, sent_at: object) -> bool:
    """True once the Queen's liveness row for `warden_id` stands on the Heartbeat sent `sent_at`."""
    row = queen.liveness.get(warden_id)
    return row is not None and row.last_heartbeat_at == sent_at


async def test_a_warden_is_judged_by_the_interval_its_heartbeat_declared() -> None:
    # Arrange: one Heartbeat declaring 15 s, against the manifest's 5 s.
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    liveness: dict[WardenId, WardenLiveness] = {}
    inbox = HumanInbox()
    record_heartbeat(deps, liveness, link.warden_id, Pulse(clock.now(), _IN_CELL_S))

    # Silent for 20 s: four of the manifest's intervals, past its window, but one of its own.
    clock.advance(20.0)
    await check_liveness(deps, (link,), liveness, inbox)
    assert not liveness[link.warden_id].is_offline
    assert liveness[link.warden_id].missed_heartbeats == 1
    assert _unreachable(inbox) == []

    # Past three of its own intervals (46 s in all): now it is silent, and said so once.
    clock.advance(26.0)
    await check_liveness(deps, (link,), liveness, inbox)
    await check_liveness(deps, (link,), liveness, inbox)
    assert liveness[link.warden_id].is_offline
    assert _unreachable(inbox) == [link.warden_id]


async def test_a_declared_interval_below_the_manifests_is_judged_by_the_manifests() -> None:
    # A Warden declaring a faster cadence than the Hive's gets no stricter judgement for it.
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    liveness: dict[WardenId, WardenLiveness] = {}
    inbox = HumanInbox()
    record_heartbeat(deps, liveness, link.warden_id, Pulse(clock.now(), 1.0))

    clock.advance(deps.heartbeat_interval_s * deps.heartbeat_miss_limit - 1.0)
    await check_liveness(deps, (link,), liveness, inbox)

    assert not liveness[link.warden_id].is_offline
    assert liveness[link.warden_id].missed_heartbeats == deps.heartbeat_miss_limit - 1
    assert _unreachable(inbox) == []


def test_a_late_heartbeat_is_judged_stale_by_its_own_declared_cadence() -> None:
    # Twenty seconds old: one 15 s interval of the Warden that declared it, four of the manifest's.
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    sent_at = clock.now()
    clock.advance(20.0)
    offline = WardenLiveness(last_heartbeat_at=None, missed_heartbeats=3, is_offline=True)
    slow = {link.warden_id: offline}
    fast = {link.warden_id: offline}

    record_heartbeat(deps, slow, link.warden_id, Pulse(sent_at, _IN_CELL_S))
    record_heartbeat(deps, fast, link.warden_id, Pulse(sent_at, deps.heartbeat_interval_s))

    assert slow[link.warden_id] == WardenLiveness(sent_at, 1, False, _IN_CELL_S)
    assert fast[link.warden_id].is_offline  # Stale by its own cadence: proves nothing now.
    assert fast[link.warden_id].last_heartbeat_at == sent_at


async def test_a_warden_beating_slower_than_the_manifest_is_never_reported_unreachable() -> None:
    # Arrange: the Hive Stand's Warden keeps the manifest's 5 s; a Virtual Cell's in-Cell Warden
    # keeps its own 15 s, three of the manifest's windows' worth of silence between beats.
    clock = FakeClock()
    deps, _link, _end = make_queen_deps(clock)
    stand, cell = _pair(deps), _pair(deps)
    queen = Queen(deps)
    await queen.attach_warden(stand.link)
    await queen.attach_warden(cell.link)
    run_task = asyncio.ensure_future(queen.run())

    # Ninety seconds, six of the Cell's own intervals. Each of its beats is sent only once the
    # Queen has judged the Stand's beat of the same instant, so she always sees the Cell's full
    # 15 s of silence first -- the moment the manifest's window used to call it unreachable.
    for step in range(1, 19):
        clock.advance(deps.heartbeat_interval_s)
        await _beat_and_wait(queen, stand, clock, deps.heartbeat_interval_s)
        if step % 3 == 0:
            await _beat_and_wait(queen, cell, clock, _IN_CELL_S)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert _unreachable(queen.human_inbox) == []
    assert queen.liveness[cell.link.warden_id].interval_s == _IN_CELL_S
    assert not queen.liveness[cell.link.warden_id].is_offline


async def _beat_and_wait(queen: Queen, pair: _WardenPair, clock: Clock, interval_s: float) -> None:
    """Send one Heartbeat from `pair` and wait until the Queen's liveness stands on it."""
    await pair.beat(clock, interval_s)
    sent_at = clock.now()
    await _wait_until(lambda: _heard_at(queen, pair.link.warden_id, sent_at))
