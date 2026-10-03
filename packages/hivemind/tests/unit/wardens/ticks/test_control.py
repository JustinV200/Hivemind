"""Tests for hivemind.wardens.ticks.control: handle_stop and send_intervention.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/control.py (codingrules section 3). `forward_control` and
    `handle_ceilings_set`/`handle_plan_written` are already exercised end to end through
    `hivemind.wardens.warden.Warden`'s own tick (test_warden_forwarding.py); this module covers
    `handle_stop` (roadmap step 5.3 / ADR-0027), `send_intervention`, `Warden.intervene`'s own
    delegate, `handle_release_lease` (roadmap step 5.13, and the Overwintering scrub's Warden half,
    phase 5 open item 8) and `handle_snapshot_reply` (open item 7).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.control for the module under test.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from builders.cells import make_cell
from builders.hosting import make_hosting_warden
from builders.wardens import QueenEnd, make_warden_deps

from hivemind.cell import CellKind
from hivemind.forage import Ceilings
from hivemind.supervision import Cancel
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens.autopilot import WardenAction
from hivemind.wardens.errors import UnknownSubBeeError
from hivemind.wardens.inbox import to_inbox_item
from hivemind.wardens.snapshot_relay import RelaySnapshotter
from hivemind.wardens.state import WardenState
from hivemind.wardens.ticks.control import (
    handle_release_lease,
    handle_snapshot_reply,
    handle_source_evict,
    handle_stop,
    send_intervention,
)
from hivemind.wardens.ticks.dispatch import act
from hivemind.wardens.trail_sync import TrailSyncDeps, WaggleTrailSync
from hivemind.wardens.warden import Warden
from waggle.clock import FakeClock
from waggle.envelope import Hop, wrap
from waggle.ids import (
    MessageId,
    new_cell_id,
    new_hive_id,
    new_message_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.base import WaggleMessage
from waggle.messages.cell.leases import CellTeardownRequest, LeaseReleased
from waggle.messages.cell.snapshot import CellSnapshotReply, CellSnapshotRequest
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.control.protocol import Shutdown
from waggle.messages.forage import SourceEvict, SourceRemovalCause
from waggle.messages.labels import Urgency
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.swarm import TrailSegmentSync

_CLOCK = FakeClock()


def _release_lease_intervene(reason: str = "operator asked") -> Intervene:
    return Intervene(
        action=InterventionAction.RELEASE_LEASE,
        subject=None,
        task_id=None,
        slot=None,
        binding=None,
        alarm_id=None,
        reason=reason,
    )


@pytest.mark.parametrize(
    "payload",
    [
        Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown"),
        CellTeardownRequest(
            cell_id=new_cell_id(_CLOCK),
            lease_id=None,
            urgency=Urgency.IMMEDIATE,
            cause=ReleaseCause.COMPLETED,
            reason="test teardown",
        ),
    ],
)
async def test_handle_stop_stops_the_warden(payload: object) -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    await handle_stop(warden, payload)
    await asyncio.wait_for(run_task, timeout=5.0)

    assert warden.state is WardenState.STOPPED


async def test_send_intervention_raises_for_an_unknown_sub_bee() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()

    with pytest.raises(UnknownSubBeeError):
        await send_intervention(warden, "no-such-worker", Cancel(reason="test"))

    await warden.stop()


def _ordered(intervene: Intervene) -> InboxItem:
    """Wrap the Queen's `intervene` as the InboxItem the Warden's tick drains it as."""
    hop = Hop(
        sender=new_hive_id(_CLOCK), recipient=new_warden_id(_CLOCK), node_id=new_node_id(_CLOCK)
    )
    return to_inbox_item(wrap(intervene, hop, clock=_CLOCK), "queen")


async def test_handle_release_lease_releases_the_lease_and_answers_the_order() -> None:
    """Roadmap step 5.13: the Warden releases its own lease and tells the Queen, naming her order.

    Phase 5 open item 8: the answer's envelope names the order's in `correlation_id`, which is
    how the Overwintering scrub tells its own answer from any other.
    """
    deps, queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()
    lease = warden.lease
    assert lease is not None
    lease_id = lease.id
    order = _release_lease_intervene("operator asked")
    item = _ordered(order)

    await handle_release_lease(warden, item, order)

    assert warden.lease is None
    answer = await queen_end.wait_for_envelope(LeaseReleased)
    released = answer.payload
    assert isinstance(released, LeaseReleased)
    assert released.lease_id == lease_id
    assert released.reason == "operator asked"
    assert answer.correlation_id == item.id


async def test_handle_release_lease_closes_the_released_leases_session() -> None:
    """The session worked for the lease: nothing it started outlives it, and none is reused."""
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()
    session = warden._session
    assert session is not None and session.is_open
    order = _release_lease_intervene()

    await handle_release_lease(warden, _ordered(order), order)

    assert warden._session is None
    assert not session.is_open


async def test_handle_release_lease_ships_the_cells_trail_before_it_answers() -> None:
    """The Queen may pause the Cell on the answer: its trail, cell.released included, goes first."""
    deps, queen_end, warden_id = make_warden_deps()
    sync = WaggleTrailSync(
        TrailSyncDeps(
            trail=deps.trail,
            transport=deps.queen_link,
            node_id=deps.identity.node_id,
            cell_id=new_cell_id(_CLOCK),
            warden_id=warden_id,
            hive_id=new_hive_id(_CLOCK),
            clock=deps.clock,
        )
    )
    warden = Warden(warden_id, dataclasses.replace(deps, trail_sync=sync))
    await warden.start()
    order = _release_lease_intervene()

    await handle_release_lease(warden, _ordered(order), order)

    await queen_end.wait_for_envelope(LeaseReleased)
    sent = [type(envelope.payload) for envelope in queen_end.envelopes]
    assert TrailSegmentSync in sent[: sent.index(LeaseReleased)]


async def test_handle_release_lease_is_idempotent_with_no_lease_held() -> None:
    """A Warden already lease-less (WATCH, a refused lease) has nothing to release or report."""
    deps, queen_end, warden_id = make_warden_deps(cells=())
    warden = Warden(warden_id, deps)
    await warden.start()
    assert warden.lease is None
    assert warden.state is WardenState.WATCH
    order = _release_lease_intervene()

    await handle_release_lease(warden, _ordered(order), order)  # Must not raise.

    assert warden.lease is None
    assert queen_end.lease_released == []


def _from_the_queen(payload: WaggleMessage, answers: MessageId) -> InboxItem:
    """Wrap `payload` as the Queen's reply to `answers`, as the Warden's tick drains it."""
    hop = Hop(
        sender=new_hive_id(_CLOCK), recipient=new_warden_id(_CLOCK), node_id=new_node_id(_CLOCK)
    )
    return to_inbox_item(wrap(payload, hop, clock=_CLOCK, correlation_id=answers), "queen")


async def test_handle_snapshot_reply_is_a_noop_for_the_default_noop_snapshotter() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    reply = CellSnapshotReply(cell_id=new_cell_id(_CLOCK), snapshot_id="snap_x", error=None)
    item = _from_the_queen(reply, new_message_id(_CLOCK))

    handle_snapshot_reply(warden, item, reply)  # Must not raise: NoopSnapshotter relays nothing.


async def test_handle_snapshot_reply_resolves_the_ask_its_envelope_names() -> None:
    """The end-to-end round trip: a RelaySnapshotter's own snapshot() resumes once this fires."""
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    deps, queen_end, warden_id = make_warden_deps(clock=clock)
    relay = RelaySnapshotter(cell_id, deps.queen_link, deps.hop, clock)
    warden = Warden(warden_id, dataclasses.replace(deps, snapshotter=relay))
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock, id=cell_id)
    ask_task = asyncio.ensure_future(relay.snapshot(cell))
    request = await queen_end.wait_for_envelope(CellSnapshotRequest)
    reply = CellSnapshotReply(cell_id=cell_id, snapshot_id="snap_new", error=None)

    # The InboxItem carries the reply's correlation_id from its envelope (roadmap step 7.8).
    handle_snapshot_reply(warden, _from_the_queen(reply, request.id), reply)

    assert await asyncio.wait_for(ask_task, timeout=5.0) == "snap_new"


# ──────────────────────────────────────────────────────────────────────────────
# handle_source_evict (roadmap step 8.3): the Queen reclaims seats she exported
# ──────────────────────────────────────────────────────────────────────────────


def _ceilings(exportable_seats: int) -> Ceilings:
    return Ceilings(
        max_sub_bees=2,
        model_vram_bytes=0,
        model_disk_bytes=0,
        loadable_sources=(),
        exportable_seats=exportable_seats,
    )


async def _hosting_warden(
    exportable_seats: int | None,
) -> tuple[Warden, SourceEvict, QueenEnd]:
    """A started hosting Warden with the given ceilings, and an eviction of its `small` source."""
    warden, _deps, queen_end, forage_map, _door = make_hosting_warden()
    await warden.start()
    if exportable_seats is not None:
        warden._ceilings = _ceilings(exportable_seats)
    assert warden._cell is not None
    source = next(s for s in forage_map.sources() if s.source_id == "small")
    order = SourceEvict(
        cell_id=warden._cell.id,
        source=source.source_ref(),
        seats_to_reclaim=1,
        reason="The Queen needs the seats.",
    )
    return warden, order, queen_end


async def test_handle_source_evict_withdraws_a_source_whose_seats_were_exported() -> None:
    warden, order, _queen_end = await _hosting_warden(exportable_seats=2)

    await handle_source_evict(warden, order)

    assert warden._models is not None and warden._models.server_of("small") is None
    assert warden._models.server_of("large") == "box"
    await warden.stop()


async def test_handle_source_evict_reports_the_removal_as_evicted() -> None:
    warden, order, queen_end = await _hosting_warden(exportable_seats=2)

    await handle_source_evict(warden, order)

    await queen_end.pump_until(lambda: bool(queen_end.source_removed))
    assert queen_end.source_removed[0].cause is SourceRemovalCause.EVICTED
    assert queen_end.source_removed[0].source.source_id == "small"
    await warden.stop()


async def test_handle_source_evict_ignores_an_order_when_the_queen_set_no_ceilings() -> None:
    warden, order, _queen_end = await _hosting_warden(exportable_seats=None)

    await handle_source_evict(warden, order)

    assert warden._models is not None and warden._models.server_of("small") == "box"
    await warden.stop()


async def test_handle_source_evict_ignores_an_order_when_no_seats_were_exported() -> None:
    warden, order, _queen_end = await _hosting_warden(exportable_seats=0)

    await handle_source_evict(warden, order)

    assert warden._models is not None and warden._models.server_of("small") == "box"
    await warden.stop()


async def test_handle_source_evict_ignores_an_order_naming_another_cell() -> None:
    warden, order, _queen_end = await _hosting_warden(exportable_seats=2)
    elsewhere = order.model_copy(update={"cell_id": new_cell_id(_CLOCK)})

    await handle_source_evict(warden, elsewhere)

    assert warden._models is not None and warden._models.server_of("small") == "box"
    await warden.stop()


async def test_handle_source_evict_ignores_a_source_this_warden_never_loaded() -> None:
    warden, order, _queen_end = await _hosting_warden(exportable_seats=2)
    stranger = order.source.model_copy(update={"source_id": "stranger"})

    await handle_source_evict(warden, order.model_copy(update={"source": stranger}))

    assert warden._models is not None and set(warden._models.running) == {"box"}
    await warden.stop()


async def test_handle_release_lease_stops_the_model_servers_with_the_lease() -> None:
    warden, _deps, queen_end, forage_map, _door = make_hosting_warden()
    await warden.start()

    order = _release_lease_intervene()
    await handle_release_lease(warden, _ordered(order), order)

    assert forage_map.sources() == ()
    assert warden._models is not None and warden._models.running == {}
    await queen_end.pump_until(lambda: len(queen_end.source_removed) == 2)
    assert {m.cause for m in queen_end.source_removed} == {SourceRemovalCause.LEASE_RELEASED}
async def test_a_snapshot_reply_drained_by_the_tick_reaches_the_ask_its_envelope_names() -> None:
    """Through the Warden's own dispatch (ticks.dispatch.act), not the handler alone."""
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    deps, queen_end, warden_id = make_warden_deps(clock=clock)
    relay = RelaySnapshotter(cell_id, deps.queen_link, deps.hop, clock)
    warden = Warden(warden_id, dataclasses.replace(deps, snapshotter=relay))
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock, id=cell_id)
    ask_task = asyncio.ensure_future(relay.snapshot(cell))
    request = await queen_end.wait_for_envelope(CellSnapshotRequest)
    reply = CellSnapshotReply(cell_id=cell_id, snapshot_id="snap_tick", error=None)

    await act(warden, WardenAction.RECORD, _from_the_queen(reply, request.id), None, None)

    assert await asyncio.wait_for(ask_task, timeout=5.0) == "snap_tick"
