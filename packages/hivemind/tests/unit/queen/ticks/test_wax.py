"""Tests for hivemind.queen.ticks.wax: handle_wax_proposed's autopilot and awake paths.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/wax.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.wax for the module under test.
    - .claude/roadmap.md step 4.2a exit criteria: "A Warden's CAUTION about its own Cell is
      written by autopilot with no awake episode... a BLOCK proposed by a Drone reaches the
      Queen's awake mode."
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace

from builders.queen import make_queen_deps

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider, ProviderCapabilities
from hivemind.llm.fake import text_response
from hivemind.llm.models import LLMRequest, LLMResponse
from hivemind.memory import MemoryContext
from hivemind.memory.cell_wax import WaxState
from hivemind.pheromone import EphemeralSegments, VeiledTrail
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.ticks.wax import handle_wax_proposed
from waggle.ids import WardenId, new_worker_id
from waggle.messages.cell import CellWaxProposed
from waggle.messages.cell.wax import WaxOrigin
from waggle.messages.cell.wax import WaxSeverity as WireWaxSeverity
from waggle.messages.labels import HoneyClearance as WireHoneyClearance

_WRITE_DECISION: Mapping[str, object] = {
    "action": "WRITE_WAX",
    "task_id": None,
    "reason": "Judged and accepted.",
    "binding": None,
}
_REJECT_DECISION: Mapping[str, object] = {
    "action": "REJECT_WAX",
    "task_id": None,
    "reason": "Not convincing.",
    "binding": None,
}


def _responder(decision: Mapping[str, object]) -> Callable[[LLMRequest], LLMResponse]:
    def respond(request: LLMRequest) -> LLMResponse:
        if request.response_schema is not None:
            return text_response(json.dumps(decision))
        return text_response(f"```json\n{json.dumps(decision)}\n```")

    return respond


def _proposal(
    cell_id: str, severity: WireWaxSeverity, proposer: str | None, origin: WaxOrigin
) -> CellWaxProposed:
    return CellWaxProposed(
        cell_id=cell_id,
        severity=severity,
        text="This Cell's disk fills up under heavy load.",
        reason="Saw two ENOSPC failures in a row.",
        clearance=WireHoneyClearance.C1,
        expires_at=None,
        origin=origin,
        proposer=proposer,
        task_id=None,
    )


async def test_a_wardens_own_caution_is_written_by_autopilot_with_no_awake_episode() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    proposed = _proposal(link.cell.id, WireWaxSeverity.CAUTION, link.warden_id, WaxOrigin.BEE)

    await handle_wax_proposed(deps, {link.warden_id: link}, link.warden_id, proposed)

    # No awake episode: the Queen's own model was never called.
    assert provider.calls == []
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    written = await ctx.store.list_wax(
        link.cell.id, frozenset({WaxState.WRITTEN}), HoneyClearance.C1
    )
    assert len(written) == 1
    assert written[0].severity.value == "CAUTION"
    events = await deps.trail.query(TrailQuery())
    assert [e.kind for e in events] == ["memory.wax_proposed", "memory.wax_written"]
    written_wire = await warden_end.wait_for_wax_written()
    assert written_wire.wax_id == written[0].id
    assert written_wire.decided_by.value == "AUTOPILOT"


async def test_a_drones_block_proposal_reaches_the_queens_awake_mode() -> None:
    provider = FakeLLMProvider(
        capabilities=ProviderCapabilities.full(), responder=_responder(_WRITE_DECISION)
    )
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    worker_id = new_worker_id(deps.clock)
    proposed = _proposal(link.cell.id, WireWaxSeverity.BLOCK, worker_id, WaxOrigin.BEE)

    await handle_wax_proposed(deps, {link.warden_id: link}, link.warden_id, proposed)

    # The Queen's own model was consulted: an awake episode ran.
    assert len(provider.calls) >= 1
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    written = await ctx.store.list_wax(
        link.cell.id, frozenset({WaxState.WRITTEN}), HoneyClearance.C1
    )
    assert len(written) == 1
    assert written[0].decided_by is not None
    assert written[0].decided_by.value == "AWAKE"
    written_wire = await warden_end.wait_for_wax_written()
    assert written_wire.decided_by.value == "AWAKE"


async def test_a_proposal_about_another_cells_warden_reaches_awake_and_may_be_rejected() -> None:
    provider = FakeLLMProvider(
        capabilities=ProviderCapabilities.full(), responder=_responder(_REJECT_DECISION)
    )
    deps, link, _warden_end = make_queen_deps(fake_provider=provider)
    other_warden_id = WardenId("warden_01ARZ3NDEKTSV4RRFFQ69G5FAV")
    proposed = _proposal(link.cell.id, WireWaxSeverity.NOTE, other_warden_id, WaxOrigin.BEE)

    await handle_wax_proposed(deps, {link.warden_id: link}, link.warden_id, proposed)

    assert len(provider.calls) >= 1
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    written = await ctx.store.list_wax(
        link.cell.id, frozenset({WaxState.WRITTEN}), HoneyClearance.C1
    )
    assert written == ()
    rejected = await ctx.store.list_wax(
        link.cell.id, frozenset({WaxState.REJECTED}), HoneyClearance.C1
    )
    assert len(rejected) == 1


async def test_a_proposal_about_a_night_veil_cell_is_refused_and_leaves_nothing_behind() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    deps, link, _warden_end = make_queen_deps(fake_provider=provider)
    durable, segments = deps.trail, EphemeralSegments(deps.clock)
    segments.open(link.cell.id)  # The Warden's own Cell is a Night Veil one.
    deps = replace(deps, trail=VeiledTrail(durable, segments))
    proposed = _proposal(link.cell.id, WireWaxSeverity.CAUTION, link.warden_id, WaxOrigin.BEE)

    await handle_wax_proposed(deps, {link.warden_id: link}, link.warden_id, proposed)

    # Not even PROPOSED: no row, and none of its words on the durable trail.
    assert provider.calls == []
    assert await deps.memory.list_wax(link.cell.id, frozenset(WaxState), HoneyClearance.C2) == ()
    assert await durable.query(TrailQuery()) == ()
    # The refusal is the Queen's own record about the Cell, veiled with the rest of it.
    [refused] = await segments.query(link.cell.id, TrailQuery())
    assert (refused.kind, refused.payload) == (
        "queen.decided",
        {"reason": "night_veil_wax_refused"},
    )
