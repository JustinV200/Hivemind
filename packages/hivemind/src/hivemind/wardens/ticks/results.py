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
    assign`'s own module docstring for why). Calls into `hivemind.wardens.acceptance`
    (run_acceptance), `hivemind.wardens.ticks.alarms` (retire_sub_bee, for the shared
    close-and-release-the-slot step) and waggle only.

Key invariants:
    - Acceptance always runs on `warden._session` -- the Warden's own -- never on any session the
      sub-bee itself held (codingrules section 8.12: "the proposer never verifies its own work").
    - `retire_sub_bee` runs exactly once per TaskResult(CLAIMED) handled, whatever the outcome, so
      the local pool slot is released and the sub-bee's link is closed either way.

See Also:
    - .claude/codingrules.md section 8.12 for "the proposer never verifies its own work".
    - .claude/roadmap.md step 3.18 for the acceptance rule this module enforces, and 3.19's own
      dispatch map for the exact TaskResult/AlarmRaised shapes this module builds.
    - hivemind.wardens.acceptance for run_acceptance and AcceptanceReport, this module's one check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.wardens.acceptance import AcceptanceReport, run_acceptance
from hivemind.wardens.ticks.alarms import retire_sub_bee
from waggle.envelope import wrap
from waggle.ids import new_alarm_id
from waggle.messages import AlarmSeverity
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised
from waggle.messages.task import TaskOutcome, TaskResult

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

MAX_DETAIL_CHARS = 2_000  # Mirrors waggle.messages.supervision.alarms.MAX_DETAIL_CHARS's own bound.

__all__ = ["MAX_DETAIL_CHARS", "handle_accept"]


async def handle_accept(warden: Warden, sub_bee: SubBee, claim: TaskResult) -> None:
    """Run acceptance on `warden`'s own session and report the verified outcome to the Queen.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        sub_bee: The sub-bee whose claim this is.
        claim: The sub-bee's own TaskResult(CLAIMED), for its summary, artifacts and spend.
    """
    if warden._session is None:
        return  # Defensive: unreachable once a sub-bee has spawned on this Warden's own session.
    report = await run_acceptance(warden._session, sub_bee.assignment.acceptance)
    await retire_sub_bee(warden, sub_bee)
    if report.passed:
        await _send_succeeded(warden, sub_bee, claim)
        return
    await _send_acceptance_failed(warden, sub_bee, claim, report)


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
    )
    await warden._deps.queen_link.send(wrap(result, warden._deps.hop, clock=warden._deps.clock))


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
    await warden._deps.queen_link.send(wrap(alarm, warden._deps.hop, clock=warden._deps.clock))
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
    )
    await warden._deps.queen_link.send(wrap(result, warden._deps.hop, clock=warden._deps.clock))
