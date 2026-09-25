"""Tests for hivemind.cli.honey.sources: the memory-backed wax and Bee Bread sources.

Fits into the Hive:
    Mirrors src/hivemind/cli/honey/sources.py (codingrules section 3). Runs over the real
    in-memory MemoryStore, written through the memory tier's own write paths.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.honey.sources for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from hivemind.cell import HoneyClearance
from hivemind.cli.honey.sources import MemoryBeeBreadSource, MemoryWaxSource
from hivemind.memory import (
    InMemoryMemoryStore,
    MemoryContext,
    MemoryIdentity,
    WaxProposalInput,
    WaxSeverity,
    deposit_transcript,
    propose_wax,
    write_wax,
)
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_hive_id, new_node_id, new_task_id
from waggle.messages.cell.wax import WaxDecision, WaxOrigin


@pytest.fixture
def clock() -> FakeClock:
    """A fresh FakeClock for one test."""
    return FakeClock()


@pytest.fixture
def ctx(clock: FakeClock) -> MemoryContext:
    """A fresh in-memory memory store on `clock`."""
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return MemoryContext(
        store=InMemoryMemoryStore(MemoryPheromoneTrail(clock)), identity=identity, clock=clock
    )


async def _wax(ctx: MemoryContext, text: str, clearance: HoneyClearance, written: bool) -> CellId:
    """Propose one note about a fresh Cell, writing it when `written`; return the Cell id."""
    cell = new_cell_id(ctx.clock)
    inputs = WaxProposalInput(
        cell_id=cell,
        severity=WaxSeverity.NOTE,
        text=text,
        reason="r",
        clearance=clearance,
        origin=WaxOrigin.HUMAN,
    )
    wax = await propose_wax(inputs, 4_000, ctx)
    if written:
        await write_wax(wax, WaxDecision.AWAKE, "ok", ctx)
    return cell


async def test_memory_wax_source_returns_written_notes_within_the_allowance(
    ctx: MemoryContext,
) -> None:
    written = await _wax(ctx, "Written.", HoneyClearance.C1, written=True)
    proposed = await _wax(ctx, "Only proposed.", HoneyClearance.C1, written=False)
    royal = await _wax(ctx, "Royal.", HoneyClearance.C2, written=True)
    source = MemoryWaxSource(ctx.store)

    (note,) = await source.live_wax(written, HoneyClearance.C1)
    everywhere = await source.live_wax(None, HoneyClearance.C1)

    assert (note.cell_id, note.text) == (written, "Written.")
    assert await source.live_wax(proposed, HoneyClearance.C2) == ()
    assert await source.live_wax(royal, HoneyClearance.C1) == ()
    assert [item.cell_id for item in everywhere] == [written]


async def test_memory_bee_bread_source_serves_recent_entries_and_lookups(
    ctx: MemoryContext, clock: FakeClock
) -> None:
    task = new_task_id(ctx.clock)
    first = await deposit_transcript("first", task, HoneyClearance.C1, ctx)
    clock.advance(1)
    second = await deposit_transcript("second", None, HoneyClearance.C1, ctx)
    royal = await deposit_transcript("royal", None, HoneyClearance.C2, ctx)
    source = MemoryBeeBreadSource(ctx.store)
    now = ctx.clock.now()

    recent = await source.recent(now - timedelta(days=1), now, HoneyClearance.C1, 10)
    newest = await source.recent(now - timedelta(days=1), now, HoneyClearance.C1, 1)

    assert [note.id for note in recent] == [second.id, first.id]
    assert [note.id for note in newest] == [second.id]
    found = await source.entry(first.id, HoneyClearance.C1)
    assert found is not None and found.task_id == task
    assert await source.entry(royal.id, HoneyClearance.C1) is None
    assert await source.entry("event_01ARZ3NDEKTSV4RRFFQ69G5FAV", HoneyClearance.C2) is None
