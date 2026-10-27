"""Keep what every bee of one attempt ran on and proposed through, for the Nurse Bee's evidence.

A task's attempt on a Warden can pass through several sub-bees: a rebind, a takeover or a Handoff
successor retires one bee and starts the next on the same Cell, and the last one claims. The Nurse
(ADR-0061) must be held against every model that touched the work (`hivemind.wardens.nurse.
strength` asks for every binding, rebind targets included) and must see every verified change,
not only the claiming bee's. `NurseTrace` is that record: each bee's binding and Capping gate, in
order, carried from a retired bee to its successor.

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens.nurse`. Built at spawn
    (`hivemind.wardens.spawn.spawn`), carried on `SubBee.trace` across a respawn
    (`hivemind.wardens.ticks.alarms`), read by the accept path (`hivemind.wardens.ticks.results`).
    Calls into `hivemind.llm` (BoundModel), `hivemind.supervision.capping` (CappingGate, the
    action bound) and waggle only.

Key invariants:
    - Only VERIFIED proposals become actions, and only those the gate's own bee proposed: a
      rejected or rolled-back change never landed, and nothing else's work is the attempt's.
    - Actions keep proposal order and are cut to the newest MAX_NURSE_ACTIONS, since the latest
      changes are what the Cell now holds.
    - Never the proposals' reasons or proposer ids: only their actions leave this module.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.supervision.capping.checks.nurse for NurseRequest, where the actions land.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.llm import BoundModel
from hivemind.supervision.capping import CappingGate, ProposalState
from hivemind.supervision.capping.checks import MAX_NURSE_ACTIONS
from waggle.ids import WorkerId
from waggle.messages.capping import ProposedAction

__all__ = ["NurseTrace"]


@dataclass(frozen=True, slots=True)
class NurseTrace:
    """Each bee of one attempt: the binding it ran on and the gate it proposed through."""

    bindings: tuple[BoundModel, ...] = ()
    gates: tuple[tuple[WorkerId, CappingGate], ...] = ()  # (the bee, its own gate), in order.

    def then(self, later: NurseTrace) -> NurseTrace:
        """Return this trace followed by `later`'s, for a successor that carries the attempt on."""
        return NurseTrace(self.bindings + later.bindings, self.gates + later.gates)

    def actions(self) -> tuple[ProposedAction, ...]:
        """Return the newest MAX_NURSE_ACTIONS verified actions, oldest first."""
        verified = [
            proposal.action
            for worker_id, gate in self.gates
            for proposal in gate.proposals()
            if proposal.state is ProposalState.VERIFIED and proposal.proposer == worker_id
        ]
        return tuple(verified[-MAX_NURSE_ACTIONS:])
