"""Define the Night Veil skeleton: which trail events outlive a Night Veil Cell, cut to what shape.

Codingrules section 12 names the lifecycle skeleton that alone survives a Night Veil Cell on the
Queen's trail: `cell.provisioned`, `cell.attested` (pass or fail, per check), `queen.placed` with
the id of the human request that asked for Night Veil, `forage.granted`, `forage.plan_written`,
`cell.sting_cut`, task state transitions carrying nothing beyond the task id, one
`capping.summary` per tier with counts of approved, rejected and rolled back, and
`cell.destroyed`. This module is that list as code. `skeleton_event` returns the copy of an event
that may cross the boundary, its payload cut to the fields the skeleton allows, or None for a kind
that never crosses; `tier_counts` folds a Cell's `capping.*` detail into the per-tier counts one
`capping.summary` carries. Both are pure (codingrules 8.3): the edges that route an event
(`hivemind.pheromone.retention.trail`), cut a task transition at its source (the Brood Chamber)
and record the summaries at teardown (`hivemind.pheromone.retention.purge`) call them.

The kept fields answer the two questions ADR-0030 keeps the skeleton for, "what did my Hive do and
what did it cost" and "is the Cell provably gone", and nothing else: the backend, image and tier a
Cell was provisioned from, each attestation check's PASS or FAIL without its detail, the request a
placement stands on, a grant's task and size, a hosting plan's revision and slot count, and the
Undertaker's cleanup counts. Every task kind, `task.progressed` included, keeps nothing but the
task id its event is about: the Brood Chamber writes each task event with its row, so a task event
is cut rather than withheld.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.retention`.
    Called by `hivemind.pheromone.retention.trail`, `hivemind.pheromone.retention.purge` and
    `hivemind.brood_chamber.chamber`. Calls into `hivemind.pheromone.events` only.

Key invariants:
    - Pure: no I/O and no clock; the same event in gives the same cut copy (or None) out.
    - A cut copy keeps the event's id, kind, subject, node, actor and time; only its payload
      shrinks, and never gains a key the original did not carry.
    - Every kind named here is already in its family's KINDS: the skeleton adds no vocabulary.

See Also:
    - .claude/codingrules.md section 12 for the list this module encodes.
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for why these and no others.
    - hivemind.pheromone.retention.trail for VeiledTrail, which records what this module cuts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.pheromone.events import PheromoneEvent, TaskEvent

# cell.attested keeps its verdict per check (codingrules 12: "pass or fail, per check"), never the
# check's own detail text, which on a real probe can name an address a leak test saw.
ATTESTED_KIND = "cell.attested"
SUMMARY_KIND = "capping.summary"  # One per tier per Cell, recorded by the teardown purge.
_ATTESTED_VERDICT_KEYS = frozenset({"passed", "red"})  # The overall verdict and the red names.
_CHECK_STATUS_KEY = "status"  # The one field of a check's own result that survives: PASS or FAIL.
_APPROVED_KIND = "capping.capped"  # Every required check passed: the gate approved the proposal.
_REJECTED_KIND = "capping.rejected"  # A check failed before anything was applied.
_ROLLED_BACK_KIND = "capping.rolled_back"  # Applied, then undone when a postcondition failed.
_CAPPING_FAMILY = "capping"
_TIER_KEY = "tier"  # capping.proposed, .capped and .rejected each name the proposal's risk tier.

# Every other skeleton kind, with the payload keys it keeps. A task kind keeps none: the task id
# is the event's own subject, and a transition carries nothing beyond it (codingrules 12).
_KEPT_KEYS: Mapping[str, frozenset[str]] = {
    "cell.provisioned": frozenset({"backend", "image", "comb_shield"}),
    "queen.placed": frozenset({"goal_request_id"}),
    "forage.granted": frozenset({"task_id", "max_sub_bees"}),
    "forage.plan_written": frozenset({"revision", "slots"}),
    "cell.sting_cut": frozenset(),
    SUMMARY_KIND: frozenset({"tier", "approved", "rejected", "rolled_back"}),
    "cell.destroyed": frozenset({"grants_revoked", "wax_retired", "leavings_removed"}),
    **{kind: frozenset() for kind in TaskEvent.KINDS},
}

SKELETON_KINDS: frozenset[str] = frozenset(_KEPT_KEYS) | {ATTESTED_KIND}

__all__ = [
    "ATTESTED_KIND",
    "SKELETON_KINDS",
    "SUMMARY_KIND",
    "TierCount",
    "merge_counts",
    "skeleton_event",
    "tier_counts",
]


@dataclass(frozen=True, slots=True)
class TierCount:
    """One risk tier's proposals on one Night Veil Cell: the counts a `capping.summary` carries.

    Attributes:
        tier: The Capping risk tier's own value (`SCRATCH_WRITE`, `NETWORK_EGRESS`, ...).
        approved: Proposals at this tier every required check passed (`capping.capped`).
        rejected: Proposals at this tier a check refused before anything applied.
        rolled_back: Proposals at this tier applied and then undone.
    """

    tier: str
    approved: int
    rejected: int
    rolled_back: int

    def payload(self) -> dict[str, JsonValue]:
        """Return the `capping.summary` payload for this tier: the tier and its three counts."""
        return {
            "tier": self.tier,
            "approved": self.approved,
            "rejected": self.rejected,
            "rolled_back": self.rolled_back,
        }


def skeleton_event[Event: PheromoneEvent](event: Event) -> Event | None:
    """Return the copy of `event` that may cross the Night Veil boundary, or None.

    Args:
        event: Any event the Queen records about a Night Veil Cell or its tasks.

    Returns:
        A copy with the payload cut to the skeleton's fields when `event.kind` is a skeleton kind;
        None when the kind never survives a Night Veil Cell.
    """
    if event.kind == ATTESTED_KIND:
        payload = _attested_verdicts(event.payload)
    elif event.kind in _KEPT_KEYS:
        kept = _KEPT_KEYS[event.kind]
        payload = {key: value for key, value in event.payload.items() if key in kept}
    else:
        return None
    # model_copy keeps the subclass and every other field; the cut payload is a subset of one
    # that already passed the payload validator, so it cannot fail it.
    return event.model_copy(update={"payload": payload})


def tier_counts(events: Iterable[PheromoneEvent]) -> tuple[TierCount, ...]:
    """Fold a Cell's `capping.*` events into one TierCount per risk tier, sorted by tier.

    Args:
        events: Everything a Night Veil Cell's ephemeral segment held; non-capping kinds are
            skipped, so the whole segment may be passed as it is.

    Returns:
        One TierCount per tier any proposal named, in tier order; empty when the Cell proposed
        nothing. A proposal whose tier no event named (its `capping.proposed` never shipped) is
        left out, never counted under a guessed tier.
    """
    tiers: dict[str, str] = {}
    outcomes: dict[str, set[str]] = {}
    for event in events:
        if event.family != _CAPPING_FAMILY:
            continue
        # A proposal's tier rides on proposed/capped/rejected; rolled_back names none, so every
        # outcome is joined to its tier through the proposal id the events share as subject.
        tier = event.payload.get(_TIER_KEY)
        if isinstance(tier, str):
            tiers.setdefault(event.subject_id, tier)
        outcomes.setdefault(event.subject_id, set()).add(event.kind)
    return _count_by_tier(tiers, outcomes)


def merge_counts(*groups: Iterable[TierCount]) -> tuple[TierCount, ...]:
    """Sum per-tier counts from several spans of one Cell's life, one TierCount per tier.

    Args:
        *groups: The counts of each span (what a Queen before a restart counted, what one after
            it counted); a tier missing from a span counts nothing there.

    Returns:
        One TierCount per tier any span named, in tier order.
    """
    totals: dict[str, tuple[int, int, int]] = {}
    for count in (count for group in groups for count in group):
        approved, rejected, rolled_back = totals.get(count.tier, (0, 0, 0))
        totals[count.tier] = (
            approved + count.approved,
            rejected + count.rejected,
            rolled_back + count.rolled_back,
        )
    return tuple(TierCount(tier, *totals[tier]) for tier in sorted(totals))


def _count_by_tier(
    tiers: Mapping[str, str], outcomes: Mapping[str, set[str]]
) -> tuple[TierCount, ...]:
    """Count approved, rejected and rolled-back proposals per tier, from per-proposal kinds."""
    by_tier: dict[str, list[set[str]]] = {}
    for proposal, tier in tiers.items():
        by_tier.setdefault(tier, []).append(outcomes.get(proposal, set()))
    # A proposal counts once per outcome it reached: one both capped and later rolled back is
    # approved by the gate and undone after it, and the summary says so in both columns.
    return tuple(
        TierCount(
            tier=tier,
            approved=sum(_APPROVED_KIND in kinds for kinds in by_tier[tier]),
            rejected=sum(_REJECTED_KIND in kinds for kinds in by_tier[tier]),
            rolled_back=sum(_ROLLED_BACK_KIND in kinds for kinds in by_tier[tier]),
        )
        for tier in sorted(by_tier)
    )


def _attested_verdicts(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Keep `cell.attested`'s verdict and each check's own status; drop every check's detail."""
    cut: dict[str, JsonValue] = {}
    for key, value in payload.items():
        if key in _ATTESTED_VERDICT_KEYS:
            cut[key] = value
        elif isinstance(value, dict):
            # One check's own result: its PASS/FAIL status crosses, its free-text detail never.
            cut[key] = {_CHECK_STATUS_KEY: value.get(_CHECK_STATUS_KEY)}
    return cut
