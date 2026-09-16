"""Tests for hivemind.memory.compact.run: compact's structured call, deposit and trail event.

Fits into the Hive:
    Mirrors src/hivemind/memory/compact/run.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.compact.run for the module under test.
"""

from __future__ import annotations

import json

import pytest
from builders.llm import make_bound, text_response
from builders.memory import make_bee_bread_entry, make_compaction_request, make_pin

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.llm import DirectCallGate, FakeLLMProvider
from hivemind.memory.bee_bread import MAX_REF_IDS
from hivemind.memory.bee_bread.entry import BeeBreadEntryKind
from hivemind.memory.compact.run import compact
from hivemind.memory.compact.schema import CompactionDeps
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.errors import (
    ClearanceError,
    EmptyCompactionError,
    SummaryOfSummaryError,
    TooManySourcesError,
)
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

# One scripted structured reply, shared by every test that does not care about its exact content.
_SCRIPTED_SUMMARY = "Two tasks were completed without incident."
_SCRIPTED_REPLY = {
    "summary": _SCRIPTED_SUMMARY,
    "key_facts": ["The deploy finished at 04:00 UTC."],
    "open_threads": ["Follow up on the flaky test."],
}


def _ctx_and_trail(clock: FakeClock) -> tuple[MemoryContext, PheromoneTrail]:
    """Build a MemoryContext plus the same trail its store records events on."""
    trail = MemoryPheromoneTrail(clock)
    store = InMemoryMemoryStore(trail)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return MemoryContext(store=store, identity=identity, clock=clock), trail


def _scripted_deps(ctx: MemoryContext, reply: dict[str, object] | None = None) -> CompactionDeps:
    """Build a CompactionDeps whose RIPENER binding replies with `reply` (or _SCRIPTED_REPLY)."""
    provider = FakeLLMProvider()
    provider.script(text_response(json.dumps(reply if reply is not None else _SCRIPTED_REPLY)))
    bound = make_bound(provider=provider, slot=ModelSlot.RIPENER)
    return CompactionDeps(bound=bound, gate=DirectCallGate(), ctx=ctx)


async def test_compact_deposits_a_summary_entry_referencing_every_source() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    source_one = make_bee_bread_entry(clock=clock)
    source_two = make_bee_bread_entry(clock=clock)
    request = make_compaction_request(clock=clock, sources=(source_one, source_two))

    result = await compact(request, _scripted_deps(ctx))

    assert result.entry.kind == BeeBreadEntryKind.SUMMARY
    assert result.entry.ref_ids == (source_one.id, source_two.id)
    fetched = await ctx.store.get_bee_bread_entry(result.entry.id, HoneyClearance.C2)
    assert fetched == result.entry


async def test_compact_appends_pins_verbatim() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    pin = make_pin(clock=clock, text="Always back up before a deploy.")
    request = make_compaction_request(clock=clock, pins=(pin,))

    result = await compact(request, _scripted_deps(ctx))

    # Verbatim: the pin's own text, untouched, somewhere in the stored payload.
    assert pin.text in (result.entry.payload or "")
    # The model was never shown the pin (module docstring): only the scripted summary/key
    # facts/open threads it actually returned should shape the rest of the payload.
    assert _SCRIPTED_SUMMARY in (result.entry.payload or "")


async def test_compact_clearance_is_max_of_sources_and_pins() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    low = make_bee_bread_entry(clock=clock, clearance=HoneyClearance.C0)
    high_pin = make_pin(clock=clock, clearance=HoneyClearance.C1)
    request = make_compaction_request(
        clock=clock, sources=(low,), pins=(high_pin,), clearance=HoneyClearance.C1
    )

    result = await compact(request, _scripted_deps(ctx))

    assert result.entry.clearance == HoneyClearance.C1


async def test_compact_refuses_a_summary_source() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    summary_entry = make_bee_bread_entry(
        clock=clock, kind=BeeBreadEntryKind.SUMMARY, text=None, payload="a prior summary"
    )
    request = make_compaction_request(clock=clock, sources=(summary_entry,))

    with pytest.raises(SummaryOfSummaryError):
        await compact(request, _scripted_deps(ctx))


async def test_compact_records_memory_compacted_event_with_token_counts() -> None:
    clock = FakeClock()
    ctx, trail = _ctx_and_trail(clock)
    source = make_bee_bread_entry(clock=clock)
    request = make_compaction_request(clock=clock, sources=(source,))

    result = await compact(request, _scripted_deps(ctx))

    events = await trail.query(TrailQuery(subject_id=result.entry.id))
    assert len(events) == 1
    assert events[0].kind == "memory.compacted"
    assert events[0].payload["source_ids"] == [source.id]
    assert events[0].payload["tokens_before"] == result.tokens_before
    assert events[0].payload["tokens_after"] == result.tokens_after


async def test_compact_refuses_empty_sources() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    request = make_compaction_request(clock=clock, sources=())

    with pytest.raises(EmptyCompactionError):
        await compact(request, _scripted_deps(ctx))


async def test_compact_refuses_more_sources_than_max_ref_ids() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    sources = tuple(make_bee_bread_entry(clock=clock) for _ in range(MAX_REF_IDS + 1))
    request = make_compaction_request(clock=clock, sources=sources)

    with pytest.raises(TooManySourcesError):
        await compact(request, _scripted_deps(ctx))


async def test_compact_refuses_a_source_above_the_callers_clearance() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    source = make_bee_bread_entry(clock=clock, clearance=HoneyClearance.C2)
    request = make_compaction_request(clock=clock, sources=(source,), clearance=HoneyClearance.C1)

    with pytest.raises(ClearanceError):
        await compact(request, _scripted_deps(ctx))


async def test_compact_refuses_a_pin_above_the_callers_clearance() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    pin = make_pin(clock=clock, clearance=HoneyClearance.C2)
    request = make_compaction_request(clock=clock, pins=(pin,), clearance=HoneyClearance.C1)

    with pytest.raises(ClearanceError):
        await compact(request, _scripted_deps(ctx))


async def test_compact_calls_on_the_bound_models_own_slot() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    request = make_compaction_request(clock=clock)
    provider = FakeLLMProvider()
    provider.script(text_response(json.dumps(_SCRIPTED_REPLY)))
    bound = make_bound(provider=provider, slot=ModelSlot.RIPENER)
    deps = CompactionDeps(bound=bound, gate=DirectCallGate(), ctx=ctx)

    await compact(request, deps)

    assert provider.calls[0].slot == ModelSlot.RIPENER
