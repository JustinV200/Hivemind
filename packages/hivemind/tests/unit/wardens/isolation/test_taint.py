"""Tests for the in-Cell isolation setter: a Warden taints its own store on the Queen's order.

Roadmap step 10.6a (ADR-0035). The Queen isolating a Cell sends its Warden a `CellTaintOrder`; the
Warden labels the memory its own store holds (here, a Handoff checkpointed for its task) TAINTED
from the order's instant on, with `TaintSource.ISOLATION` and the Queen's `cell.isolated` event as
the cause, and the loader every resume goes through refuses it from then on. Memory written
before that instant is left alone, an order for another Cell or from a sub-bee labels nothing,
and a repeated order changes nothing.

Fits into the Hive:
    Mirrors src/hivemind/wardens/isolation/taint.py (codingrules section 3), over a real Warden
    running one held sub-bee (builders.quarantine's scene).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.isolation.taint, under test.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import pytest
from builders.memory import make_handoff
from builders.quarantine import (
    QuarantineScene,
    drain,
    start_scene,
    stop_scene,
    wait_for_event,
)

from hivemind.cell import HoneyClearance
from hivemind.memory import MemoryContext, TaintedMemoryError, read_handoff, write_checkpoint
from hivemind.pheromone import TrailQuery
from hivemind.wardens import warden as warden_module
from hivemind.wardens.inbox import to_inbox_item
from waggle.clock import FakeClock
from waggle.envelope import Hop, wrap
from waggle.ids import CellId, new_cell_id, new_event_id
from waggle.messages import HandoffRef
from waggle.messages.cell import CellTaintOrder

_TAINTED = "memory.tainted"  # The trail kind every label writes.


def _order(
    scene: QuarantineScene, suspect_at: datetime, cell_id: CellId | None = None
) -> CellTaintOrder:
    """The Queen's order for the scene's Cell (or `cell_id`): its Warden and its one task."""
    cell = scene.warden._cell
    assert cell is not None
    return CellTaintOrder(
        cell_id=cell_id if cell_id is not None else cell.id,
        cause_event_id=new_event_id(scene.deps.clock),
        suspect_at=suspect_at,
        authors=(scene.warden._warden_id,),
        task_ids=(scene.assignment.task_id,),
        reason="Cell isolated (queen) on a Guard report.",
    )


async def _checkpoint(scene: QuarantineScene) -> HandoffRef:
    """Checkpoint a Handoff for the scene's task into the Warden's own store, now."""
    deps = scene.deps
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    task = scene.assignment.task_id
    return await write_checkpoint(make_handoff(task_id=task), task, ctx)


async def _tainted(scene: QuarantineScene) -> list[str]:
    """The item id of every `memory.tainted` row on the Warden's own trail."""
    return [event.subject_id for event in await scene.deps.trail.query(TrailQuery(kind=_TAINTED))]


async def test_the_queens_order_taints_the_handoffs_the_cell_holds_from_its_instant() -> None:
    scene = await start_scene()
    suspect_at = scene.deps.clock.now()
    _fake(scene).advance(1.0)
    ref = await _checkpoint(scene)
    order = _order(scene, suspect_at)

    await scene.queen.send(order)
    event = await wait_for_event(scene.deps.trail, _TAINTED, ref.event_id)

    assert (event.payload["source"], event.payload["cause_event_id"]) == (
        "isolation",
        order.cause_event_id,
    )
    # The loader every resume goes through refuses it now, whatever the clearance asked.
    with pytest.raises(TaintedMemoryError):
        await read_handoff(scene.deps.memory, ref, HoneyClearance.C2)
    await stop_scene(scene)


async def test_memory_written_before_the_orders_instant_is_left_alone() -> None:
    scene = await start_scene()
    before = await _checkpoint(scene)
    _fake(scene).advance(1.0)
    suspect_at = scene.deps.clock.now()
    after = await _checkpoint(scene)

    await scene.queen.send(_order(scene, suspect_at))
    await wait_for_event(scene.deps.trail, _TAINTED, after.event_id)
    await drain()

    # The later Handoff (and its checkpoint deposit) is labelled; the earlier one is not.
    tainted = await _tainted(scene)
    assert after.event_id in tainted and before.event_id not in tainted
    await read_handoff(scene.deps.memory, before, HoneyClearance.C2)  # Still readable.
    await stop_scene(scene)


async def test_an_order_for_another_cell_labels_nothing() -> None:
    scene = await start_scene()
    suspect_at = scene.deps.clock.now()
    await _checkpoint(scene)

    await scene.queen.send(_order(scene, suspect_at, cell_id=new_cell_id(scene.deps.clock)))
    await drain()

    assert await _tainted(scene) == []
    await stop_scene(scene)


async def test_an_order_from_anyone_but_the_queen_labels_nothing() -> None:
    scene = await start_scene()
    suspect_at = scene.deps.clock.now()
    await _checkpoint(scene)
    bee = scene.warden.sub_bees[0].worker_id
    hop = Hop(sender=bee, recipient=scene.warden._warden_id, node_id=scene.deps.identity.node_id)
    envelope = wrap(_order(scene, suspect_at), hop, clock=scene.deps.clock)

    # As if its own sub-bee had sent it over its link: only the Queen isolates.
    await warden_module._handle_item(scene.warden, to_inbox_item(envelope, principal=bee))

    assert await _tainted(scene) == []
    await stop_scene(scene)


async def test_a_repeated_order_changes_nothing() -> None:
    scene = await start_scene()
    suspect_at = scene.deps.clock.now()
    ref = await _checkpoint(scene)
    order = _order(scene, suspect_at)

    await scene.queen.send(order)
    await wait_for_event(scene.deps.trail, _TAINTED, ref.event_id)
    await drain()  # Every other label of the same order lands in the same handling.
    first = await _tainted(scene)
    await scene.queen.send(order)  # The Queen resends it when the Warden's link comes back.
    await drain()

    assert await _tainted(scene) == first
    await stop_scene(scene)


def _fake(scene: QuarantineScene) -> FakeClock:
    """The scene's clock, which is always a FakeClock (builders.quarantine.start_scene)."""
    return cast(FakeClock, scene.deps.clock)
