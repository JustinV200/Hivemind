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

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why). Calls into `hivemind.cell` (CellIdentity),
    `hivemind.exoskeleton.surface` (check_structural, for a task's page criteria),
    `hivemind.guard` (warden_principal), `hivemind.supervision` (Alarm, record_alarm_event --
    this dispatch's own alarm-reaches-the-trail fix; capping's CommandAdmission and CommandGuard,
    ADR-0060), `hivemind.wardens.acceptance` (run_acceptance), `hivemind.wardens.ticks.alarms`
    (retire_sub_bee, for the shared close-and-release-the-slot step) and waggle only.

Key invariants:
    - Acceptance always runs on `warden._session` -- the Warden's own -- never on any session the
      sub-bee itself held (codingrules section 8.12: "the proposer never verifies its own work").
    - A claim from a bee the Warden asked for itself (`SubBee.local`, roadmap 11.9a) is kept on its
      row and never accepted, retired here or reported: the Queen has no such task.
    - `retire_sub_bee` runs exactly once per TaskResult(CLAIMED) handled, whatever the outcome, so
      the local pool slot is released and the sub-bee's link is closed either way; and only after
      acceptance, so a URL_MATCHES or ELEMENT_TEXT criterion still has the task's browser to read.
    - A device that cannot be asked (`DEVICE_ERRORS`: a closed session, a refused request, a link
      that went) never ends this Warden's loop: nothing was verified, so nothing is accepted, and
      the task is reported FAILED with the error's class as the reason (roadmap 11.6a).
    - `_send_acceptance_failed` records `alarm.raised` for the ACCEPTANCE_FAILED Alarm it raises,
      before sending it to the Queen (this dispatch's own fix 1).
    - Both `_send_succeeded` and `_send_acceptance_failed` copy `claim.scout_report` onto the
      TaskResult they send unchanged (roadmap step 6.10): a Scout's report reaches the Queen
      whether its own task's acceptance (FILE_EXISTS on `SCOUT_REPORT_FILE`) passed or failed.
    - A failed acceptance carries `ResultCause.CELL_RUNS_NO_COMMANDS` exactly when the claim did
      or a command criterion was refused on this Cell (ADR-0060), so the Queen re-places the
      task rather than retrying it where its commands will be refused again.

See Also:
    - .claude/codingrules.md section 8.12 for "the proposer never verifies its own work".
    - .claude/roadmap.md step 3.18 for the acceptance rule this module enforces, and 3.19's own
      dispatch map for the exact TaskResult/AlarmRaised shapes this module builds.
    - hivemind.wardens.acceptance for run_acceptance and AcceptanceReport, this module's one check.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from hivemind.cell import CellError, CellIdentity, HoneyClearance
from hivemind.exoskeleton.surface import check_structural
from hivemind.guard import warden_principal
from hivemind.supervision import Alarm as MirroredAlarm
from hivemind.supervision import record_alarm_event
from hivemind.supervision.capping import CommandAdmission, CommandGuard
from hivemind.wardens.acceptance import AcceptanceReport, GuiCheck, run_acceptance
from hivemind.wardens.deps import device_outage
from hivemind.wardens.links import send_guarded
from hivemind.wardens.ticks.alarms import retire_sub_bee
from hivemind.wardens.ticks.trail_ship import ship_trail_before_result
from waggle.envelope import wrap
from waggle.errors import TransportError
from waggle.ids import new_alarm_id
from waggle.messages import AlarmSeverity
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised
from waggle.messages.task import ResultCause, TaskOutcome, TaskResult

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

MAX_DETAIL_CHARS = 2_000  # Mirrors waggle.messages.supervision.alarms.MAX_DETAIL_CHARS's own bound.
# What asking a device to check a criterion can fail with: a Cell error (a closed session, a device
# that refused the request or did not answer) or a link that went. Anything else is a real bug.
DEVICE_ERRORS = (CellError, TransportError)

__all__ = ["DEVICE_ERRORS", "MAX_DETAIL_CHARS", "handle_accept"]


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
    # Before retiring: retiring detaches the Exoskeleton, and a page criterion reads its browser.
    try:
        report = await run_acceptance(
            warden._session, sub_bee.assignment.acceptance, commands, _gui_check(warden, sub_bee)
        )
    except DEVICE_ERRORS as error:
        # A device that went away mid-check (roadmap 11.6a, 4b): a handled outcome, not a crash
        # of this Warden's loop with the task left RUNNING and nobody to report it.
        await _send_unchecked(warden, sub_bee, claim, error)
        return
    await retire_sub_bee(warden, sub_bee)
    if report.passed:
        await _send_succeeded(warden, sub_bee, claim)
        return
    await _send_acceptance_failed(warden, sub_bee, claim, report)


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


async def _send_succeeded(warden: Warden, sub_bee: SubBee, claim: TaskResult) -> None:
    """Report the sub-bee's claim as SUCCEEDED, verified by this Warden."""
    result = TaskResult(
        task_id=claim.task_id,
        attempt=claim.attempt,
        outcome=TaskOutcome.SUCCEEDED,
        summary=claim.summary,
        clearance=claim.clearance,
        artifacts=claim.artifacts,
        checked_by=warden._warden_id,
        handoff=claim.handoff,
        spend=claim.spend,
        reason="Every acceptance criterion held.",
        scout_report=claim.scout_report,
    )
    # The Queen may pause or destroy this Cell the moment the result lands: ship the task's own
    # trail rows first, over the same ordered link (hivemind.wardens.ticks.trail_ship).
    await ship_trail_before_result(warden)
    # Codingrules 8.8: a result queues while the Warden is disconnected. A Warden that dials the
    # Queen holds a reconnecting link that does so (`hivemind.wardens.offline`); on a link that is
    # closed for good the send is logged and dropped. This sub-bee is already retired either way.
    await _send_to_queen(warden, result)


async def _send_to_queen(warden: Warden, payload: AlarmRaised | TaskResult) -> bool:
    """Wrap `payload` and send it over this Warden's own queen link; see `send_guarded`."""
    envelope = wrap(payload, warden._deps.hop, clock=warden._deps.clock)
    return await send_guarded(warden._deps.queen_link, envelope)


async def _send_acceptance_failed(
    warden: Warden, sub_bee: SubBee, claim: TaskResult, report: AcceptanceReport
) -> None:
    """Raise ACCEPTANCE_FAILED with the failing assertion, then report FAILED, both to the Queen."""
    failing = report.failing[0]
    detail = f"{failing.kind.value} did not hold: {failing.observed}"[:MAX_DETAIL_CHARS]
    alarm = AlarmRaised(
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
        spend=claim.spend,
        reason="acceptance",
        scout_report=claim.scout_report,
        cause=_failure_cause(claim, report),
    )
    await ship_trail_before_result(warden)  # Same reason as _send_succeeded: teardown follows.
    await _send_to_queen(warden, result)


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
