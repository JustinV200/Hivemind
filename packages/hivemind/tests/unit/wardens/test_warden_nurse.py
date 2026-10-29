"""Tests for the Warden's accept path with the Nurse Bee wired in (ADR-0061, build step 4).

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/results.py (codingrules section 3), split by feature (14.2)
    from test_warden_spawn_and_accept.py: a real Warden, a scripted Worker that writes and claims
    a file, and a FakeNurseReviewer in place of the model.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.results for handle_accept.
    - hivemind.wardens.nurse.accept for the wiring and the request.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from functools import partial
from hashlib import sha256
from pathlib import Path

import pytest
from builders.capping import make_postcondition
from builders.cells import make_cell
from builders.forage import make_source
from builders.quarantine import settle
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import Cell, CellKind, CombShieldLevel
from hivemind.forage import Effort, ForageMap, ModelSlot, SlotBinding
from hivemind.guard import Capability, CapabilityFamily
from hivemind.llm import BoundModel
from hivemind.pheromone import TrailQuery
from hivemind.supervision.capping import FakeNurseReviewer, NurseAnswer, NurseRequest
from hivemind.wardens.nurse import NurseWiring
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.registry import ToolInvocation
from hivemind.workers.tools.session import write_file
from waggle.clock import FakeClock
from waggle.ids import TaskId, WardenId, new_cell_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import MAX_MODEL_GRADE
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.labels import Postcondition, PostconditionKind
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.task import (
    ArtifactRef,
    NurseFinding,
    NurseOutcome,
    NurseVerdict,
    TaskAssign,
    TaskResult,
    WorkerRole,
)

_OUTPUT = "output.txt"
_TEXT = b"the finished report"
_NURSE_COST = 0.25
_PASS = NurseAnswer(
    verdict=NurseVerdict(outcome=NurseOutcome.PASS, rubric_id="nurse-review/1"),
    nurse_grade=MAX_MODEL_GRADE,  # The fake Worker's model is ungraded, so only the top reviews.
    spend=_NURSE_COST,
)
_CHANGES = NurseAnswer(
    verdict=NurseVerdict(
        outcome=NurseOutcome.CHANGES_REQUESTED,
        findings=(NurseFinding(path=_OUTPUT, problem="It is a stub.", fix="Write the report."),),
        rubric_id="nurse-review/1",
    ),
    nurse_grade=MAX_MODEL_GRADE,
    spend=_NURSE_COST,
)
_NOBODY = NurseAnswer(verdict=None, nurse_grade=None)
_FILE = make_postcondition(PostconditionKind.FILE_EXISTS, subject=_OUTPUT)
_RUBRIC = make_postcondition(
    PostconditionKind.JUDGE_RUBRIC, subject="report", expected="It reads as a finished report."
)


async def _write_and_claim(
    ctx: WorkerContext, assignment: TaskAssign, resume: object
) -> WorkerOutcome:
    """Write the report and claim it as an artifact."""
    await asyncio.sleep(0)
    await ctx.session.put_file(Path(_OUTPUT), _TEXT)
    artifact = ArtifactRef(path=_OUTPUT, size_bytes=len(_TEXT), sha256=sha256(_TEXT).hexdigest())
    return make_outcome(artifacts=(artifact,), summary="Trust me, it is perfect.")


def _factory() -> Callable[[WorkerRole], ScriptedWorker]:
    """Workers that write the report and claim it."""
    return lambda role: ScriptedWorker(_write_and_claim, role=role)


def _grant(clock: FakeClock, assignment: TaskAssign, holder: WardenId) -> GrantIssued:
    source = SourceRef(source_id="local", provider="fake", model="test-model", host_cell_id=None)
    return GrantIssued(
        grant_id=assignment.grant_id,
        holder=holder,
        cell_id=new_cell_id(clock),
        task_id=None,
        revision=0,
        allowed=(AllowedBinding(slot="WORKER", source=source, max_effort=WireEffort.MEDIUM),),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=clock.now(),
        reason="test grant",
    )


async def _accept(
    reviewer: FakeNurseReviewer,
    acceptance: tuple[Postcondition, ...],
    role: WorkerRole,
    cells: tuple[Cell, ...] | None = None,
    **overrides: object,
) -> tuple[TaskResult, list[str]]:
    """Run one task through a real Warden with `reviewer` as its Nurse; return what came back.

    `cells` and `overrides` reach `make_warden_deps` (its Cell, its `[llm.slots]` rows, ...).
    """
    clock = FakeClock()
    nurse = NurseWiring(reviewer=reviewer, forage=ForageMap((), clock=clock))
    deps, queen_end, warden_id = make_warden_deps(
        clock,
        cells=cells,
        worker_factory=_factory(),
        fake_provider=None,  # Named only so mypy knows `overrides` cannot carry it.
        nurse=nurse,
        **overrides,
    )
    assignment = make_assignment(clock=clock, role=role, acceptance=acceptance)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    await queen_end.send(assignment)
    await queen_end.send(_grant(clock, assignment, warden_id))
    result = await queen_end.wait_for_result()
    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
    events = await deps.trail.query(TrailQuery(family="capping"))
    return result, [e.kind for e in events if "nurse" in e.kind]


@pytest.mark.parametrize(
    ("answer", "acceptance", "outcome", "reason", "kinds"),
    [
        (_PASS, (_FILE,), "SUCCEEDED", None, ["capping.nurse_reviewed"]),
        (_CHANGES, (_FILE,), "FAILED", "nurse", ["capping.nurse_reviewed"]),
        # No eligible Nurse: accepted "not nursed", flagged on the result and the trail.
        (_NOBODY, (_FILE,), "SUCCEEDED", None, ["capping.nurse_unavailable"]),
        # A rubric criterion holds on a Nurse's PASS, and fails closed when none could judge it.
        (_PASS, (_FILE, _RUBRIC), "SUCCEEDED", None, ["capping.nurse_reviewed"]),
        (_NOBODY, (_RUBRIC,), "FAILED", "acceptance", ["capping.nurse_unavailable"]),
    ],
)
async def test_the_nurse_decides_what_the_warden_reports(
    answer: NurseAnswer,
    acceptance: tuple[Postcondition, ...],
    outcome: str,
    reason: str | None,
    kinds: list[str],
) -> None:
    reviewer = FakeNurseReviewer(answer)

    result, recorded = await _accept(reviewer, acceptance, WorkerRole.DRONE)

    assert result.outcome.value == outcome
    assert reason is None or result.reason == reason
    assert result.review is not None and result.review.round == 1
    assert result.spend == pytest.approx(make_outcome().spend_usd + answer.spend)
    assert recorded == kinds
    # The fresh context: the file as the Warden read it, never the Worker's own account.
    (request,) = reviewer.calls
    assert [(f.path, f.text) for f in request.files] == [(_OUTPUT, _TEXT.decode())]
    assert "Trust me" not in request.model_dump_json()
    assert [o.kind for o in request.outcomes] == [PostconditionKind.FILE_EXISTS] * (
        _FILE in acceptance
    )


@pytest.mark.parametrize(
    ("role", "acceptance"),
    [
        (WorkerRole.UNDERTAKER, (_FILE,)),  # A skipped role is accepted on its machine checks.
        (WorkerRole.DRONE, (make_postcondition(subject="never-written.txt"),)),  # Failed first.
    ],
)
async def test_no_nurse_is_asked_when_it_has_nothing_to_review(
    role: WorkerRole, acceptance: tuple[Postcondition, ...]
) -> None:
    reviewer = FakeNurseReviewer(_PASS)

    result, recorded = await _accept(reviewer, acceptance, role)

    assert (reviewer.calls, recorded, result.review) == ([], [], None)


class _SlowNurse(FakeNurseReviewer):
    """A Nurse whose review waits until the test lets it answer, like a slow local model."""

    def __init__(self) -> None:
        super().__init__(_PASS)
        self.answer = asyncio.Event()

    async def review(self, request: NurseRequest) -> NurseAnswer:
        self.calls.append(request)
        await self.answer.wait()
        return _PASS


async def test_the_warden_keeps_beating_while_the_nurse_reviews() -> None:
    clock = FakeClock()
    reviewer = _SlowNurse()
    nurse = NurseWiring(reviewer=reviewer, forage=ForageMap((), clock=clock))
    deps, queen_end, warden_id = make_warden_deps(clock, worker_factory=_factory(), nurse=nurse)
    assignment = make_assignment(clock=clock, role=WorkerRole.DRONE, acceptance=(_FILE,))
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    await queen_end.send(assignment)
    await queen_end.send(_grant(clock, assignment, warden_id))
    await settle(lambda: bool(reviewer.calls))
    beats = len(queen_end.heartbeats)

    clock.advance(deps.heartbeat_interval_s)
    pumped = queen_end.pump_until(lambda: len(queen_end.heartbeats) > beats)
    await asyncio.wait_for(pumped, timeout=5.0)
    reviewer.answer.set()
    result = await queen_end.wait_for_result()
    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert result.outcome.value == "SUCCEEDED"


def _judge(provider: str) -> SlotBinding:
    """The `[llm.slots]` judge row, on `provider`."""
    return SlotBinding(
        key="judge", provider=provider, model="m", fallback=None, effort=Effort.MEDIUM
    )


@pytest.mark.parametrize(
    ("judge", "asked"),
    [
        ((_judge("hosted"),), False),  # The files would leave the machine.
        ((), False),  # A JUDGE chain it cannot follow fails closed.
        ((_judge("fake"),), True),  # A local judge may read them.
    ],
)
async def test_a_night_veil_cell_never_asks_a_hosted_nurse(
    judge: tuple[SlotBinding, ...], asked: bool
) -> None:
    reviewer = FakeNurseReviewer(_PASS)
    veiled = make_cell(CellKind.VIRTUAL, comb_shield=CombShieldLevel.NIGHT_VEIL)
    rows = (*make_warden_deps()[0].bindings, *judge)  # The Worker's own rows serve locally.

    result, recorded = await _accept(
        reviewer,
        (_FILE,),
        WorkerRole.DRONE,
        cells=(veiled,),
        lease_capability=Capability(family=CapabilityFamily.CELL_VIRTUAL),
        bindings=rows,
        local_providers=frozenset({"fake"}),
    )

    assert result.outcome.value == "SUCCEEDED"  # Not nursed is still accepted on its checks.
    assert len(reviewer.calls) == asked
    assert recorded == ["capping.nurse_reviewed" if asked else "capping.nurse_unavailable"]


def _row(key: str, model: str, fallback: str | None = None) -> SlotBinding:
    """One `[llm.slots]` row on the in-process `fake` provider."""
    return SlotBinding(
        key=key, provider="fake", model=model, fallback=fallback, effort=Effort.MEDIUM
    )


def _queen_rebind(task_id: TaskId, binding: str) -> Intervene:
    """The Queen's Intervene(REBIND) naming `binding`, as her fallback chain resolves it."""
    return Intervene(
        action=InterventionAction.REBIND,
        subject=None,
        task_id=task_id,
        slot="WORKER",
        binding=binding,
        alarm_id=None,
        reason="The bound provider is unavailable.",
    )


def _bind_model(bound: BoundModel, models: dict[str, str], key: str) -> BoundModel:
    """Bind `key` on the WORKER slot to its own model, so each key carries its own grade."""
    return dataclasses.replace(bound, slot=ModelSlot.WORKER, binding=key, model=models[key])


async def test_a_rebound_attempt_is_reviewed_as_every_bee_that_worked_on_it() -> None:
    # ADR-0061 step 4: the first bee, on the strong `worker` model, lands a verified write; the
    # Queen rebinds the task to the weak `worker_fallback`, whose fresh bee claims. The Nurse sees
    # the first bee's action and is held to the strong model's grade, not only the claimant's.
    drafted: list[str] = []

    async def script(ctx: WorkerContext, assignment: TaskAssign, resume: object) -> WorkerOutcome:
        if assignment.attempt == 1:
            invocation = ToolInvocation(ctx=ctx, assignment=assignment)
            drafted.append(await write_file(invocation, {"path": "draft.txt", "content": "v1"}))
            await ctx.clock.sleep(1_000_000.0)  # Works on until the rebind retires it.
        return await _write_and_claim(ctx, assignment, resume)

    clock = FakeClock()
    graded = (
        make_source(source_id="strong", provider="fake-warden", model="strong", grade=4),
        make_source(source_id="weak", provider="fake-warden", model="weak", grade=1),
    )
    reviewer = FakeNurseReviewer(_PASS)
    nurse = NurseWiring(reviewer=reviewer, forage=ForageMap(graded, clock=clock))
    deps, queen_end, warden_id = make_warden_deps(
        clock,
        worker_factory=lambda role: ScriptedWorker(script, role=role),
        nurse=nurse,
        bindings=(_row("worker", "strong", "worker_fallback"), _row("worker_fallback", "weak")),
    )
    models = {"worker": "strong", "worker_fallback": "weak"}  # Each key binds its own model.
    rebind = partial(_bind_model, deps.bound, models)
    deps = dataclasses.replace(deps, rebind=rebind)
    assignment = make_assignment(clock=clock, acceptance=(_FILE,))
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    await queen_end.send(assignment)
    await queen_end.send(_grant(clock, assignment, warden_id))
    await settle(lambda: bool(drafted))

    await queen_end.send(_queen_rebind(assignment.task_id, "worker_fallback"))
    result = await queen_end.wait_for_result()
    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert (result.outcome.value, result.attempt) == ("SUCCEEDED", 2)
    (request,) = reviewer.calls
    assert [action.paths for action in request.actions] == [("draft.txt",)]
    assert request.worker_grade == 4  # The first bee's model, not the claimant's grade 1.
