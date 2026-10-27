"""Fit the Nurse Bee into a Warden's accept path: what it judges, how it is asked, what is recorded.

ADR-0061 plugs the Nurse in after deterministic acceptance passes and before the Warden reports
SUCCEEDED. This module is everything that step needs that is not the Warden itself: the wiring a
Warden is built with (`NurseWiring`, from the manifest's `[nurse]`), which criteria a machine
check defers to the Nurse (`JUDGED_KINDS`: a `JUDGE_RUBRIC` criterion, "unsupported in v0" until
now), the request built from Warden-held facts only (`nurse_request`), the Worker's own `llm.call`
events for its grade (`grant_calls`), the review itself with an outage turned into "not nursed"
(`ask_nurse`, `not_nursed`), and the trail event that records a round (`nurse_event`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens.nurse`. Called by
    `hivemind.wardens.ticks.results.handle_accept`; `NurseWiring` is built by the composition
    roots (`hivemind.cli.compose.deps.warden`, `hivemind.cli.in_cell.deps`). Calls into
    `hivemind.forage`, `hivemind.llm` (the outage errors), `hivemind.memory` (MemoryIdentity),
    `hivemind.pheromone`, `hivemind.supervision.capping`, `hivemind.wardens.acceptance`,
    `hivemind.wardens.nurse.strength` and waggle only.

Key invariants:
    - A request never carries the Worker's transcript, hot state, Handoff, a proposal's reason or
      the claim's summary; `nurse_request` reads none of them.
    - A JUDGE_RUBRIC criterion counts as held only on a REVIEWED PASS: the machine report the
      Warden decides on first leaves it out, and the Nurse is told it has no machine check.
    - An outage (ProviderUnavailableError, RateLimitedError) is "not nursed" (UNAVAILABLE), never
      a crash of the Warden's loop and never an escalation.
    - A trail event carries ids, enums, counts and grades only, never a finding's words, and no
      `tier` key, so the Night Veil teardown's tier counts never see it.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.supervision.capping.checks.nurse for run_nurse, the seam this calls through.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.forage import ForageMap
from hivemind.forage.tempo import Tempo
from hivemind.llm import ProviderUnavailableError, RateLimitedError
from hivemind.memory import MemoryIdentity
from hivemind.pheromone import CappingEvent, LlmEvent, PheromoneTrail, TrailQuery
from hivemind.supervision.capping import NurseFile, NurseRequest, NurseReviewer, run_nurse
from hivemind.supervision.capping.checks.judge import MAX_GOAL_CHARS
from hivemind.wardens.acceptance import AcceptanceReport
from hivemind.wardens.nurse.strength import LLM_CALL_KIND, WorkerStrength
from waggle.clock import Clock
from waggle.ids import CellId, TaskId, new_event_id
from waggle.messages.capping import ProposedAction
from waggle.messages.labels import PostconditionKind
from waggle.messages.task import NurseReview, NurseReviewStatus, TaskAssign, TaskResult, WorkerRole

JUDGED_KINDS = frozenset({PostconditionKind.JUDGE_RUBRIC})  # No machine check; the Nurse's own.
DEFAULT_SKIP_ROLES = frozenset({WorkerRole.UNDERTAKER, WorkerRole.HOUSE_BEE})
DEFAULT_MAX_EVIDENCE_CHARS = 32_000  # Mirrors the manifest's [nurse] default.
# The newest llm.call rows read for the Worker's grade: far more calls than one attempt makes,
# few enough that one accept never scans the whole trail.
CALL_QUERY_LIMIT = 2_000
REVIEWED_KIND = "capping.nurse_reviewed"
UNAVAILABLE_KIND = "capping.nurse_unavailable"
_OUTAGES = (ProviderUnavailableError, RateLimitedError)

log = get_logger(__name__)

__all__ = [
    "CALL_QUERY_LIMIT",
    "JUDGED_KINDS",
    "AttemptEvidence",
    "NurseWiring",
    "ask_nurse",
    "grant_calls",
    "machine_report",
    "not_nursed",
    "nurse_event",
    "nurse_request",
    "unjudged_report",
]


@dataclass(frozen=True, slots=True)
class NurseWiring:
    """What a Warden's accept path needs to run the Nurse: built from the manifest's `[nurse]`.

    A Warden built with none (`WardenDeps.nurse` is None, `[nurse] enabled = false`) never asks.
    """

    reviewer: NurseReviewer  # ModelNurseReviewer in production; FakeNurseReviewer in tests.
    forage: ForageMap  # Grades the Worker's models, as the reviewer grades its own.
    skip_roles: frozenset[WorkerRole] = DEFAULT_SKIP_ROLES
    max_evidence_chars: int = DEFAULT_MAX_EVIDENCE_CHARS

    def reviews(self, role: WorkerRole) -> bool:
        """Return whether an attempt by `role` is reviewed (SCOUT is; skip_roles are not)."""
        return role not in self.skip_roles


@dataclass(frozen=True, slots=True)
class AttemptEvidence:
    """What the Warden gathered about one attempt, beside its assignment and claim."""

    actions: tuple[ProposedAction, ...]  # The attempt's verified actions, oldest first.
    files: tuple[NurseFile, ...]  # The touched files, read and scanned (nurse.evidence).
    strength: WorkerStrength  # The Worker's grade and providers, spill included.
    spend_left: float | None  # What the grant has left after the attempt; None uncapped.


def machine_report(report: AcceptanceReport) -> AcceptanceReport:
    """Return `report` without the criteria only the Nurse can judge.

    Args:
        report: Every criterion's machine outcome; a JUDGE_RUBRIC one never holds there.

    Returns:
        The report the Warden decides on before asking the Nurse.
    """
    outcomes = tuple(o for o in report.outcomes if o.kind not in JUDGED_KINDS)
    failing = tuple(o for o in outcomes if not o.has_held)
    return AcceptanceReport(outcomes=outcomes, passed=not failing, failing=failing)


def unjudged_report(report: AcceptanceReport, review: NurseReview) -> AcceptanceReport:
    """Return `report` with each JUDGE_RUBRIC criterion failing because no Nurse judged it.

    Args:
        report: Every criterion's machine outcome.
        review: The UNAVAILABLE or ERROR review that left them unjudged.

    Returns:
        A failing report naming why, so the ACCEPTANCE_FAILED Alarm says what happened.
    """
    observed = f"no Nurse judged it (review {review.status.value})"
    outcomes = tuple(
        o.model_copy(update={"observed": observed}) if o.kind in JUDGED_KINDS else o
        for o in report.outcomes
    )
    failing = tuple(o for o in outcomes if not o.has_held)
    return AcceptanceReport(outcomes=outcomes, passed=not failing, failing=failing)


def nurse_request(
    assignment: TaskAssign, claim: TaskResult, machine: AcceptanceReport, gathered: AttemptEvidence
) -> NurseRequest:
    """Build the Nurse's request from Warden-held facts only.

    Args:
        assignment: The task as the Queen assigned it.
        claim: The sub-bee's claim; only its artifacts are read, never its summary.
        machine: The machine report (`machine_report`), whose outcomes the Nurse sees.
        gathered: The actions, files, Worker strength and budget the Warden gathered.

    Returns:
        The request; no Worker id, transcript, Handoff, reason or summary can be in it.
    """
    return NurseRequest(
        goal=assignment.objective[:MAX_GOAL_CHARS],
        acceptance_criteria=assignment.acceptance,
        outcomes=machine.outcomes,
        actions=gathered.actions,
        artifacts=claim.artifacts,
        files=gathered.files,
        worker_grade=gathered.strength.grade,
        worker_providers=gathered.strength.providers,
        tempo=Tempo.from_wire(assignment.tempo),
        spend_left=gathered.spend_left,
    )


async def grant_calls(trail: PheromoneTrail, grant_id: str) -> tuple[LlmEvent, ...]:
    """Return the newest `llm.call` events recorded under `grant_id`, for the Worker's grade.

    Args:
        trail: The trail the Warden's Fanner lanes record to.
        grant_id: The task's grant; every sub-bee lane stamps it on its calls.

    Returns:
        The matching events, newest first, from the newest CALL_QUERY_LIMIT calls.
    """
    query = TrailQuery(kind=LLM_CALL_KIND, newest_first=True, limit=CALL_QUERY_LIMIT)
    events = await trail.query(query)
    return tuple(
        e for e in events if isinstance(e, LlmEvent) and e.payload.get("grant_id") == grant_id
    )


async def ask_nurse(
    wiring: NurseWiring, request: NurseRequest, round_: int
) -> tuple[NurseReview, float]:
    """Run one Nurse round; an outage is "not nursed", never an error.

    Args:
        wiring: The Warden's Nurse.
        request: The evidence.
        round_: The round, from 1 (TaskAssign.review_round plus one).

    Returns:
        The review and the spend to add to the result's.
    """
    try:
        return await run_nurse(wiring.reviewer, request, round_)
    except _OUTAGES as outage:
        # Nobody could be reached: like no eligible model, accepted and flagged (ADR-0061).
        log.warning("wardens.nurse.outage", error=type(outage).__name__, round=round_)
        return not_nursed(request, round_), 0.0


def not_nursed(request: NurseRequest, round_: int) -> NurseReview:
    """Return the UNAVAILABLE review of a round no Nurse could be asked for.

    Args:
        request: The evidence that was never sent.
        round_: The round, from 1.

    Returns:
        A review with no verdict and no Nurse grade, the Worker's grade recorded.
    """
    return NurseReview(
        status=NurseReviewStatus.UNAVAILABLE, round=round_, worker_grade=request.worker_grade
    )


def nurse_event(
    review: NurseReview, task_id: TaskId, cell_id: CellId, identity: MemoryIdentity, clock: Clock
) -> CappingEvent:
    """Build the trail event recording one round: ids, enums, counts and grades only.

    Args:
        review: The round's review.
        task_id: The task reviewed, the event's subject.
        cell_id: The Cell it ran on, so a Night Veil teardown can drop it with the Cell.
        identity: The Warden's stamp.
        clock: Mints the event's id and time.

    Returns:
        `capping.nurse_unavailable` when no Nurse was chosen, else `capping.nurse_reviewed`.
    """
    payload: dict[str, JsonValue] = {
        "cell_id": cell_id,
        "round": review.round,
        "worker_grade": review.worker_grade,
    }
    kind = UNAVAILABLE_KIND
    if review.status is not NurseReviewStatus.UNAVAILABLE:
        kind = REVIEWED_KIND
        payload |= {"status": review.status.value, "nurse_grade": review.nurse_grade}
    if review.verdict is not None:
        findings = len(review.verdict.findings)
        payload |= {"outcome": review.verdict.outcome.value, "findings": findings}
    return CappingEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor=identity.actor,
        kind=kind,
        subject_id=task_id,
        payload=payload,
    )
