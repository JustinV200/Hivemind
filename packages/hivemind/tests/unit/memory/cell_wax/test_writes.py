"""Tests for hivemind.memory.cell_wax.writes: propose_wax through retire_wax_for_cell.

Fits into the Hive:
    Mirrors src/hivemind/memory/cell_wax/writes.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.cell_wax.writes for the module under test.
"""

from __future__ import annotations

import pytest
from builders.memory import make_wax_proposal_input

from hivemind.memory.cell_wax import (
    WaxState,
    clear_wax,
    expire_wax,
    propose_wax,
    reject_wax,
    retire_wax_for_cell,
    write_wax,
)
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.errors import InvalidWaxTransitionError, WaxTextTooLongError
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id
from waggle.messages.cell.wax import WaxClearCause, WaxDecision


def _ctx_and_trail(clock: FakeClock) -> tuple[MemoryContext, PheromoneTrail]:
    trail = MemoryPheromoneTrail(clock)
    store = InMemoryMemoryStore(trail)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return MemoryContext(store=store, identity=identity, clock=clock), trail


def _ctx(clock: FakeClock) -> MemoryContext:
    ctx, _trail = _ctx_and_trail(clock)
    return ctx


async def test_propose_wax_inserts_a_proposed_row_and_records_its_event() -> None:
    clock = FakeClock()
    ctx, trail = _ctx_and_trail(clock)

    wax = await propose_wax(make_wax_proposal_input(clock=clock), 4_000, ctx)

    assert wax.state is WaxState.PROPOSED
    events = await trail.query(TrailQuery(subject_id=wax.cell_id))
    assert [e.kind for e in events] == ["memory.wax_proposed"]


async def test_propose_wax_refuses_text_over_the_manifest_cap() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    inputs = make_wax_proposal_input(clock=clock, text="x" * 50)

    with pytest.raises(WaxTextTooLongError):
        await propose_wax(inputs, 10, ctx)


async def test_write_wax_moves_proposed_to_written() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    proposed = await propose_wax(make_wax_proposal_input(clock=clock), 4_000, ctx)

    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the cap.", ctx)

    assert written.state is WaxState.WRITTEN
    assert written.decided_by is WaxDecision.AUTOPILOT
    reloaded = await ctx.store.get_wax(written.id)
    assert reloaded == written


async def test_write_wax_on_an_already_written_note_raises() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    proposed = await propose_wax(make_wax_proposal_input(clock=clock), 4_000, ctx)
    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the cap.", ctx)

    with pytest.raises(InvalidWaxTransitionError):
        await write_wax(written, WaxDecision.AUTOPILOT, "Again.", ctx)


async def test_reject_wax_moves_proposed_to_rejected() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    proposed = await propose_wax(make_wax_proposal_input(clock=clock), 4_000, ctx)

    rejected = await reject_wax(proposed, "Not convincing.", ctx)

    assert rejected.state is WaxState.REJECTED


async def test_clear_wax_moves_written_to_cleared_with_cause() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    proposed = await propose_wax(make_wax_proposal_input(clock=clock), 4_000, ctx)
    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the cap.", ctx)

    cleared = await clear_wax(written, "Stale now.", ctx)

    assert cleared.state is WaxState.CLEARED
    assert cleared.clear_cause is WaxClearCause.CLEARED


async def test_expire_wax_moves_written_to_expired_with_expired_cause() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    proposed = await propose_wax(make_wax_proposal_input(clock=clock), 4_000, ctx)
    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the cap.", ctx)

    expired = await expire_wax(written, ctx)

    assert expired.state is WaxState.EXPIRED
    assert expired.clear_cause is WaxClearCause.EXPIRED


async def test_retire_wax_for_cell_clears_every_written_note_for_that_cell() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    inputs = make_wax_proposal_input(clock=clock)
    proposed = await propose_wax(inputs, 4_000, ctx)
    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the cap.", ctx)

    cleared = await retire_wax_for_cell(inputs.cell_id, ctx)

    assert [item.id for item in cleared] == [written.id]
    assert cleared[0].state is WaxState.CLEARED
    assert cleared[0].clear_cause is WaxClearCause.CELL_RETIRED


async def test_retire_wax_for_cell_is_empty_when_the_cell_has_no_written_wax() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    inputs = make_wax_proposal_input(clock=clock)

    cleared = await retire_wax_for_cell(inputs.cell_id, ctx)

    assert cleared == ()
