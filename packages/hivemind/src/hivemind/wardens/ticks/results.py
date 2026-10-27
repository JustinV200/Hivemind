"""Handle a sub-bee's claimed TaskResult: run acceptance on the Warden's own session, then report.

Roadmap step 3.18 and the phase's own dispatch map: "TaskResult(CLAIMED) from a sub-bee -> ACCEPT:
`hivemind.wardens.acceptance.run_acceptance` runs every Postcondition through `hivemind.
supervision.capping.check_postcondition` on the WARDEN's own session (the bee that did the work
never verifies its own work: codingrules section 8.12, roadmap 3.18); all hold -> TaskResult
(outcome=SUCCEEDED, checked_by=<warden id>, artifacts, handoff, spend) to the Queen; any fails ->
AlarmRaised(kind=ACCEPTANCE_FAILED, detail=<the failing assertion: kind + target + observed>,
context task/cell/worker ids) to the Queen AND TaskResult(FAILED, reason='acceptance') to the
Queen; sub-bee DONE either way; the local pool slot is released." `handle_accept` is that whole
sequence, and the only place `hivemind.wardens.acceptance.run_acceptance` is ever called from.

ADR-0061 adds the Nurse Bee between "all hold" and SUCCEEDED, on a Warden built with one
(`WardenDeps.nurse`) and for a role it does not skip. The machine checks decide first, leaving out
the JUDGE_RUBRIC criteria only the Nurse can judge (`hivemind.wardens.nurse.machine_report`); when
they pass, the Warden gathers the evidence (the touched files through its own session and the
Guard's scanner) before retiring the bee, then asks the Nurse and records the round on the trail.
A PASS, or no Nurse (UNAVAILABLE) or a failed one (ERROR), reports SUCCEEDED with the review
attached ("not nursed" for the last two); CHANGES_REQUESTED reports FAILED with the review, for the
Queen to fix forward; and a JUDGE_RUBRIC criterion no Nurse judged fails acceptance as before. On a
Night Veil Cell the Nurse is asked only when its JUDGE chain is local, like the task's own binding.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why). Calls into `hivemind.cell` (CellIdentity),
    `hivemind.exoskeleton.surface` (check_structural, for a task's page criteria),
    `hivemind.guard` (warden_principal, the scanner's site), `hivemind.supervision` (Alarm,
    record_alarm_event -- this dispatch's own alarm-reaches-the-trail fix; capping's
    CommandAdmission and CommandGuard, ADR-0060), `hivemind.wardens.acceptance` (run_acceptance),
    `hivemind.wardens.nurse` (the Nurse Bee's accept-path side, ADR-0061),
    `hivemind.wardens.ticks.alarms` (retire_sub_bee, for the shared close-and-release-the-slot
    step) and waggle only.

Key invariants:
    - Acceptance always runs on `warden._session` -- the Warden's own -- never on any session the
      sub-bee itself held (codingrules section 8.12: "the proposer never verifies its own work").
    - A claim from a bee the Warden asked for itself (`SubBee.local`, roadmap 11.9a) is kept on its
      row and never accepted, retired here or reported: the Queen has no such task.
    - `retire_sub_bee` runs exactly once per TaskResult(CLAIMED) handled, whatever the outcome, so
      the local pool slot is released and the sub-bee's link is closed either way; and only after
      acceptance and the Nurse's file reads, so a URL_MATCHES or ELEMENT_TEXT criterion still has
      the task's browser to read; the Nurse's model call comes after it, holding no slot.
    - A device that cannot be asked (`DEVICE_ERRORS`: a closed session, a refused request, a link
      that went) never ends this Warden's loop: nothing was verified, so nothing is accepted, and
      the task is reported FAILED with the error's class as the reason (roadmap 11.6a).
    - `_send_acceptance_failed` records `alarm.raised` for the ACCEPTANCE_FAILED Alarm it raises,
      before sending it to the Queen (this dispatch's own fix 1).
    - Every result sent copies `claim.scout_report` unchanged (roadmap step 6.10): a Scout's
      report reaches the Queen whether its own task's acceptance passed or failed.
    - A failed acceptance carries `ResultCause.CELL_RUNS_NO_COMMANDS` exactly when the claim did
      or a command criterion was refused on this Cell (ADR-0060), so the Queen re-places the
      task rather than retrying it where its commands will be refused again.
    - A Nurse round's spend is added to the result's (ADR-0061), whatever the review said.
    - A Night Veil Cell's files never reach a JUDGE chain that could call a hosted model: that
      round is UNAVAILABLE without a call (roadmap step 10.3a's local-only rule).

See Also:
    - .claude/codingrules.md section 8.12 for "the proposer never verifies its own work".
    - .claude/roadmap.md step 3.18 for the acceptance rule this module enforces, and 3.19's own
      dispatch map for the exact TaskResult/AlarmRaised shapes this module builds.
    - hivemind.wardens.acceptance for run_acceptance and AcceptanceReport, this module's one check.
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the Nurse step.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from hivemind.cell import CellError, CellIdentity, CombShieldLevel, HoneyClearance
from hivemind.exoskeleton.surface import check_structural
from hivemind.forage import ModelSlot
from hivemind.guard import warden_principal
from hivemind.guard.scanner import ScanRecorder, ScanSite, ScanSource
from hivemind.supervision import Alarm as MirroredAlarm
from hivemind.supervision import record_alarm_event
from hivemind.supervision.capping import CommandAdmission, CommandGuard, NurseRequest
from hivemind.wardens.acceptance import AcceptanceReport, GuiCheck, run_acceptance
from hivemind.wardens.deps import device_outage
from hivemind.wardens.links import send_guarded
from hivemind.wardens.nurse import (
    JUDGED_KINDS,
    AttemptEvidence,
    EvidenceReader,
    NurseWiring,
    ask_nurse,
    bee_may_read,
    grant_calls,
    machine_report,
    not_nursed,
    nurse_event,
    nurse_request,
    read_evidence,
    spend_left,
    touched_paths,
    unjudged_report,
    worker_strength,
)
from hivemind.wardens.spawn.binding import binding_is_local
from hivemind.wardens.ticks.alarms import retire_sub_bee
from hivemind.wardens.ticks.trail_ship import ship_trail_before_result
from waggle.envelope import wrap
from waggle.errors import TransportError
from waggle.ids import CellId, new_alarm_id
from waggle.messages import AlarmSeverity
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised
from waggle.messages.task import NurseOutcome, NurseReview, ResultCause, TaskOutcome, TaskResult

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

MAX_DETAIL_CHARS = 2_000  # Mirrors waggle.messages.supervision.alarms.MAX_DETAIL_CHARS's own bound.
MAX_SCAN_REF_CHARS = 200  # A scanned file's locator on a flag: the path's head, never its text.
# What asking a device to check a criterion can fail with: a Cell error (a closed session, a device
# that refused the request or did not answer) or a link that went. Anything else is a real bug.
DEVICE_ERRORS = (CellError, TransportError)

__all__ = ["DEVICE_ERRORS", "MAX_DETAIL_CHARS", "handle_accept"]


@dataclass(frozen=True, slots=True)
class _Nursed:
    """A Nurse round's record and the attempt's spend with the round's cost added."""

    review: NurseReview
    spend: float


async def handle_accept(warden: Warden, sub_bee: SubBee, claim: TaskResult) -> None:
    """Run acceptance on `warden`'s own session and report the verified outcome to the Queen.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        sub_bee: The sub-bee whose claim this is.
        claim: The sub-bee's own TaskResult(CLAIMED), for its summary, artifacts and spend.
    """
    if sub_bee.local is not None:
        # The Warden asked for this task itself: its ending is the Warden's to read, not hers.
        sub_bee.local.claim = claim
        return
    if warden._session is None or warden._cell is None:
        return  # Defensive: unreachable once a sub-bee has spawned on this Warden's own session.
    outage = device_outage(warden._deps)
    if outage is not None:
        # The bee finished its work before it obeyed a pause: this claim, not a resume, is its end.
        outage.forget(sub_bee.task_id)
    # ADR-0060: a command criterion runs only where this Cell may run commands, asked as this
    # Warden, so a refusal is its own guard.denied row.
    guard = CommandGuard(warden._deps.enforcer, warden_principal(warden._warden_id))
    commands = CommandAdmission(warden._cell, guard)
    nurse = _nurse_for(warden, sub_bee)
    request: NurseRequest | None = None
    # Before retiring: retiring detaches the Exoskeleton, and a page criterion reads its browser.
    try:
        report = await run_acceptance(
            warden._session, sub_bee.assignment.acceptance, commands, _gui_check(warden, sub_bee)
        )
        # ADR-0061: the machine decides first, without the criteria only the Nurse can judge.
        machine = report if nurse is None else machine_report(report)
        if nurse is not None and machine.passed:
            request = await _gather(warden, sub_bee, claim, machine, nurse)
    except DEVICE_ERRORS as error:
        # A device that went away mid-check (roadmap 11.6a, 4b): a handled outcome, not a crash
        # of this Warden's loop with the task left RUNNING and nobody to report it.
        await _send_unchecked(warden, sub_bee, claim, error)
        return
    await retire_sub_bee(warden, sub_bee)
    if nurse is not None and request is not None:
        await _nurse_and_report(warden, sub_bee, claim, report, (nurse, request, warden._cell.id))
    elif machine.passed:
        await _send_succeeded(warden, claim)
    else:
        await _send_acceptance_failed(warden, sub_bee, claim, machine)


def _nurse_for(warden: Warden, sub_bee: SubBee) -> NurseWiring | None:
    """Return the Warden's Nurse when it reviews this sub-bee's role, else None (ADR-0061)."""
    nurse = warden._deps.nurse
    return nurse if nurse is not None and nurse.reviews(sub_bee.assignment.role) else None


async def _gather(
    warden: Warden,
    sub_bee: SubBee,
    claim: TaskResult,
    machine: AcceptanceReport,
    nurse: NurseWiring,
) -> NurseRequest:
    """Build the Nurse's request from what this Warden holds, reading files on its own session."""
    assert warden._session is not None  # noqa: S101 - handle_accept checked it before calling.
    trace = sub_bee.trace
    actions = trace.actions()
    reader = EvidenceReader(
        session=warden._session,
        scanner=warden._deps.scanner,
        site=partial(_scan_site, warden, sub_bee),
        max_chars=nurse.max_evidence_chars,
    )
    # A claimed artifact is the Worker's word: read only what the bee could have read itself.
    may_read = partial(bee_may_read, sub_bee.capabilities, warden._session.scratch_dir)
    files = await read_evidence(reader, touched_paths(actions, claim.artifacts, may_read))
    # The Worker's grade: every binding the attempt ran on, plus any call those cannot explain.
    calls = await grant_calls(warden._deps.trail, sub_bee.assignment.grant_id)
    strength = worker_strength(trace.bindings, calls, nurse.forage)
    left = spend_left(warden._grants.get(sub_bee.assignment.grant_id), claim.spend)
    gathered = AttemptEvidence(actions=actions, files=files, strength=strength, spend_left=left)
    return nurse_request(sub_bee.assignment, claim, machine, gathered)


def _scan_site(warden: Warden, sub_bee: SubBee, path: str) -> ScanSite:
    """Build the scan site for one touched file: this Warden reads it, at its Cell's tier."""
    identity = warden._deps.identity
    recorder = ScanRecorder(
        trail=warden._deps.trail,
        identity=CellIdentity(
            hive_id=identity.hive_id, node_id=identity.node_id, actor=identity.actor
        ),
        clock=warden._deps.clock,
    )
    assert warden._cell is not None  # noqa: S101 - handle_accept checked it before reading.
    return ScanSite(
        source=ScanSource.SESSION_OUTPUT,
        consumer=str(warden._warden_id),
        recorder=recorder,
        tier=warden._cell.comb_shield,
        targets=sub_bee.capabilities,  # The hosts the task may reach are not outside hosts.
        task_id=sub_bee.task_id,
        ref=path[:MAX_SCAN_REF_CHARS],
    )


async def _nurse_and_report(
    warden: Warden,
    sub_bee: SubBee,
    claim: TaskResult,
    report: AcceptanceReport,
    asked: tuple[NurseWiring, NurseRequest, CellId],
) -> None:
    """Ask the Nurse, record the round, and report what its review decides (module docstring)."""
    nurse, request, cell_id = asked
    round_ = sub_bee.assignment.review_round + 1
    if _veiled_from_the_nurse(warden):
        review, cost = not_nursed(request, round_), 0.0  # The evidence never leaves the machine.
    else:
        review, cost = await ask_nurse(nurse, request, round_)
    deps = warden._deps
    event = nurse_event(review, claim.task_id, cell_id, deps.identity, deps.clock)
    await deps.trail.record(event)
    nursed = _Nursed(review=review, spend=claim.spend + cost)
    verdict = review.verdict
    if verdict is not None and verdict.outcome is NurseOutcome.CHANGES_REQUESTED:
        await _send_rejected(warden, claim, nursed)
    elif verdict is None and any(o.kind in JUDGED_KINDS for o in report.outcomes):
        # A criterion only a Nurse can judge, and none did: it fails closed, as before ADR-0061.
        failed = unjudged_report(report, review)
        await _send_acceptance_failed(warden, sub_bee, claim, failed, nursed)
    else:
        await _send_succeeded(warden, claim, nursed)


def _veiled_from_the_nurse(warden: Warden) -> bool:
    """Whether a Night Veil Cell's evidence would reach a hosted model through the JUDGE chain.

    Roadmap step 10.3a binds a Night Veil task to local models only; the Nurse reads that task's
    files, so its chain is held to the same rule (`binding_is_local`, failing closed on a chain it
    cannot follow), and a chain that could reach a hosted model is never asked.
    """
    cell = warden._cell
    veiled = cell is not None and cell.comb_shield is CombShieldLevel.NIGHT_VEIL
    return veiled and not binding_is_local(ModelSlot.JUDGE.manifest_key, warden._deps)


async def _send_unchecked(
    warden: Warden, sub_bee: SubBee, claim: TaskResult, error: Exception
) -> None:
    """Report the claim FAILED because the Cell could not be asked to check it.

    Nothing was verified, so nothing is accepted (codingrules 8.12): the Queen retries or
    re-places the task, as for any FAILED result, and the swarm gate's own `CELL_UNREACHABLE`
    Alarm says why the Cell cannot be asked. The error's class is named; its text may hold a path.
    """
    await retire_sub_bee(warden, sub_bee)
    result = TaskResult(
        task_id=claim.task_id,
        attempt=claim.attempt,
        outcome=TaskOutcome.FAILED,
        summary=claim.summary,
        clearance=claim.clearance,
        artifacts=claim.artifacts,
        checked_by=warden._warden_id,
        handoff=claim.handoff,
        spend=claim.spend,
        reason=f"acceptance could not be checked: {type(error).__name__}",
        scout_report=claim.scout_report,
    )
    await ship_trail_before_result(warden)
    await _send_to_queen(warden, result)


def _gui_check(warden: Warden, sub_bee: SubBee) -> GuiCheck | None:
    """Check GUI criteria on the Exoskeleton this Warden attached for `sub_bee`, if any."""
    handle = sub_bee.exoskeleton
    if handle is None:
        return None
    return partial(check_structural, handle.peripherals, warden._deps.clock)


async def _send_succeeded(warden: Warden, claim: TaskResult, nursed: _Nursed | None = None) -> None:
    """Report the sub-bee's claim as SUCCEEDED, verified by this Warden (and nursed, if asked)."""
    reason = "Every acceptance criterion held."
    if nursed is not None:
        reason = f"Every acceptance criterion held; Nurse review {nursed.review.status.value}."
    result = _verified(warden, claim, TaskOutcome.SUCCEEDED, reason, nursed)
    # The Queen may pause or destroy this Cell the moment the result lands: ship the task's own
    # trail rows first, over the same ordered link (hivemind.wardens.ticks.trail_ship).
    await ship_trail_before_result(warden)
    # Codingrules 8.8: a result queues while the Warden is disconnected. A Warden that dials the
    # Queen holds a reconnecting link that does so (`hivemind.wardens.offline`); on a link that is
    # closed for good the send is logged and dropped. This sub-bee is already retired either way.
    await _send_to_queen(warden, result)


async def _send_rejected(warden: Warden, claim: TaskResult, nursed: _Nursed) -> None:
    """Report the claim FAILED because the Nurse asked for changes; the Queen fixes forward."""
    result = _verified(warden, claim, TaskOutcome.FAILED, "nurse", nursed)
    await ship_trail_before_result(warden)  # Same reason as _send_succeeded: teardown may follow.
    await _send_to_queen(warden, result)


def _verified(
    warden: Warden, claim: TaskResult, outcome: TaskOutcome, reason: str, nursed: _Nursed | None
) -> TaskResult:
    """Build this Warden's verified result for `claim`, with the Nurse's review and spend."""
    return TaskResult(
        task_id=claim.task_id,
        attempt=claim.attempt,
        outcome=outcome,
        summary=claim.summary,
        clearance=claim.clearance,
        artifacts=claim.artifacts,
        checked_by=warden._warden_id,
        handoff=claim.handoff,
        spend=claim.spend if nursed is None else nursed.spend,
        reason=reason,
        scout_report=claim.scout_report,
        review=None if nursed is None else nursed.review,
    )


async def _send_to_queen(warden: Warden, payload: AlarmRaised | TaskResult) -> bool:
    """Wrap `payload` and send it over this Warden's own queen link; see `send_guarded`."""
    envelope = wrap(payload, warden._deps.hop, clock=warden._deps.clock)
    return await send_guarded(warden._deps.queen_link, envelope)


async def _send_acceptance_failed(
    warden: Warden,
    sub_bee: SubBee,
    claim: TaskResult,
    report: AcceptanceReport,
    nursed: _Nursed | None = None,
) -> None:
    """Raise ACCEPTANCE_FAILED with the failing assertion, then report FAILED, both to the Queen."""
    alarm = _acceptance_alarm(warden, sub_bee, claim, report)
    # This Warden raises ACCEPTANCE_FAILED itself (this dispatch's own fix 1: the same rule
    # hivemind.wardens.ticks.heartbeat.raise_stalled_alarms follows for WORKER_STALLED).
    mirrored = MirroredAlarm.from_wire(alarm)
    identity = _cell_identity(warden)
    await record_alarm_event(
        warden._deps.trail, identity, warden._deps.clock, mirrored, "alarm.raised"
    )
    # Queued durably by a reconnecting link while cut off, like _send_succeeded's own send.
    await _send_to_queen(warden, alarm)
    result = TaskResult(
        task_id=claim.task_id,
        attempt=claim.attempt,
        outcome=TaskOutcome.FAILED,
        summary=claim.summary,
        clearance=claim.clearance,
        artifacts=claim.artifacts,
        checked_by=warden._warden_id,
        handoff=claim.handoff,
        spend=claim.spend if nursed is None else nursed.spend,
        reason="acceptance",
        scout_report=claim.scout_report,
        cause=_failure_cause(claim, report),
        review=None if nursed is None else nursed.review,
    )
    await ship_trail_before_result(warden)  # Same reason as _send_succeeded: teardown follows.
    await _send_to_queen(warden, result)


def _acceptance_alarm(
    warden: Warden, sub_bee: SubBee, claim: TaskResult, report: AcceptanceReport
) -> AlarmRaised:
    """Build the ACCEPTANCE_FAILED Alarm naming the first failing assertion."""
    failing = report.failing[0]
    detail = f"{failing.kind.value} did not hold: {failing.observed}"[:MAX_DETAIL_CHARS]
    return AlarmRaised(
        alarm_id=new_alarm_id(warden._deps.clock),
        kind=AlarmKind.ACCEPTANCE_FAILED,
        severity=AlarmSeverity.WARNING,
        origin=warden._warden_id,
        attempts=0,
        raised_at=warden._deps.clock.now(),
        context=AlarmContext(
            task_id=claim.task_id,
            cell_id=warden._cell.id if warden._cell is not None else None,
            worker_id=sub_bee.worker_id,
            event_id=None,
            handoff=None,
        ),
        detail=detail,
        clearance=HoneyClearance.C1.to_wire(),
        reason="A sub-bee's claimed work failed its own task's acceptance criteria.",
    )


def _failure_cause(claim: TaskResult, report: AcceptanceReport) -> ResultCause | None:
    """Name the typed cause a failed acceptance reports: the Cell refused the task's commands.

    ADR-0060: the Worker's own claim says so when the Cell refused one of its commands, and so
    does a command criterion this Warden was refused; either way a retry on this Cell would meet
    the same refusal, so the Queen re-places the task instead.
    """
    if claim.cause is not None:
        return claim.cause
    if any(outcome.commands_refused for outcome in report.failing):
        return ResultCause.CELL_RUNS_NO_COMMANDS
    return None


def _cell_identity(warden: Warden) -> CellIdentity:
    """Build the CellIdentity `_send_acceptance_failed`'s own alarm.raised event is stamped with."""
    identity = warden._deps.identity
    return CellIdentity(hive_id=identity.hive_id, node_id=identity.node_id, actor=identity.actor)
