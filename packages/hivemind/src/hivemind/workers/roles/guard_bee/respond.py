"""Turn one Guard Bee finding into a GuardReport, carry out what it asks, and record it on trail.

Every finding becomes a `GuardReport` (the rule, the events it cites oldest first, the Cell, bees,
tasks and grants it touches, the recommended action and a confidence), a `C2` deposit through the
`GuardReportSink` seam, and a `guard.alert` trail event carrying ids, the rule, the action, the
confidence, the counts and what became of it (ADR-0035). The Guard Bee acts alone only to narrow the
whole Hive: `raise_audit_rate` records `guard.audit_rate_raised` (the tier's rate in force, raised
by `[guard.bee] audit_raise_step`, capped at 1.0, for `audit_raise_hold_s`), which the Hive Stand's
Capping gates read back from this same trail; `reduce_entrance` records `guard.reduce_ordered`,
which the Hive Entrance follows. A report aimed at one Cell or one bee is a request filed through
the Queen's `GuardRequestDoor` when `RequestLedger` admits it, and only a `guard.alert` otherwise.
A recommendation whose target the finding does not name falls back to `observe`, never to a guess.
A report at CRITICAL confidence that asks for nothing (a raise, a reduce order, an observation) is
also shown to the human through the door's `report_to_human` (ADR-0035); a CRITICAL request is
not, because the Queen shows it herself, once, with her decision. The order is fixed: act (file,
raise or order), show, deposit, then record the alert that marks the finding reported, so a crash
in between repeats an action (a narrowing is safe to repeat, a duplicate request is still only a
request) rather than losing one. `FindingResponder` also keeps
what it reported (per rule and key, the moment each finding counted up to), and rebuilds that and
its ledger from the Guard Bee's own alerts after a restart.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Owned by
    `.bee.GuardBee`. Calls into `hivemind.cell` (CellIdentity), `hivemind.guard` (GuardAction,
    GuardReport, GuardRequestDoor, new_guard_report_id), `hivemind.pheromone` (GuardEvent,
    PheromoneTrail), `hivemind.supervision.capping` (AuditRateRaise, RiskTier, TierTable,
    raised_audit_rate) and this package's `.evaluate`, `.judge`, `.requests`, `.rules`, `.sink`.

Key invariants:
    - The only kinds recorded here are `guard.alert`, `guard.audit_rate_raised` and
      `guard.reduce_ordered`, and a raise only ever raises: nothing here can widen anything.
    - Every payload holds ids, enum values, numbers and one timestamp; the summary is built from
      the rule's title, ids and counts only.
    - Only a request, or a CRITICAL report that asks for nothing, goes through the Queen's door;
      nothing here holds a Cell or a Waggle link.

See Also:
    - hivemind.guard.report for GuardReport and GuardRequestDoor, the contract.
    - hivemind.entrance.streams.orders for the follower that obeys `guard.reduce_ordered`.
    - hivemind.wardens.spawn.audited_gate for the gate that samples at a raised rate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import JsonValue

from hivemind.cell import CellIdentity
from hivemind.guard import (
    GuardAction,
    GuardConfidence,
    GuardReport,
    GuardRequestDoor,
    new_guard_report_id,
)
from hivemind.guard.report import MAX_CITED_EVENTS, MAX_SUMMARY_CHARS
from hivemind.pheromone import GuardEvent, PheromoneEvent, PheromoneTrail
from hivemind.supervision.capping import (
    AUDIT_RATE_RAISED_KIND,
    AuditRateRaise,
    RiskTier,
    TierTable,
    raised_audit_rate,
)
from hivemind.workers.roles.guard_bee.evaluate import Finding, Mark, allowed_actions, targets_of
from hivemind.workers.roles.guard_bee.judge import Verdict
from hivemind.workers.roles.guard_bee.requests import Disposition, RequestLedger, request_target
from hivemind.workers.roles.guard_bee.rules import NARROWING_ACTIONS, GroupKey, Measure, RuleShape
from hivemind.workers.roles.guard_bee.sink import GuardDeposit, GuardReportSink
from hivemind.workers.roles.guard_bee.watch import ALERT_KIND
from waggle.clock import Clock
from waggle.ids import new_event_id

REDUCE_ORDERED_KIND = "guard.reduce_ordered"  # What the Entrance follows to reduce itself.
_TIERS = {tier.value: tier for tier in RiskTier}  # A raise names a tier by its value.

__all__ = [
    "REDUCE_ORDERED_KIND",
    "AuditRaise",
    "FindingResponder",
    "ResponderDeps",
    "Response",
    "build_report",
]


@dataclass(frozen=True, slots=True)
class AuditRaise:
    """How one raise lifts a Capping tier's sampled-audit rate: from the table, by how much."""

    tiers: TierTable  # The Hive's own tier table: the rates in force before any raise.
    step: float  # [guard.bee] audit_raise_step.
    hold_s: float  # [guard.bee] audit_raise_hold_s.


@dataclass(frozen=True, slots=True)
class ResponderDeps:
    """What carrying out a finding needs: the trail, the Queen's door and the C2 seam."""

    trail: PheromoneTrail  # The Queen's trail: every alert, raise and order lands here.
    clock: Clock  # Stamps every report and event.
    identity: CellIdentity  # The Hive, the Queen's node and the actor the events carry.
    door: GuardRequestDoor  # The Queen's door for a request: the one way a report reaches her.
    sink: GuardReportSink  # Where every report is deposited as C2 Nectar.
    audit: AuditRaise  # How a raise is sized.


@dataclass(frozen=True, slots=True)
class Response:
    """One finding answered: its report, and what became of it."""

    report: GuardReport
    disposition: Disposition


def build_report(finding: Finding, verdict: Verdict, clock: Clock) -> GuardReport:
    """Build the report for `finding` on `verdict`, falling back to observe without a target.

    Args:
        finding: The rule's finding.
        verdict: The rule's own verdict, or a judge's change to it.
        clock: Mints the report's id and time.

    Returns:
        A valid GuardReport citing at most `MAX_CITED_EVENTS` events, oldest first.
    """
    targets = targets_of(finding)
    feasible = allowed_actions(targets) | NARROWING_ACTIONS
    action = verdict.action if verdict.action in feasible else GuardAction.OBSERVE
    cited = tuple(sighting.fact.event_id for sighting in finding.sightings)
    return GuardReport(
        id=new_guard_report_id(clock),
        rule=finding.rule.key,
        event_ids=cited[:MAX_CITED_EVENTS],
        cell_id=targets.cell,
        bee_ids=targets.bees,
        task_ids=targets.tasks,
        grant_ids=targets.grants,
        recommended=action,
        confidence=verdict.confidence,
        filed_at=clock.now(),
        summary=_summary(finding, action, verdict.confidence),
    )


class FindingResponder:
    """Answer findings, and remember what was reported so a burst is answered once.

    Owns its own mutable state (codingrules 8.5): the consumption marks and the request ledger
    change only through `respond` and `restore`.
    """

    def __init__(self, deps: ResponderDeps, ledger: RequestLedger) -> None:
        """Build a responder that has reported nothing yet.

        Args:
            deps: The trail, the Queen's door, the C2 seam and the raise sizing.
            ledger: The request limits: floor, coalescing and the hourly cap.
        """
        self._deps = deps
        self._ledger = ledger
        self._consumed: dict[tuple[str, str], Mark] = {}

    @property
    def consumed(self) -> Mapping[tuple[str, str], Mark]:
        """Per rule and key, how far the findings reported so far counted."""
        return self._consumed

    async def respond(self, finding: Finding, verdict: Verdict) -> Response:
        """Report `finding`: act on it, deposit it, record its alert, and mark it consumed.

        Args:
            finding: The rule's finding.
            verdict: The rule's own verdict, or a judge's change to it.

        Returns:
            The report and what became of it.
        """
        report = build_report(finding, verdict, self._deps.clock)
        disposition = await self._carry_out(report, finding.key)
        shown = await self._show(report)
        await self._deps.sink.deposit(GuardDeposit(report=report))
        payload = _alert(report, finding, verdict, disposition)
        await self._record(
            ALERT_KIND, _subject(report, finding, self._deps.identity), payload | {"shown": shown}
        )
        self._mark(finding.rule.key, finding.key, finding.mark)
        return Response(report=report, disposition=disposition)

    def restore(self, alerts: Iterable[PheromoneEvent]) -> None:
        """Rebuild the consumption marks and the ledger from the Guard Bee's own alerts.

        Args:
            alerts: Its own `guard.alert` events, as the rebuild read them back.
        """
        for alert in alerts:
            rule, key = alert.payload.get("rule"), alert.payload.get("key")
            mark = _mark_of(alert.payload)
            if not (isinstance(rule, str) and isinstance(key, str) and mark is not None):
                continue  # Not an alert this module wrote: never trusted.
            self._mark(rule, key, mark)
            target = alert.payload.get("target")
            filed = alert.payload.get("disposition") == Disposition.FILED.value
            if filed and isinstance(target, str):
                self._ledger.restore(rule, target, alert.at)

    async def _carry_out(self, report: GuardReport, key: str) -> Disposition:
        """File, raise, order or only observe, by what the report recommends."""
        if report.is_request:
            return await self._file(report)
        if report.recommended is GuardAction.RAISE_AUDIT_RATE:
            return await self._raise(report, key)
        if report.recommended is GuardAction.REDUCE_ENTRANCE:
            # The order is the action: the Entrance follows the trail and reduces itself.
            order: dict[str, JsonValue] = {"rule": report.rule, "report_id": report.id}
            await self._record(REDUCE_ORDERED_KIND, self._deps.identity.hive_id, order)
            return Disposition.REDUCE_ORDERED
        return Disposition.OBSERVED

    async def _show(self, report: GuardReport) -> bool:
        """Show a CRITICAL report that asks for nothing to the human; True when it was shown."""
        if report.confidence is not GuardConfidence.CRITICAL or report.is_request:
            # Below CRITICAL the alert is the record; a CRITICAL request is shown by the Queen's
            # own decision on it, so showing it here too would tell the human twice.
            return False
        # Latency: one durable write into the Queen's own tables (the Alarm and its chat line).
        await self._deps.door.report_to_human(report)
        return True

    async def _file(self, report: GuardReport) -> Disposition:
        """File a request through the Queen's door when the ledger admits it."""
        now = self._deps.clock.now()
        disposition = self._ledger.admit(report, now)
        if disposition is Disposition.FILED:
            # Latency: one durable write into the Queen's own store; she decides on her tick.
            await self._deps.door.file_guard_request(report)
            self._ledger.record(report, now)
        return disposition

    async def _raise(self, report: GuardReport, key: str) -> Disposition:
        """Raise the key's tier by one step over the rate in force, capped at 1.0."""
        tier = _TIERS.get(key)
        if tier is None:
            return Disposition.OBSERVED  # A tier the Hive does not know: nothing to raise.
        audit, now = self._deps.audit, self._deps.clock.now()
        spec = audit.tiers.tiers.get(tier)
        table_rate = spec.audit_rate if spec is not None else 0.0
        current = max(table_rate, await raised_audit_rate(self._deps.trail, tier, now))
        if current >= 1.0:
            return Disposition.AT_CEILING
        raised = AuditRateRaise(
            tier=tier,
            from_rate=current,
            to_rate=min(1.0, current + audit.step),
            until=now + timedelta(seconds=audit.hold_s),
            report_id=report.id,
            rule=report.rule,
        )
        await self._record(AUDIT_RATE_RAISED_KIND, self._deps.identity.hive_id, raised.to_payload())
        return Disposition.RAISED

    async def _record(self, kind: str, subject: str, payload: dict[str, JsonValue]) -> None:
        """Record one guard.* event on the Queen's trail, as the Queen's node."""
        identity, clock = self._deps.identity, self._deps.clock
        event = GuardEvent(
            id=new_event_id(clock),
            hive_id=identity.hive_id,
            node_id=identity.node_id,
            at=clock.now(),
            actor=identity.actor,
            kind=kind,
            subject_id=subject,
            payload=payload,
        )
        # Latency: one local trail write; the record exists before the round moves on.
        await self._deps.trail.record(event)

    def _mark(self, rule: str, key: str, mark: Mark) -> None:
        """Consume what `mark` covers for one rule and key, on top of what was already."""
        known = self._consumed.get((rule, key))
        self._consumed[(rule, key)] = mark if known is None else known.merge(mark)


def _alert(
    report: GuardReport, finding: Finding, verdict: Verdict, disposition: Disposition
) -> dict[str, JsonValue]:
    """Build the `guard.alert` payload: ids, the rule, the verdict, the counts, the outcome."""
    rule = finding.rule
    payload: dict[str, JsonValue] = {
        "report_id": report.id,
        "rule": report.rule,
        "action": report.recommended.value,
        "confidence": report.confidence.value,
        "disposition": disposition.value,
        "group": rule.group_by.value,
        "key": finding.key,
        "target": request_target(report),
        "measure": finding.measure,
        "threshold": rule.threshold,
        "window_s": rule.window_s,
        "through": finding.through.isoformat(),
        "through_ids": [*sorted(finding.mark.ids)],
        "event_ids": list(report.event_ids),
        "judged": verdict.judged,
    }
    optional: dict[str, JsonValue] = {
        "base": finding.base,
        "cell_id": report.cell_id,
        "bee_ids": list(report.bee_ids) or None,
        "task_ids": list(report.task_ids) or None,
        "grant_ids": list(report.grant_ids) or None,
    }
    payload.update({name: value for name, value in optional.items() if value is not None})
    if verdict.judged:
        # What the rule alone would have said, so a reader sees what the judge changed.
        payload["rule_action"] = rule.action.value
        payload["rule_confidence"] = rule.confidence.value
    return payload


def _mark_of(payload: Mapping[str, JsonValue]) -> Mark | None:
    """Read an alert's mark back (its `through` and `through_ids`), or None when malformed."""
    through, ids = payload.get("through"), payload.get("through_ids", [])
    if not isinstance(through, str) or not isinstance(ids, list):
        return None
    try:
        moment = datetime.fromisoformat(through)
    except ValueError:
        return None  # Malformed: the alert is skipped rather than half-trusted.
    if moment.tzinfo is None:
        return None  # Trail times are always zoned; this one was not written here.
    return Mark(through=moment, ids=frozenset(str(value) for value in ids))


def _subject(report: GuardReport, finding: Finding, identity: CellIdentity) -> str:
    """Return what an alert is about: its Cell, bee, task or device, else the Hive itself."""
    for candidate in (report.cell_id, *report.bee_ids, *report.task_ids):
        if candidate is not None:
            return candidate
    if finding.rule.group_by is GroupKey.DEVICE:
        return finding.key
    return identity.hive_id


def _summary(finding: Finding, action: GuardAction, confidence: GuardConfidence) -> str:
    """Write one sentence for the human from the rule's title, the key and the counts."""
    rule = finding.rule
    text = (
        f"{rule.title}: {_measured(finding)} for {rule.group_by.value} {finding.key} within "
        f"{rule.window_s:g}s; recommends {action.value} ({confidence.value})."
    )
    return text[:MAX_SUMMARY_CHARS]


def _measured(finding: Finding) -> str:
    """Say what the rule measured, in the words of its shape."""
    rule = finding.rule
    if rule.shape is RuleShape.RATIO:
        return f"{len(finding.sightings)} of {finding.base} ({finding.measure:.0%})"
    if rule.shape is RuleShape.SEQUENCE:
        return f"{finding.measure:g} after its first {rule.first[0].kind}"
    if rule.distinct is not None:
        return f"{finding.measure:g} distinct {rule.distinct.value}s"
    if rule.measure is Measure.COST_USD:
        return f"${finding.measure:.2f} spent"
    return f"{finding.measure:g} events"
