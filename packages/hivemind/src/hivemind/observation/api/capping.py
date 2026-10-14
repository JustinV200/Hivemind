"""Build the Capping view: proposals by tier and state, verdicts, rollbacks and audit rates.

Nothing lands unchecked (codingrules 8.12): every proposal climbs propose, check, cap, apply and
verify, and each hop is a ``capping.*`` trail event keyed by the proposal's id; a sampled audit adds
a ``capping.audited`` event. The gate keeps its proposal table in the Queen's memory, so the trail
is the one place the Observation Hive can read the whole story, and this module folds it: one row
per proposal with the state its newest event reached, the verdict it earned (and the check that
failed it, as a code, never the reviewer's prose, which is not on the trail), how a rollback
restored it, and per risk tier how much was audited and how much of that the judge rejected. Every
row carries the bee that proposed it (the recording actor) and its task, so the UI can link to the
bee's thoughts.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.api``. Built from the
    trail's ``capping`` family by ``hivemind.entrance.reads.screens.capping``; its rows are also
    what a Cell page shows as the Cell's Capping activity. Pure: calls into the proposal state and
    judge enums and pydantic.

Key invariants:
    - Pure: events are handed in; the newest ``MAX_ROWS`` of each list are kept.
    - An event of a kind this fold does not know (including ``capping.summary``) is ignored.
    - No field holds a reviewer's reasons or a proposal's action text: codes, ids and times.

See Also:
    - hivemind.cli.capping.queue for the command-line read of the same trail.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hivemind.pheromone import PheromoneEvent
from hivemind.supervision.capping import JudgeOutcome, ProposalState, RiskTier, is_terminal
from waggle.messages.base import CellIdField, TaskIdField

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

MAX_ROWS = 200  # Newest rows kept per list: a screenful and a bounded response.
# capping.* kinds that move a proposal, mapped to the state each reaches (the gate's own seven).
_KIND_STATE = {
    "capping.proposed": ProposalState.PROPOSED,
    "capping.checked": ProposalState.CHECKING,
    "capping.capped": ProposalState.CAPPED,
    "capping.applied": ProposalState.APPLIED,
    "capping.verified": ProposalState.VERIFIED,
    "capping.rejected": ProposalState.REJECTED,
    "capping.rolled_back": ProposalState.ROLLED_BACK,
}
_AUDITED = "capping.audited"  # A sampled proposal's second look.

__all__ = [
    "MAX_ROWS",
    "AuditRow",
    "CappingView",
    "ProposalRow",
    "TierAuditRate",
    "TierStateCount",
    "VerdictRow",
    "capping_view",
    "cappings_on_cell",
]


class ProposalRow(BaseModel):
    """One proposal and the state its newest event reached (observe)."""

    model_config = _CONFIG

    id: str = Field(description="The proposal's id (the subject of its capping.* events).")
    tier: str = Field(description="Its declared RiskTier.")
    state: ProposalState = Field(description="PROPOSED ... VERIFIED, REJECTED or ROLLED_BACK.")
    bee_id: str = Field(description="The bee that proposed it (its events' actor).")
    task_id: TaskIdField | None = Field(description="The task it serves.")
    cell_id: CellIdField | None = Field(description="The Cell it would act on.")
    proposed_at: datetime = Field(description="When it was proposed.")
    updated_at: datetime = Field(description="When its newest event happened.")
    failing_check: str | None = Field(description="REJECTED: the check that failed it.")
    rollback_method: str | None = Field(description="ROLLED_BACK: how the Cell was restored.")
    action_kind: str | None = Field(description="Once applied: the kind of action it ran.")


class VerdictRow(BaseModel):
    """The gate's word on a proposal after its checks: capped (approved) or rejected."""

    model_config = _CONFIG

    proposal_id: str = Field(description="The proposal.")
    tier: str = Field(description="Its RiskTier.")
    verdict: Literal["CAPPED", "REJECTED"] = Field(description="Approved to apply, or refused.")
    failing_check: str | None = Field(description="REJECTED: the check that failed, as a code.")
    judge_error: bool = Field(description="The judge could not answer (not 'it disagreed').")
    checks_run: int = Field(description="Checks run (CAPPED: passed; REJECTED: up to the failure).")
    bee_id: str = Field(description="The bee that proposed it.")
    task_id: TaskIdField | None = Field(description="Its task.")
    at: datetime = Field(description="When the verdict was recorded.")


class AuditRow(BaseModel):
    """One sampled audit of a proposal."""

    model_config = _CONFIG

    proposal_id: str = Field(description="The audited proposal.")
    tier: str = Field(description="Its RiskTier.")
    outcome: JudgeOutcome | None = Field(description="The judge's finding; null when it never ran.")
    judge_error: bool = Field(description="The sample was taken but never judged.")
    bee_id: str = Field(
        description="The bee that proposed it; the audit's actor if the proposal is out of view."
    )
    task_id: TaskIdField | None = Field(description="Its task, when the proposal is in view.")
    at: datetime = Field(description="When the audit was recorded.")


class TierStateCount(BaseModel):
    """How many proposals of one tier sit in one state."""

    model_config = _CONFIG

    tier: str = Field(description="The RiskTier.")
    state: ProposalState = Field(description="The state.")
    count: int = Field(description="Proposals of that tier in that state.")


class TierAuditRate(BaseModel):
    """One tier's audit coverage and what the judge found."""

    model_config = _CONFIG

    tier: str = Field(description="The RiskTier.")
    finished: int = Field(description="Proposals that reached a terminal state.")
    audited: int = Field(description="Sampled audits recorded.")
    rejected: int = Field(description="Audits whose finding was REJECT.")
    inconclusive: int = Field(description="Audits the judge never scored.")
    sampled_fraction: float | None = Field(description="audited / finished; null with none.")
    rejection_fraction: float | None = Field(
        description="rejected / (audited - inconclusive); null with no scored audit."
    )


class CappingView(BaseModel):
    """The Hive-wide Capping queue and what came of it (observe)."""

    model_config = _CONFIG

    in_flight: list[ProposalRow] = Field(description="Proposals not yet terminal, newest first.")
    finished: list[ProposalRow] = Field(description="Terminal proposals, newest first.")
    by_tier_state: list[TierStateCount] = Field(description="Proposal counts per tier and state.")
    verdicts: list[VerdictRow] = Field(description="Capped and rejected verdicts, newest first.")
    rollbacks: list[ProposalRow] = Field(description="Rolled-back proposals, newest first.")
    audits: list[AuditRow] = Field(description="Sampled audits, newest first.")
    audit_rates: list[TierAuditRate] = Field(description="Audit coverage and findings per tier.")


@dataclass
class _Track:
    """One proposal while the fold walks the trail; local to this module and never exposed."""

    row: ProposalRow
    judge_error: bool = False
    verdicts: list[VerdictRow] = field(default_factory=list)


def capping_view(events: Sequence[PheromoneEvent]) -> CappingView:
    """Fold ``capping.*`` events into the Capping view.

    Args:
        events: The ``capping`` family's events, in any order (sorted oldest first here).

    Returns:
        The view; empty lists on an empty trail.
    """
    tracks: dict[str, _Track] = {}
    audits: list[PheromoneEvent] = []
    # Oldest first, so each proposal ends in the state its newest event reached.
    for event in sorted(events, key=lambda hop: hop.at):
        if event.kind == _AUDITED:
            audits.append(event)
        elif event.kind in _KIND_STATE:
            _apply(tracks, event)
    rows = [track.row for track in tracks.values()]
    newest = sorted(rows, key=lambda row: (row.updated_at, row.id), reverse=True)
    audit_rows = [_audit_row(event, tracks) for event in reversed(audits)]
    verdicts = sorted(
        (verdict for track in tracks.values() for verdict in track.verdicts),
        key=lambda verdict: (verdict.at, verdict.proposal_id),
        reverse=True,
    )
    return CappingView(
        in_flight=[row for row in newest if not is_terminal(row.state)][:MAX_ROWS],
        finished=[row for row in newest if is_terminal(row.state)][:MAX_ROWS],
        by_tier_state=_counts(rows),
        verdicts=verdicts[:MAX_ROWS],
        rollbacks=[row for row in newest if row.state is ProposalState.ROLLED_BACK][:MAX_ROWS],
        audits=audit_rows[:MAX_ROWS],
        audit_rates=_rates(rows, audit_rows),
    )


def cappings_on_cell(view: CappingView, cell_id: str) -> CappingView:
    """Narrow a Capping view to the proposals that act on one Cell.

    Args:
        view: The Hive-wide view.
        cell_id: The Cell.

    Returns:
        The view with only that Cell's proposals, verdicts, rollbacks and audits; counts and
        rates are recomputed over what is left.
    """
    in_flight = [row for row in view.in_flight if row.cell_id == cell_id]
    finished = [row for row in view.finished if row.cell_id == cell_id]
    ids = {row.id for row in (*in_flight, *finished)}
    audits = [audit for audit in view.audits if audit.proposal_id in ids]
    return CappingView(
        in_flight=in_flight,
        finished=finished,
        by_tier_state=_counts([*in_flight, *finished]),
        verdicts=[verdict for verdict in view.verdicts if verdict.proposal_id in ids],
        rollbacks=[row for row in view.rollbacks if row.cell_id == cell_id],
        audits=audits,
        audit_rates=_rates([*in_flight, *finished], audits),
    )


def _apply(tracks: dict[str, _Track], event: PheromoneEvent) -> None:
    """Move one proposal's track to the state ``event`` reached, creating it on first sight."""
    payload = event.payload
    track = tracks.get(event.subject_id)
    # capping.proposed carries the task, Cell and tier; a later event repeats only the tier.
    if track is None:
        task, cell = payload.get("task_id"), payload.get("cell_id")
        track = _Track(
            ProposalRow(
                id=event.subject_id,
                tier=str(payload.get("tier", "")),
                state=_KIND_STATE[event.kind],
                bee_id=event.actor,
                task_id=task if isinstance(task, str) else None,
                cell_id=cell if isinstance(cell, str) else None,
                proposed_at=event.at,
                updated_at=event.at,
                failing_check=None,
                rollback_method=None,
                action_kind=None,
            )
        )
        tracks[event.subject_id] = track
        update: dict[str, object] = {}
    elif event.kind == "capping.proposed":
        # Tied on `at`, the trail can hand a verdict before its own proposal (two ids minted in
        # the same clock tick order at random), so the proposal only says what the verdict could
        # not, and never moves the state back to PROPOSED.
        update = _proposal_facts(event, track)
    else:
        update = {"state": _KIND_STATE[event.kind], "updated_at": event.at}
    if payload.get("tier") and not track.row.tier:
        update["tier"] = str(payload["tier"])
    track.judge_error = track.judge_error or payload.get("judge_error") is True
    _kind_fields(event, track, update)
    track.row = track.row.model_copy(update=update)


def _proposal_facts(event: PheromoneEvent, track: _Track) -> dict[str, object]:
    """Return what a late-sorted proposal adds to a track a later event already made."""
    payload, row = event.payload, track.row
    task, cell = payload.get("task_id"), payload.get("cell_id")
    facts: dict[str, object] = {"proposed_at": min(row.proposed_at, event.at)}
    if row.task_id is None and isinstance(task, str):
        facts["task_id"] = task
    if row.cell_id is None and isinstance(cell, str):
        facts["cell_id"] = cell
    return facts


def _kind_fields(event: PheromoneEvent, track: _Track, update: dict[str, object]) -> None:
    """Add what only some hops carry: the failing check, the rollback method, the action kind."""
    payload = event.payload
    failing = _text(payload.get("failing_check")) if event.kind == "capping.rejected" else None
    if event.kind == "capping.rejected":
        update["failing_check"] = failing
    elif event.kind == "capping.rolled_back":
        update["rollback_method"] = _text(payload.get("method"))
    elif event.kind == "capping.applied":
        update["action_kind"] = _text(payload.get("action_kind"))
    # The gate's word after its checks is the one hop that earns a verdict row.
    if event.kind in ("capping.capped", "capping.rejected"):
        checks = payload.get("checks_passed", payload.get("checks_run", 0))
        track.verdicts.append(
            VerdictRow(
                proposal_id=track.row.id,
                tier=track.row.tier,
                verdict="CAPPED" if event.kind == "capping.capped" else "REJECTED",
                failing_check=failing,
                judge_error=track.judge_error,
                checks_run=checks if isinstance(checks, int) else 0,
                bee_id=track.row.bee_id,
                task_id=track.row.task_id,
                at=event.at,
            )
        )


def _text(value: object) -> str | None:
    """Return a payload value when it is a string, else None."""
    return value if isinstance(value, str) else None


def _audit_row(event: PheromoneEvent, tracks: dict[str, _Track]) -> AuditRow:
    """Shape one ``capping.audited`` event, joined to its proposal's bee and task when known."""
    payload = event.payload
    track = tracks.get(event.subject_id)
    outcome = payload.get("outcome")
    return AuditRow(
        proposal_id=event.subject_id,
        tier=str(payload.get("tier", "")),
        outcome=_outcome(outcome),
        judge_error=payload.get("judge_error") is True,
        bee_id=track.row.bee_id if track is not None else event.actor,
        task_id=track.row.task_id if track is not None else None,
        at=event.at,
    )


def _outcome(value: object) -> JudgeOutcome | None:
    """Parse a judge outcome off the trail; None for no verdict or an unknown one."""
    try:
        return JudgeOutcome(str(value))
    except ValueError:
        return None


def _counts(rows: Sequence[ProposalRow]) -> list[TierStateCount]:
    """Count proposals per (tier, state), tiers in the RiskTier order, states in lifecycle order."""
    counted = Counter((row.tier, row.state) for row in rows)
    tier_order = {tier.value: index for index, tier in enumerate(RiskTier)}
    state_order = {state: index for index, state in enumerate(ProposalState)}
    keys = sorted(
        counted, key=lambda key: (tier_order.get(key[0], len(tier_order)), state_order[key[1]])
    )
    return [
        TierStateCount(tier=tier, state=state, count=counted[(tier, state)]) for tier, state in keys
    ]


def _rates(rows: Sequence[ProposalRow], audits: Sequence[AuditRow]) -> list[TierAuditRate]:
    """Compute each tier's audit coverage and rejection fraction."""
    finished = Counter(row.tier for row in rows if is_terminal(row.state))
    audited = Counter(audit.tier for audit in audits)
    rejected = Counter(audit.tier for audit in audits if audit.outcome is JudgeOutcome.REJECT)
    unscored = Counter(audit.tier for audit in audits if audit.outcome is None)
    tiers = sorted(set(finished) | set(audited))
    out: list[TierAuditRate] = []
    # One rate row per tier that has a finished proposal or an audit.
    for tier in tiers:
        scored = audited[tier] - unscored[tier]
        out.append(
            TierAuditRate(
                tier=tier,
                finished=finished[tier],
                audited=audited[tier],
                rejected=rejected[tier],
                inconclusive=unscored[tier],
                sampled_fraction=audited[tier] / finished[tier] if finished[tier] else None,
                rejection_fraction=rejected[tier] / scored if scored else None,
            )
        )
    return out
