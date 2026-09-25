"""Test hivemind.entrance.reads.census: each Cell and Warden, joined from every place that knows it.

Over the Queen's own in-memory stores, a stand-in for her attached Wardens and one for the Virtual
Cell lifecycle's table: a Virtual Cell her attached Warden runs on is listed once, with its
lifecycle status; one the lifecycle reconciled after a restart, with no Cell record and no Warden
yet, is listed from its row alone (image, backend, tier; always FULL access); the newest lease and
mode edges on the trail decide a Cell's lease and mode; an unknown Cell is refused; and a Warden
the Queen judged offline reads OFFLINE whatever its last Heartbeat said.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/census.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest
from builders.cells import make_cell
from builders.queen import make_queen_deps
from builders.supervision import make_telemetry

from hivemind.cell import AccessLevel, CellKind, CombShieldLevel, LeaseState
from hivemind.entrance.errors import CellNotFoundError
from hivemind.entrance.gate import HiveReads, LlmReads
from hivemind.entrance.reads import cell_view, cell_views, warden_views
from hivemind.entrance.streams import TelemetryBoard
from hivemind.hive import LiveVirtualCell, VirtualCellStatus
from hivemind.observation import CellMode
from hivemind.pheromone import event_class_for
from hivemind.queen import QueenDeps, WardenLink, WardenLiveness
from waggle.clock import FakeClock
from waggle.ids import WardenId, new_cell_id, new_event_id
from waggle.messages.supervision import Heartbeat, WardenState

_IMAGE, _BACKEND = "hive/worker:1", "docker"  # A Virtual Cell's image and backend, by name.


@dataclass(frozen=True, slots=True)
class _Census:
    """The Queen's attached Wardens and their pulse, as the census reads them."""

    wardens: tuple[WardenLink, ...]
    liveness: Mapping[WardenId, WardenLiveness] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Lifecycle:
    """The Virtual Cell lifecycle's live table."""

    rows: tuple[LiveVirtualCell, ...]

    def live_cells(self) -> tuple[LiveVirtualCell, ...]:
        """Every row, as the lifecycle lists them."""
        return self.rows


def _reads(
    deps: QueenDeps, census: _Census, lifecycle: _Lifecycle | None = None
) -> tuple[HiveReads, TelemetryBoard]:
    """What the Entrance reads, over the Queen's stores; the telemetry board beside it."""
    board = TelemetryBoard()
    llm = LlmReads({}, deps.bindings, deps.cluster_state, deps.health_poller)
    reads = HiveReads(
        goal_requests=deps.goal_requests,
        chat=deps.chat,
        chamber=deps.chamber,
        trail=deps.trail,
        memory=deps.memory,
        ledger=deps.ledger,
        census=census,
        telemetry=board,
        llm=llm,
        virtual_cells=lifecycle,
    )
    return reads, board


async def _edge(deps: QueenDeps, clock: FakeClock, kind: str, subject_id: str) -> None:
    """Record one edge on the trail, a second after the one before."""
    clock.advance(1.0)
    identity = deps.identity
    event = event_class_for(kind)(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload={},
    )
    await deps.trail.record(event)


async def test_virtual_cells_are_listed_once_and_from_their_row_when_unrecorded() -> None:
    clock = FakeClock()
    cell = make_cell(CellKind.VIRTUAL, clock)
    deps, link, _ = make_queen_deps(clock, cell=cell)
    attached = LiveVirtualCell(
        cell_id=cell.id,
        status=VirtualCellStatus.GRANTED,
        backend=_BACKEND,
        image=_IMAGE,
        comb_shield=cell.comb_shield,
        cell=cell,
        warden_id=link.warden_id,
    )
    reconciled = LiveVirtualCell(
        cell_id=new_cell_id(clock),
        status=VirtualCellStatus.DORMANT,
        backend=_BACKEND,
        image=_IMAGE,
        comb_shield=CombShieldLevel.PROPOLIS,
    )
    reads, _ = _reads(deps, _Census((link,)), _Lifecycle((attached, reconciled)))

    views = await cell_views(reads)

    assert [view.id for view in views] == [cell.id, reconciled.cell_id]
    first, second = views
    assert (first.kind, first.virtual_status, first.warden_id) == (
        CellKind.VIRTUAL,
        VirtualCellStatus.GRANTED,
        link.warden_id,
    )
    assert (second.name, second.source, second.kind) == (_IMAGE, _BACKEND, CellKind.VIRTUAL)
    assert (second.access_level, second.comb_shield) == (AccessLevel.FULL, CombShieldLevel.PROPOLIS)
    assert (second.warden_id, second.virtual_status) == (None, VirtualCellStatus.DORMANT)


async def test_the_newest_lease_and_mode_edges_decide() -> None:
    clock = FakeClock()
    deps, link, _ = make_queen_deps(clock)
    reads, _ = _reads(deps, _Census((link,)))
    await _edge(deps, clock, "cell.leased", link.cell.id)
    await _edge(deps, clock, "warden.watch", link.warden_id)
    leased = await cell_view(reads, link.cell.id)
    await _edge(deps, clock, "warden.active", link.warden_id)
    await _edge(deps, clock, "warden.reconnected", link.warden_id)  # Not a mode edge.
    await _edge(deps, clock, "cell.released", link.cell.id)

    released = await cell_view(reads, link.cell.id)

    assert (leased.lease_state, leased.mode) == (LeaseState.OPEN, CellMode.WATCH)
    assert (released.lease_state, released.mode) == (LeaseState.RELEASED, CellMode.ACTIVE)
    assert released.last_event == "cell.released"


async def test_an_unknown_cell_is_refused() -> None:
    deps, link, _ = make_queen_deps(FakeClock())
    reads, _ = _reads(deps, _Census((link,)))

    with pytest.raises(CellNotFoundError):
        await cell_view(reads, new_cell_id(FakeClock()))


def test_a_warden_the_queen_judged_offline_reads_offline_whatever_it_said() -> None:
    clock = FakeClock()
    deps, link, _ = make_queen_deps(clock)
    pulse = WardenLiveness(last_heartbeat_at=clock.now(), missed_heartbeats=3, is_offline=True)
    reads, board = _reads(deps, _Census((link,), {link.warden_id: pulse}))
    heartbeat = Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )
    board.record(link.warden_id, heartbeat, clock.now())

    [view] = warden_views(reads)

    assert (view.state, view.offline, view.missed_heartbeats) == (WardenState.OFFLINE, True, 3)
    assert (view.cell_id, view.sub_bees, view.live_grants) == (link.cell.id, 0, 0)
