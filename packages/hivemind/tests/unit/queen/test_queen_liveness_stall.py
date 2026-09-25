"""Tests for the Queen's liveness when her own tick stalls: the Warden's pulse, not her attention.

A real run (2026-09-24) stalled the Queen's tick on a Virtual Cell provision for longer than the
miss limit while the Hive Stand's Warden kept heartbeating; she then heard one stale Heartbeat per
tick, marked the Warden back online on each and raised a fresh CELL_UNREACHABLE Alarm for each.
Every test here drives a real Queen over real `MemoryTransport` pairs on a FakeClock (builders'
defaults: a 5 s heartbeat interval and a miss limit of 3, so a 15 s window), and waits on state
(what she has handled, what her liveness says), never on a timer.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py, inbox/links.py and ticks/liveness.py (codingrules
    section 3); split by feature (14.2) from test_queen_liveness.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.inbox.links for LinkReaders, the per-link readers under test here.
    - hivemind.queen.ticks.liveness for record_heartbeat and check_liveness.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

from builders.cells import make_cell
from builders.forage import make_capacity
from builders.human import queen_responder
from builders.queen import make_queen_deps
from builders.supervision import make_telemetry
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task
from hivemind.cell import CellKind, CombShieldLevel
from hivemind.hive import BackendCapabilities, NetworkPolicy, VirtualCellSpec
from hivemind.llm import FakeLLMProvider
from hivemind.llm.models import LLMRequest, LLMResponse
from hivemind.queen.attach import detach_warden
from hivemind.queen.chat import ChatKind, ChatQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.errors import UnknownWardenError
from hivemind.queen.inbox import LINK_QUEUE_SIZE
from hivemind.queen.placement import Placement, PlacementPolicy, VirtualBackendCandidate
from hivemind.queen.queen import Queen
from hivemind.supervision import AlarmKind
from waggle.clock import Clock, FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.ids import WardenId, new_device_id, new_warden_id
from waggle.messages.supervision import Heartbeat, WardenState
from waggle.transport.memory import MemoryTransport

_STALL_BEATS = 8  # Heartbeats sent while a tick stalls: 40 s, and five of them stale on arrival.
_OUTAGE_S = 16.0  # Silence just past the 15 s window.
_POLL_LIMIT = 4_000  # Loop turns a state wait may take before the test fails instead of hanging.
_REPLY = {"action": "REPLY", "reason": "The human asked for progress.", "message": "Nearly."}
_READER = "LinkReaders._read"  # The qualified name of every link's own reader coroutine.


@dataclass(frozen=True, slots=True)
class _WardenPair:
    """One attachable Warden link, and the Warden's own end to send its Heartbeats from."""

    link: WardenLink
    transport: MemoryTransport
    hop: Hop

    async def beat(self, clock: Clock, tokens_used: int = 2_048) -> None:
        """Send one Heartbeat stamped by `clock`, its telemetry tagged with `tokens_used`."""
        heartbeat = Heartbeat(
            telemetry=make_telemetry(tokens_used=tokens_used),
            task_id=None,
            worker_state=None,
            warden_state=WardenState.ACTIVE,
            children=(),
            grant_id=None,
            grant_spend=None,
            interval_s=5.0,
        )
        await self.transport.send(wrap(heartbeat, self.hop, clock=clock))


@dataclass
class _StallingProvider:
    """A VirtualCellProvider whose acquire holds until released: a slow provision."""

    result: WardenLink
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def acquire(self, placement: Placement, task: Task) -> WardenLink:
        """Hold until the test releases it, then hand back `result`."""
        self.entered.set()
        await self.release.wait()
        return self.result


class _SlowModel(FakeLLMProvider):
    """A FakeLLMProvider holding every call until released: a long episode on a slow model."""

    def __init__(self, responder: Callable[[LLMRequest], LLMResponse]) -> None:
        """Wrap `responder`; every call waits on `release` first."""
        super().__init__(responder=responder)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Answer only once released; see `FakeLLMProvider.complete`."""
        self.entered.set()
        await self.release.wait()
        return await super().complete(request)


def _pair(deps: QueenDeps, kind: CellKind = CellKind.REAL) -> _WardenPair:
    """Build one more Warden link over a fresh MemoryTransport pair."""
    queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
    warden_id = new_warden_id(deps.clock)
    node_id = deps.identity.node_id
    link = WardenLink(
        warden_id=warden_id,
        cell=make_cell(kind=kind, clock=deps.clock),
        transport=queen_end,
        hop=Hop(sender=deps.identity.hive_id, recipient=warden_id, node_id=node_id),
    )
    hop = Hop(sender=warden_id, recipient=deps.identity.hive_id, node_id=node_id)
    return _WardenPair(link=link, transport=warden_end, hop=hop)


def _virtual_backend(deps: QueenDeps) -> VirtualBackendCandidate:
    """One Virtual backend with room, so a `prefer = "virtual"` placement provisions on it."""
    spec = VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.NONE,
        capacity=make_capacity(),
        comb_shield=CombShieldLevel.MEADOW,
        hive_id=deps.identity.hive_id,
    )
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True)
    return VirtualBackendCandidate(name="docker", capabilities=capabilities, specs=(spec,))


async def _wait_until(condition: Callable[[], bool | Awaitable[bool]]) -> None:
    """Yield the event loop until `condition()` (plain or awaitable) holds, or fail."""
    for _ in range(_POLL_LIMIT):
        result = condition()
        if isinstance(result, Awaitable):
            result = await result
        if result:
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")


async def _handled(queen: Queen, warden_id: WardenId, tokens_used: int) -> bool:
    """True once the Heartbeat tagged `tokens_used` is the newest `warden_id` one she handled."""
    try:
        return (await queen.telemetry(warden_id)).tokens_used == tokens_used
    except UnknownWardenError:
        return False  # No Heartbeat handled yet.


def _unreachable(queen: Queen) -> list[str]:
    """Every CELL_UNREACHABLE Alarm the Queen escalated to the human, by origin."""
    alarms = queen.human_inbox.alarms
    return [a.origin for a in alarms if a.kind is AlarmKind.CELL_UNREACHABLE]


async def _alarm_lines(deps: QueenDeps) -> list[str]:
    """Every Alarm line the Queen posted to the chat."""
    return [line.text for line in await deps.chat.read(ChatQuery()) if line.kind is ChatKind.ALARM]


def _readers() -> int:
    """Count the live link reader tasks (`hivemind.queen.inbox.links.LinkReaders._read`)."""
    names = [getattr(task.get_coro(), "__qualname__", "") for task in asyncio.all_tasks()]
    return names.count(_READER)


async def _stop(queen: Queen, run_task: asyncio.Task[None]) -> None:
    """Stop the Queen and let her in-flight tick, liveness sweep included, finish."""
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)


async def _heartbeat_through_a_stall(
    pair: _WardenPair, clock: FakeClock, interval_s: float
) -> None:
    """Keep `pair`'s Warden heartbeating for `_STALL_BEATS` intervals while the Queen is stuck."""
    for beat in range(1, _STALL_BEATS + 1):
        clock.advance(interval_s)
        await pair.beat(clock, tokens_used=beat)


async def test_a_tick_stalled_on_a_provision_past_the_miss_limit_raises_no_alarm() -> None:
    # The exact real-run shape: a ready task placed on a Virtual Cell whose provision takes 40 s
    # against a 15 s window, the Warden heartbeating all the while. The provision no longer runs
    # inside her tick (hivemind.queen.dispatcher.provisions), so this now also holds with no stall.
    clock = FakeClock()
    base, _link, _end = make_queen_deps(clock)
    stand = _pair(base)
    provider = _StallingProvider(result=_pair(base, CellKind.VIRTUAL).link)
    deps = dataclasses.replace(
        base,
        virtual_provider=provider,
        virtual_backends=(_virtual_backend(base),),
        placement_policy=PlacementPolicy(prefer="virtual"),
    )
    queen = Queen(deps)
    await queen.attach_warden(stand.link)
    await stand.beat(clock)
    await deps.chamber.submit(make_graph_draft({"root": ()}))
    run_task = asyncio.ensure_future(queen.run())
    await _wait_until(provider.entered.is_set)

    await _heartbeat_through_a_stall(stand, clock, deps.heartbeat_interval_s)
    provider.release.set()
    await _wait_until(lambda: _handled(queen, stand.link.warden_id, _STALL_BEATS))
    await _stop(queen, run_task)

    assert _unreachable(queen) == []
    assert await _alarm_lines(deps) == []
    assert not queen.liveness[stand.link.warden_id].is_offline


async def test_a_tick_stalled_on_a_long_awake_episode_raises_no_alarm() -> None:
    # The stall lands between her drain and her liveness sweep in one tick: the Heartbeats sent
    # during it are heard by the link's reader, and the sweep judges by them.
    clock = FakeClock()
    model = _SlowModel(queen_responder(_REPLY, []))
    deps, _link, _end = make_queen_deps(clock, fake_provider=model)
    stand = _pair(deps)
    queen = Queen(deps)
    await queen.attach_warden(stand.link)
    await stand.beat(clock)
    run_task = asyncio.ensure_future(queen.run())
    await _wait_until(lambda: _handled(queen, stand.link.warden_id, 2_048))

    # A human's message always wakes a model, and this model takes 40 s to answer.
    await queen.post_human_message("How is the haiku going?", new_device_id(clock))
    await _wait_until(model.entered.is_set)
    await _heartbeat_through_a_stall(stand, clock, deps.heartbeat_interval_s)
    model.release.set()
    await _wait_until(lambda: _handled(queen, stand.link.warden_id, _STALL_BEATS))
    await _stop(queen, run_task)

    assert _unreachable(queen) == []
    assert await _alarm_lines(deps) == []


async def test_a_silent_warden_raises_one_alarm_per_outage() -> None:
    clock = FakeClock()
    deps, _link, _end = make_queen_deps(clock)
    warden = _pair(deps)
    warden_id = warden.link.warden_id
    queen = Queen(deps)
    await queen.attach_warden(warden.link)
    await warden.beat(clock)
    run_task = asyncio.ensure_future(queen.run())
    await _wait_until(lambda: _handled(queen, warden_id, 2_048))

    # Silent past the window: one Alarm, and none more while the same outage lasts.
    clock.advance(_OUTAGE_S)
    deps.wake.set()  # Nothing else would start a tick: the only Warden is silent.
    await _wait_until(lambda: queen.liveness[warden_id].is_offline)
    assert _unreachable(queen) == [warden_id]
    clock.advance(10.0)
    deps.wake.set()
    await _wait_until(lambda: queen.liveness[warden_id].missed_heartbeats == 5)
    assert _unreachable(queen) == [warden_id]

    # Back with a fresh Heartbeat, then silent again: a second outage, a second Alarm.
    await warden.beat(clock)
    await _wait_until(lambda: not queen.liveness[warden_id].is_offline)
    assert len(_unreachable(queen)) == 1
    clock.advance(_OUTAGE_S)
    deps.wake.set()
    await _wait_until(lambda: queen.liveness[warden_id].is_offline)
    await _stop(queen, run_task)

    assert _unreachable(queen) == [warden_id, warden_id]
    assert len(await _alarm_lines(deps)) == 2


async def test_a_backlog_of_stale_heartbeats_after_an_outage_raises_no_second_alarm() -> None:
    clock = FakeClock()
    deps, _link, _end = make_queen_deps(clock)
    warden = _pair(deps)
    warden_id = warden.link.warden_id
    queen = Queen(deps)
    await queen.attach_warden(warden.link)
    await warden.beat(clock)
    start = clock.now()
    run_task = asyncio.ensure_future(queen.run())
    await _wait_until(lambda: _handled(queen, warden_id, 2_048))
    clock.advance(20.0)
    deps.wake.set()
    await _wait_until(lambda: queen.liveness[warden_id].is_offline)

    # The outage's own Heartbeats, sent 1 to 5 s after the last one heard, arrive only now:
    # every one is already 15 s or more old, so none is proof of life today.
    late = FakeClock(start=start)
    for beat in range(1, 6):
        late.advance(1.0)
        await warden.beat(late, tokens_used=beat)
    await _wait_until(lambda: _handled(queen, warden_id, 5))
    await _stop(queen, run_task)

    assert _unreachable(queen) == [warden_id]
    assert len(await _alarm_lines(deps)) == 1
    current = queen.liveness[warden_id]
    assert current.is_offline
    assert current.last_heartbeat_at == start + timedelta(seconds=5)


async def test_a_flooding_warden_never_starves_another_wardens_heartbeat() -> None:
    clock = FakeClock()
    deps, _link, _end = make_queen_deps(clock)
    flooder, quiet = _pair(deps), _pair(deps)
    queen = Queen(deps)
    await queen.attach_warden(flooder.link)
    await queen.attach_warden(quiet.link)
    # More than one queue's worth from the flooder, one Heartbeat from the quiet Warden. Each a
    # little newer than the last, so the Attendant orders them oldest first, as sent.
    for beat in range(1, LINK_QUEUE_SIZE + 45):
        clock.advance(0.01)
        await flooder.beat(clock, tokens_used=beat)
    await quiet.beat(clock, tokens_used=1)
    await _wait_until(lambda: queen._links.queued(flooder.link.warden_id) == LINK_QUEUE_SIZE)
    await _wait_until(lambda: queen._links.queued(quiet.link.warden_id) == 1)

    await queen._tick()

    # One tick: the quiet Warden is heard, and the flooder gave exactly one queue's worth.
    assert await _handled(queen, quiet.link.warden_id, 1)
    assert await _handled(queen, flooder.link.warden_id, LINK_QUEUE_SIZE)
    await queen.stop()


async def test_detach_and_stop_leave_no_reader_task_pending() -> None:
    clock = FakeClock()
    deps, _link, _end = make_queen_deps(clock)
    first, second = _pair(deps), _pair(deps)
    queen = Queen(deps)
    before = asyncio.all_tasks()
    await queen.attach_warden(first.link)
    await queen.attach_warden(second.link)
    run_task = asyncio.ensure_future(queen.run())
    await second.beat(clock)
    await _wait_until(lambda: _handled(queen, second.link.warden_id, 2_048))
    assert _readers() == 2

    await detach_warden(queen, second.link.warden_id)
    assert _readers() == 1
    await _stop(queen, run_task)

    assert _readers() == 0
    assert asyncio.all_tasks() == before
