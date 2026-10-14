"""Move a device's Warden onto the device and back, one rung of the runtime ladder at a time.

A device starts at Level 0 (its Warden on the Hive Stand, only a packet on the device). The
operator asks for a higher rung, or the Hive Stand needs relief, and the Queen colonizes: the
packet installs the Hive's runtime, the Warden's running tasks are paused into Handoffs, the Level
0 Warden stops, the packet starts the Warden on the device under the same id, and the tasks resume
there from their Handoffs, carried inline (ADR-0052). Every rung change, up or down, passes the
Guard's `NUC_PROMOTION` point first; any failure rolls back to a Warden on the Hive Stand and says
why on the trail; a withdrawal is made only by an idle Warden. A package, because the decision,
the Guard check, the two flows, their steps and the place a Warden's arrival is reported are
separate concepts that together outgrow one file (codingrules 5.2).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Built by the gate and called by its reconcile pass and its door. Calls into
    `hivemind.queen.attach`, `.cluster`, `.dispatcher`, `.trail`, `hivemind.queen.swarm_gate`
    (takeback, terms, wardens), `hivemind.swarm` and waggle only.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder and the move.
    - hivemind.swarm.level for `decide_colonize`, the pure decision the first rung defers to.

Public API:
    - Colonizer, Flow, GUARD_DENIED_WORD: carries out the operator's requested rung (flow).
    - Promotion, Withdrawal: the two flows (promote, withdraw).
    - Ask, Step, decide_request, next_rung: the pure request decision (request).
    - authorise_rung: the Guard check every rung change passes (guard).
    - ColonizeStepError, word_for: how a step fails (errors).
"""

from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError, word_for
from hivemind.queen.swarm_gate.colonize.flow import GUARD_DENIED_WORD, Colonizer, Flow
from hivemind.queen.swarm_gate.colonize.guard import authorise_rung
from hivemind.queen.swarm_gate.colonize.promote import Promotion
from hivemind.queen.swarm_gate.colonize.request import Ask, Step, decide_request, next_rung
from hivemind.queen.swarm_gate.colonize.withdraw import Withdrawal

__all__ = [
    "GUARD_DENIED_WORD",
    "Ask",
    "ColonizeStepError",
    "Colonizer",
    "Flow",
    "Promotion",
    "Step",
    "Withdrawal",
    "authorise_rung",
    "decide_request",
    "next_rung",
    "word_for",
]
