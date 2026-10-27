"""Unit tests for hivemind.queen.cell_gate.scrub: make_scrub and ScrubAcks.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/cell_gate/scrub.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

Every scrub here waits on a FakeClock: an answer is handed over through `ScrubAcks.claim`, the way
the Cell's listener hands it over, and a timeout is the test advancing the clock, never a timer.

See Also:
    - hivemind.queen.cell_gate.scrub for the module under test.
    - tests/e2e/test_virtual_cells.py for the scrub over a real in-Cell Warden and its link.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from builders.cells import make_cell

from hivemind.cell import Cell, CellKind
from hivemind.hive import CellScrubError
from hivemind.pheromone import TrailQuery, TrailRecorder
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.scrub import SCRUB_FAILED_KIND, ScrubAcks, ScrubTerms, make_scrub
from hivemind.queen.deps import WardenLink
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import new_hive_id, new_lease_id, new_node_id, new_warden_id
from waggle.messages.cell import LeaseReleased, ReleaseCause
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.transport.memory import MemoryTransport

_TIMEOUT_S = 10.0  # The scrub's own bound in these tests; only the FakeClock ever reaches it.
_WAIT_S = 2.0  # Bounds every real wait here, so a broken scrub fails fast instead of hanging.
_ROUNDS = 20  # Advances `_run_out_the_wait` makes at most; a scrub gives up within a few.


@dataclass
class _StubQueen:
    """A `.wardens` view a test sets directly, standing in for `hivemind.queen.queen.Queen`."""

    wardens: tuple[WardenLink, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class _Scene:
    """One attached Virtual Cell, the Warden's end of its link, and the scrub under test."""

    cell: Cell
    link: WardenLink
    warden_end: MemoryTransport
    acks: ScrubAcks
    trail: MemoryPheromoneTrail
    clock: FakeClock


def _scene() -> _Scene:
    """Attach one Virtual Cell's Warden to a stub Queen, over an in-memory pair."""
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(sender=new_hive_id(clock), recipient=new_warden_id(clock), node_id=new_node_id(clock))
    link = WardenLink(warden_id=new_warden_id(clock), cell=cell, transport=queen_end, hop=hop)
    trail = MemoryPheromoneTrail(clock)
    return _Scene(cell, link, warden_end, ScrubAcks(), trail, clock)


def _scrub_of(scene: _Scene, queen: _StubQueen | None) -> asyncio.Task[None]:
    """Start scrubbing `scene.cell` as the Overwintering pool would, against `queen`."""
    recorder = TrailRecorder(
        trail=scene.trail,
        clock=scene.clock,
        hive_id=new_hive_id(scene.clock),
        node_id=new_node_id(scene.clock),
    )
    scrub = make_scrub(
        lambda: queen, scene.acks, scene.clock, recorder, ScrubTerms(timeout_s=_TIMEOUT_S)
    )
    return asyncio.ensure_future(scrub(scene.cell))


async def _order(scene: _Scene) -> Envelope:
    """Read the one order the scrub sent its Warden, bounded by `_WAIT_S`."""
    return await asyncio.wait_for(anext(scene.warden_end.receive()), timeout=_WAIT_S)


def _answer(scene: _Scene, order: Envelope, *, restored: bool = True) -> Envelope:
    """The Warden's LeaseReleased for `order`, correlated to it as the Warden sends it."""
    released = LeaseReleased(
        lease_id=new_lease_id(scene.clock),
        cell_id=scene.cell.id,
        holder=scene.link.warden_id,
        cause=ReleaseCause.COMPLETED,
        is_restored=restored,
        killed_processes=0,
        residual_paths=() if restored else ("/var/lib/hivemind/scratch/lease_x",),
        reason="scrubbed",
    )
    hop = Hop(
        sender=scene.link.warden_id,
        recipient=scene.link.hop.sender,
        node_id=new_node_id(scene.clock),
    )
    return wrap(released, hop, clock=scene.clock, correlation_id=order.id)


async def _failures(scene: _Scene) -> list[str]:
    """Every `cell.scrub_failed` reason on the scene's trail, in order."""
    events = await scene.trail.query(TrailQuery(kind=SCRUB_FAILED_KIND))
    return [str(event.payload["reason"]) for event in events if event.subject_id == scene.cell.id]


async def _run_out_the_wait(task: asyncio.Task[None], clock: FakeClock) -> None:
    """Advance `clock` past the scrub's bound, yielding, until `task` gives up waiting.

    The wait starts its timer only once its order is sent, so a single `advance` made the moment
    the order is read can land before the timer exists; each round here advances again, never
    sleeping for real (codingrules 14.5).
    """
    for _ in range(_ROUNDS):
        if task.done():
            return
        clock.advance(_TIMEOUT_S)
        await asyncio.sleep(0)  # Let the woken wait notice its timer and return.


async def test_a_scrub_orders_its_warden_to_release_and_returns_once_it_confirms() -> None:
    scene = _scene()
    task = _scrub_of(scene, _StubQueen(wardens=(scene.link,)))

    order = await _order(scene)
    claimed = scene.acks.claim(scene.cell.id, _answer(scene, order))

    await asyncio.wait_for(task, timeout=_WAIT_S)  # Must not raise.
    intervene = order.payload
    assert isinstance(intervene, Intervene)
    assert intervene.action is InterventionAction.RELEASE_LEASE
    assert intervene.subject is None  # The Warden itself, never one of its sub-bees.
    assert claimed is True
    assert await _failures(scene) == []


async def test_a_scrub_with_no_answer_in_time_fails_and_records_why() -> None:
    scene = _scene()
    task = _scrub_of(scene, _StubQueen(wardens=(scene.link,)))
    order = await _order(scene)

    await _run_out_the_wait(task, scene.clock)

    with pytest.raises(CellScrubError, match="did not confirm"):
        await asyncio.wait_for(task, timeout=_WAIT_S)
    assert len(await _failures(scene)) == 1
    # Its wait is gone, but its answer is still its own: taken and dropped if it comes now, so the
    # Queen's tick never reads it, and never from another Cell's link.
    other = make_cell(kind=CellKind.VIRTUAL, clock=scene.clock)
    assert scene.acks.claim(other.id, _answer(scene, order)) is False
    assert scene.acks.claim(scene.cell.id, _answer(scene, order)) is True


async def test_a_scrub_whose_warden_left_scratch_behind_fails() -> None:
    scene = _scene()
    task = _scrub_of(scene, _StubQueen(wardens=(scene.link,)))
    order = await _order(scene)

    scene.acks.claim(scene.cell.id, _answer(scene, order, restored=False))

    with pytest.raises(CellScrubError, match="could not remove 1 path"):
        await asyncio.wait_for(task, timeout=_WAIT_S)
    assert len(await _failures(scene)) == 1


@pytest.mark.parametrize("attached", [False, True])
async def test_a_scrub_fails_at_once_when_its_warden_cannot_be_reached(attached: bool) -> None:
    """Not attached (or no Queen bound yet), or attached over a link that has already closed."""
    scene = _scene()
    await scene.warden_end.close()
    queen = _StubQueen(wardens=(scene.link,)) if attached else None

    with pytest.raises(CellScrubError):
        await asyncio.wait_for(_scrub_of(scene, queen), timeout=_WAIT_S)

    assert len(await _failures(scene)) == 1


async def test_claim_never_takes_an_answer_from_another_cells_link() -> None:
    scene = _scene()
    other = make_cell(kind=CellKind.VIRTUAL, clock=scene.clock)
    task = _scrub_of(scene, _StubQueen(wardens=(scene.link,)))
    order = await _order(scene)

    taken = scene.acks.claim(other.id, _answer(scene, order))
    await _run_out_the_wait(task, scene.clock)

    assert taken is False
    with pytest.raises(CellScrubError):
        await asyncio.wait_for(task, timeout=_WAIT_S)


async def test_claim_ignores_an_uncorrelated_or_unrelated_frame() -> None:
    scene = _scene()
    order = wrap(
        Intervene(
            action=InterventionAction.RELEASE_LEASE,
            subject=None,
            task_id=None,
            slot=None,
            binding=None,
            alarm_id=None,
            reason="test",
        ),
        scene.link.hop,
        clock=scene.clock,
    )
    scene.acks.expect(order.id, scene.cell.id)
    uncorrelated = _answer(scene, order).model_copy(update={"correlation_id": None})

    assert scene.acks.claim(scene.cell.id, uncorrelated) is False
    assert scene.acks.claim(scene.cell.id, order) is False  # Not a LeaseReleased at all.
    assert scene.acks.claim(scene.cell.id, _answer(scene, order)) is True
