"""Mint the trail events a label lowering proposal records, from the proposal as the store left it.

A lowering proposal asks to lower one Nectar's label (a raw deposit in the Honey Store and the Honey
rows ripened from it) below what the Real Cell floor holds it at (ADR-0034). Each of its edges is on
the Pheromone Trail (the Hive's append-only audit log), written in the same transaction as the
change: `honey.lowering_proposed` when it is filed, `honey.label_lowered` when a judge or the human
lowers the label, `honey.lowering_rejected` when a judge rejects it, the human denies it, or it lost
its eligibility by the time it was applied. The store calls these builders inside its transaction
with the proposal as decided there, because whether an approval lowered the label or found it no
longer eligible is only known then.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.lowering`.
    Built by the lowering service (`review`), called by the store's lowering transactions. Calls
    into `hivemind.honey_store.identity` (honey_event), `hivemind.honey_store.clearance`, this
    package's `models` and `state`, and the store protocol's `LoweringEvents` only; no I/O.

Key invariants:
    - Every event's subject is the Nectar, and its payload holds labels, the approver, the outcome
      and ids only: never the deposit's text, the Ripener's reason, the verdict's reasons or the
      human's reason (codingrules section 12).
    - A rubric id is recorded only for a judge's own decision; a human's carries None.

See Also:
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the three events.
    - hivemind.pheromone.events.families for the honey.* vocabulary these kinds belong to.
    - hivemind.honey_store.store.protocol for LoweringEvents, the builder shape.
"""

from __future__ import annotations

from hivemind.honey_store.clearance import LabelApprover
from hivemind.honey_store.identity import HoneyIdentity, honey_event
from hivemind.honey_store.lowering.models import LoweringProposal
from hivemind.honey_store.lowering.state import LoweringState
from hivemind.honey_store.store import LoweringEvents
from hivemind.pheromone import HoneyEvent
from waggle.clock import Clock

LOWERING_PROPOSED_KIND = "honey.lowering_proposed"  # A proposal was filed for one Nectar.
LABEL_LOWERED_KIND = "honey.label_lowered"  # A judge or the human lowered its label.
LOWERING_REJECTED_KIND = "honey.lowering_rejected"  # Rejected, denied, or no longer eligible.
REJECTED_BY_APPROVER = "REJECT"  # A rejection's outcome when the judge or the human said no.
REJECTED_AS_INELIGIBLE = "INELIGIBLE"  # Its outcome when eligibility was lost at apply time.

__all__ = [
    "LABEL_LOWERED_KIND",
    "LOWERING_PROPOSED_KIND",
    "LOWERING_REJECTED_KIND",
    "REJECTED_AS_INELIGIBLE",
    "REJECTED_BY_APPROVER",
    "applied_events",
    "proposed_events",
    "rejected_events",
]


def proposed_events(identity: HoneyIdentity, clock: Clock) -> LoweringEvents:
    """Build the filing's events builder: one `honey.lowering_proposed` with both labels.

    Args:
        identity: The Hive, node and actor the event is stamped with.
        clock: Mints the event's id and time.

    Returns:
        A builder the store calls with the new proposal, inside its filing transaction.
    """

    def build(proposal: LoweringProposal) -> tuple[HoneyEvent, ...]:
        """Return the one event for a freshly filed proposal."""
        event = honey_event(
            identity,
            clock,
            LOWERING_PROPOSED_KIND,
            proposal.nectar_id,
            proposal_id=proposal.id,
            **{"from": proposal.from_label.value, "to": proposal.to_label.value},
        )
        return (event,)

    return build


def applied_events(identity: HoneyIdentity, clock: Clock) -> LoweringEvents:
    """Build an approval's events builder: the lowering, or the rejection it turned into.

    Args:
        identity: The Hive, node and actor the event is stamped with.
        clock: Mints the event's id and time.

    Returns:
        A builder the store calls with the decided proposal inside its apply transaction:
        `honey.label_lowered` when it is LOWERED, else `honey.lowering_rejected` with outcome
        `INELIGIBLE` (the target no longer stood when it was applied).
    """

    def build(proposal: LoweringProposal) -> tuple[HoneyEvent, ...]:
        """Return the lowering's event, or the ineligible rejection's."""
        # An approval that found its target gone was rejected instead (ADR-0034).
        if proposal.state is not LoweringState.LOWERED:
            return (_rejected(identity, clock, proposal, REJECTED_AS_INELIGIBLE),)
        event = honey_event(
            identity,
            clock,
            LABEL_LOWERED_KIND,
            proposal.nectar_id,
            approver=_approver(proposal),
            proposal_id=proposal.id,
            rubric_id=_rubric_id(proposal),
            **{"from": proposal.from_label.value, "to": proposal.to_label.value},
        )
        return (event,)

    return build


def rejected_events(identity: HoneyIdentity, clock: Clock) -> LoweringEvents:
    """Build a rejection's events builder: one `honey.lowering_rejected`, outcome `REJECT`.

    Args:
        identity: The Hive, node and actor the event is stamped with.
        clock: Mints the event's id and time.

    Returns:
        A builder the store calls with the rejected proposal inside its reject transaction.
    """

    def build(proposal: LoweringProposal) -> tuple[HoneyEvent, ...]:
        """Return the one event for a judge's rejection or the human's denial."""
        return (_rejected(identity, clock, proposal, REJECTED_BY_APPROVER),)

    return build


def _rejected(
    identity: HoneyIdentity, clock: Clock, proposal: LoweringProposal, outcome: str
) -> HoneyEvent:
    """Mint one `honey.lowering_rejected`: the approver, the outcome, the ids; never a reason."""
    return honey_event(
        identity,
        clock,
        LOWERING_REJECTED_KIND,
        proposal.nectar_id,
        approver=_approver(proposal),
        outcome=outcome,
        proposal_id=proposal.id,
        rubric_id=_rubric_id(proposal),
    )


def _approver(proposal: LoweringProposal) -> str | None:
    """Return a decided proposal's approver as its wire value (None only if undecided)."""
    return proposal.approver.value if proposal.approver is not None else None


def _rubric_id(proposal: LoweringProposal) -> str | None:
    """Return the judge's rubric id for a judge's own decision; None for the human's."""
    return proposal.rubric_id if proposal.approver is LabelApprover.JUDGE else None
