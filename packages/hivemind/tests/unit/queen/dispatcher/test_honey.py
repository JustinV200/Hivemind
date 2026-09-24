"""Tests for hivemind.queen.dispatcher.honey: the Queen's Honey pre-check and plan consultation.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/honey.py (codingrules section 3). Every consultation
    searches a real SQLite Honey Store (`builders.house_bee.open_honey_access`) whose rows were
    deposited through intake and ripened by a real Ripener pass; the Queen side is
    `builders.queen.make_queen_deps` with its `honey` field set.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.honey for the module under test.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from pathlib import Path

from builders.cells import make_cell
from builders.honey import make_nectar_submission
from builders.house_bee import HoneyHarness, open_honey_access
from builders.memory import make_wax_proposal_input
from builders.queen import WardenEnd, make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskGraphDraft
from hivemind.cell import Cell, CellKind, CombShieldLevel, HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.honey_store import NectarOrigin
from hivemind.llm import BoundModel, UnresolvableSlotError
from hivemind.manifest import HoneyRetrievalSection
from hivemind.memory import MemoryContext
from hivemind.memory.cell_wax import propose_wax, write_wax
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import (
    consult_for_assignment,
    consult_for_plan,
    dispatch_ready,
    redispatch,
    resume_paused,
)
from waggle.clock import FakeClock
from waggle.ids import CellId, new_warden_id
from waggle.messages.cell.wax import WaxDecision
from waggle.messages.honey import HoneyHit

_OBJECTIVE = "Find which port the widget service listens on and write it to answer.txt."
_FACT = b"The widget service listens on port 48213; its health check is /healthz."
_WAX_TEXT_CAP = 4_000


async def _setup(
    tmp_path: Path, clock: FakeClock, cell: Cell | None = None
) -> tuple[QueenDeps, WardenLink, WardenEnd, HoneyHarness]:
    """A Queen whose `honey` is a real, empty Honey Store, with one attached Warden link."""
    harness = await open_honey_access(tmp_path, clock)
    deps, link, end = make_queen_deps(clock, cell=cell, honey=harness.access)
    return deps, link, end, harness


async def _task(deps: QueenDeps, clearance: HoneyClearance = HoneyClearance.C1) -> Task:
    """Submit a one-task goal with `_OBJECTIVE` at `clearance`, and return its task."""
    base = make_graph_draft({"answer": ()})
    draft = base.tasks[0].model_copy(update={"objective": _OBJECTIVE, "clearance": clearance})
    (task,) = await deps.chamber.submit(TaskGraphDraft(tasks=(draft,)))
    return task


async def _ripen_finding(
    harness: HoneyHarness, clock: FakeClock, content: bytes = _FACT, **overrides: object
) -> None:
    """Deposit one finding through intake and ripen it, so a search can find it."""
    submission = make_nectar_submission(clock=clock, content=content, **overrides)
    await harness.access.intake.submit(submission)
    await harness.access.ripener.run_pass()


async def _consulted(deps: QueenDeps) -> list[PheromoneEvent]:
    """Every `queen.honey_consulted` event on the Queen's own trail."""
    return list(await deps.trail.query(TrailQuery(kind="queen.honey_consulted")))


async def _write_wax(
    deps: QueenDeps, cell_id: CellId, *, clearance: HoneyClearance = HoneyClearance.C1, **kw: object
) -> str:
    """Propose and write one CAUTION about `cell_id` in the Queen's memory; return its id."""
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    inputs = make_wax_proposal_input(
        clock=deps.clock,
        cell_id=cell_id,
        clearance=clearance,
        proposer=new_warden_id(deps.clock),
        **kw,
    )
    proposed = await propose_wax(inputs, _WAX_TEXT_CAP, ctx)
    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the cap.", ctx)
    return written.id


# ──────────────────────────────────────────────────────────────────────────────
# consult_for_assignment
# ──────────────────────────────────────────────────────────────────────────────


async def test_consult_for_assignment_attaches_nothing_with_no_honey_store() -> None:
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)

    assert await consult_for_assignment(deps, await _task(deps), link) == ()
    assert await _consulted(deps) == []


async def test_consult_for_assignment_attaches_nothing_when_the_pre_check_is_off(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    deps, link, _end, harness = await _setup(tmp_path, clock)
    off = dataclasses.replace(harness.access, retrieval=HoneyRetrievalSection(precheck_max_hits=0))
    deps = dataclasses.replace(deps, honey=off)
    await _ripen_finding(harness, clock)

    assert await consult_for_assignment(deps, await _task(deps), link) == ()
    assert await _consulted(deps) == []


async def test_consult_for_assignment_finds_the_hives_knowledge_and_records_the_consultation(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    deps, link, _end, harness = await _setup(tmp_path, clock)
    await _ripen_finding(harness, clock)
    task = await _task(deps)

    hits = await consult_for_assignment(deps, task, link)

    assert hits, "the ripened finding about the widget service was not attached"
    assert all(hit.scope == "hive" for hit in hits)
    assert any("48213" in hit.excerpt for hit in hits)
    (event,) = await _consulted(deps)
    assert event.subject_id == task.id
    assert event.payload["stage"] == "assign"
    assert event.payload["hits"] == len(hits)
    assert event.payload["wax"] == 0


async def test_consult_for_assignment_puts_the_cells_live_wax_first(tmp_path: Path) -> None:
    clock = FakeClock()
    deps, link, _end, harness = await _setup(tmp_path, clock)
    await _ripen_finding(harness, clock)
    wax_id = await _write_wax(deps, link.cell.id)

    hits = await consult_for_assignment(deps, await _task(deps), link)

    first = hits[0]
    assert first.honey_ref == f"/cells/{link.cell.id}/wax/{wax_id}"
    assert first.title == "Cell Wax CAUTION"
    assert first.score == 1.0
    assert first.scope == f"cell:{link.cell.id}"
    assert first.provenance.cell_id == link.cell.id
    assert len(hits) > 1  # The Honey found by the searches still follows it.
    (event,) = await _consulted(deps)
    assert event.payload["wax"] == 1


async def test_consult_for_assignment_leaves_out_wax_past_its_own_expiry(tmp_path: Path) -> None:
    clock = FakeClock()
    deps, link, _end, _harness = await _setup(tmp_path, clock)
    await _write_wax(deps, link.cell.id, expires_at=clock.now() + timedelta(minutes=5))
    clock.advance(timedelta(minutes=10).total_seconds())  # Past it, before any sweep ran.

    hits = await consult_for_assignment(deps, await _task(deps), link)

    assert not any("/wax/" in hit.honey_ref for hit in hits)


async def test_consult_for_assignment_never_attaches_wax_above_the_tasks_clearance(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    deps, link, _end, _harness = await _setup(tmp_path, clock)
    await _write_wax(deps, link.cell.id, clearance=HoneyClearance.C2)

    hits = await consult_for_assignment(deps, await _task(deps, HoneyClearance.C1), link)

    assert hits == ()


async def test_consult_for_assignment_finds_the_chosen_cells_history_but_no_other_cells(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    deps, link, _end, harness = await _setup(tmp_path, clock)
    other = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    # Cell Wax history (roadmap 7.9a): ripened at cell:<id> scope, one for each Cell.
    for cell_id, content in ((link.cell.id, _FACT), (other.id, _FACT + b" Elsewhere.")):
        await _ripen_finding(
            harness, clock, content=content, origin=NectarOrigin.CELL_WAX, cell_id=cell_id
        )

    hits = await consult_for_assignment(deps, await _task(deps), link)

    assert {hit.scope for hit in hits} == {f"cell:{link.cell.id}"}


async def test_consult_for_assignment_withholds_c2_honey_from_a_c1_task(tmp_path: Path) -> None:
    clock = FakeClock()
    deps, link, _end, harness = await _setup(tmp_path, clock)
    await _ripen_finding(harness, clock, declared=HoneyClearance.C2)

    hits = await consult_for_assignment(deps, await _task(deps, HoneyClearance.C1), link)

    assert hits == ()
    (event,) = await _consulted(deps)
    withheld = event.payload["withheld"]
    assert isinstance(withheld, int) and withheld >= 1


async def test_consult_for_assignment_on_a_night_veil_cell_records_nothing(tmp_path: Path) -> None:
    clock = FakeClock()
    veiled = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.NIGHT_VEIL)
    deps, link, _end, harness = await _setup(tmp_path, clock, cell=veiled)
    await _ripen_finding(harness, clock)

    hits = await consult_for_assignment(deps, await _task(deps), link)

    assert hits  # C1 Honey still reaches a Night Veil Cell (its tier reads C0 and C1).
    assert await _consulted(deps) == []


async def test_consult_for_assignment_yields_nothing_when_it_fails(tmp_path: Path) -> None:
    clock = FakeClock()
    deps, link, _end, harness = await _setup(tmp_path, clock)
    await _ripen_finding(harness, clock)

    def unbound(slot: ModelSlot) -> BoundModel:
        raise UnresolvableSlotError(f"No binding for {slot.manifest_key} in this test.")

    broken = dataclasses.replace(deps, bound_for=unbound)

    assert await consult_for_assignment(broken, await _task(broken), link) == ()


async def test_consult_for_assignment_keeps_to_the_pre_check_size(tmp_path: Path) -> None:
    clock = FakeClock()
    deps, link, _end, harness = await _setup(tmp_path, clock)
    one_hit = dataclasses.replace(
        harness.access, retrieval=HoneyRetrievalSection(precheck_max_hits=1)
    )
    deps = dataclasses.replace(deps, honey=one_hit)
    await _write_wax(deps, link.cell.id)
    await _ripen_finding(harness, clock)

    hits = await consult_for_assignment(deps, await _task(deps), link)

    assert len(hits) == 1
    assert "/wax/" in hits[0].honey_ref  # The Queen's own caution is never crowded out.


# ──────────────────────────────────────────────────────────────────────────────
# consult_for_plan
# ──────────────────────────────────────────────────────────────────────────────


async def test_consult_for_plan_reads_every_scope_up_to_the_goals_clearance(tmp_path: Path) -> None:
    clock = FakeClock()
    deps, _link, _end, harness = await _setup(tmp_path, clock)
    # A task's working material (task:<id> scope): only the Queen, not a Worker, reads it all.
    await _ripen_finding(harness, clock, origin=NectarOrigin.BEE_BREAD)
    await _ripen_finding(harness, clock, content=_FACT + b" Royal.", declared=HoneyClearance.C2)

    consultation = await consult_for_plan(deps, _OBJECTIVE, HoneyClearance.C1)

    assert consultation is not None
    assert consultation.hits
    assert all(hit.scope.startswith("task:") for hit in consultation.hits)
    assert all(hit.clearance.value != "C2" for hit in consultation.hits)
    assert consultation.withheld >= 1


async def test_consult_for_plan_returns_none_with_no_honey_store() -> None:
    clock = FakeClock()
    deps, _link, _end = make_queen_deps(clock)

    assert await consult_for_plan(deps, _OBJECTIVE, HoneyClearance.C2) is None


# ──────────────────────────────────────────────────────────────────────────────
# Through the dispatcher: fresh dispatches and retries alike carry the pre-check
# ──────────────────────────────────────────────────────────────────────────────


async def test_dispatch_ready_sends_the_pre_checks_hits_on_the_task_assign(tmp_path: Path) -> None:
    clock = FakeClock()
    deps, link, end, harness = await _setup(tmp_path, clock)
    await _ripen_finding(harness, clock)
    task = await _task(deps)

    await dispatch_ready(deps, [link])
    assignment = await end.wait_for_assignment()

    assert assignment.task_id == task.id
    assert _has_fact(assignment.honey)


async def test_redispatch_sends_the_pre_checks_hits_on_the_retry_too(tmp_path: Path) -> None:
    clock = FakeClock()
    deps, link, end, harness = await _setup(tmp_path, clock)
    task = await _task(deps)
    await dispatch_ready(deps, [link])
    first = await end.wait_for_assignment()
    await _ripen_finding(harness, clock)  # Learned between the first attempt and the retry.

    await redispatch(deps, [link], task.id, attempt=first.attempt + 1)
    await end.pump_until(lambda: len(end.assignments) == 2)
    retry = end.assignments[-1]

    assert not _has_fact(first.honey)
    assert retry.attempt == first.attempt + 1
    assert _has_fact(retry.honey)


async def test_resume_paused_sends_the_pre_checks_hits_on_the_resumed_assignment_too(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    deps, link, end, harness = await _setup(tmp_path, clock)
    task = await _task(deps)
    await dispatch_ready(deps, [link])
    await end.wait_for_assignment()
    await deps.chamber.pause(task.id, "Clustered for this test.")
    await _ripen_finding(harness, clock)  # Learned while the task was paused.

    await resume_paused(deps, [link], task.id, None, "Woken for this test.")
    await end.pump_until(lambda: len(end.assignments) == 2)

    assert _has_fact(end.assignments[-1].honey)


def _has_fact(hits: tuple[HoneyHit, ...]) -> bool:
    """Whether any hit carries the widget service's port."""
    return any("48213" in hit.excerpt for hit in hits)
