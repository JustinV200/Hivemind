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
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from builders.capping import make_postcondition
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import CombShieldLevel
from hivemind.forage import Effort, ForageMap, SlotBinding
from hivemind.pheromone import TrailQuery
from hivemind.supervision.capping import FakeNurseReviewer, NurseAnswer
from hivemind.wardens.nurse import NurseWiring
from hivemind.wardens.ticks.results import _veiled_from_the_nurse
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import FakeClock
from waggle.ids import WardenId, new_cell_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import MAX_MODEL_GRADE
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.labels import Postcondition, PostconditionKind
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


def _factory() -> Callable[[WorkerRole], ScriptedWorker]:
    """A Worker that writes the report and claims it as an artifact."""

    async def script(ctx: WorkerContext, assignment: TaskAssign, resume: object) -> WorkerOutcome:
        await asyncio.sleep(0)
        await ctx.session.put_file(Path(_OUTPUT), _TEXT)
        artifact = ArtifactRef(
            path=_OUTPUT, size_bytes=len(_TEXT), sha256=sha256(_TEXT).hexdigest()
        )
        return make_outcome(artifacts=(artifact,), summary="Trust me, it is perfect.")

    return lambda role: ScriptedWorker(script, role=role)


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
    reviewer: FakeNurseReviewer, acceptance: tuple[Postcondition, ...], role: WorkerRole
) -> tuple[TaskResult, list[str]]:
    """Run one task through a real Warden with `reviewer` as its Nurse; return what came back."""
    clock = FakeClock()
    nurse = NurseWiring(reviewer=reviewer, forage=ForageMap((), clock=clock))
    deps, queen_end, warden_id = make_warden_deps(clock, worker_factory=_factory(), nurse=nurse)
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


def _judge(provider: str) -> SlotBinding:
    """The `[llm.slots]` judge row, on `provider`."""
    return SlotBinding(
        key="judge", provider=provider, model="m", fallback=None, effort=Effort.MEDIUM
    )


@pytest.mark.parametrize(
    ("shield", "rows", "veiled"),
    [
        (CombShieldLevel.NIGHT_VEIL, (_judge("hosted"),), True),  # Files would leave the machine.
        (CombShieldLevel.NIGHT_VEIL, (), True),  # A chain it cannot follow fails closed.
        (CombShieldLevel.NIGHT_VEIL, (_judge("fake"),), False),  # A local judge may read them.
        (CombShieldLevel.MEADOW, (_judge("hosted"),), False),
    ],
)
def test_a_night_veil_cell_never_asks_a_hosted_nurse(
    shield: CombShieldLevel, rows: tuple[SlotBinding, ...], veiled: bool
) -> None:
    deps = SimpleNamespace(bindings=rows, local_providers=frozenset({"fake"}))
    warden = cast(Warden, SimpleNamespace(_cell=SimpleNamespace(comb_shield=shield), _deps=deps))

    assert _veiled_from_the_nurse(warden) is veiled
