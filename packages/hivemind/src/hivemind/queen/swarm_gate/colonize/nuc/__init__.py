"""Give a colonized device a model server of its own, and take it away: the Nuc rungs.

Level 2 of the runtime ladder (ADR-0052) is a colonized device whose Warden also runs a model
server on it, so its sub-bees keep thinking when the link to the Hive Stand is lost. The Queen
decides it from data (`facts`: what the device's Warden declared, what the Hive allows and what
fits the VRAM it has free), asks the Guard like for every rung, and carries it out as a flow:
`NucPromotion` sets the Warden's ceilings, orders the server started, moves the rung and writes
the device's hosting plan with its own models first; `NucDemotion` is the way back. `NucDesk` is
where the gate keeps what device Wardens declared and where the orders to them wait for their
answers. A sub-package of `colonize` because these are two more rungs of the same ladder, and its
directory is at the fan-out limit.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Built and started by `colonize.flow.Colonizer`; the
    desk is fed by the gate's door. Calls into `hivemind.queen.forage`, `hivemind.queen.swarm_gate`
    (its own modules), `hivemind.swarm` and waggle only.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules import each other by full module path, never through this face, and
      `colonize.env` names the desk only as a type, so importing the face cannot cycle.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder.
    - hivemind.swarm.nuc for ``decide_nuc``, the pure decision these flows act on.
    - hivemind.wardens.ticks.nuc for the Warden's side.

Public API:
    - NucDesk, REPLY_MARGIN_S: the declared servers and the orders in flight (desk).
    - nuc_decision, nuc_ceilings: the pure halves of a promotion (facts).
    - NucPromotion, PROMOTE_DEADLINE_S, DEMOTE_DEADLINE_S: the way up (promote).
    - NucDemotion: the way down (demote).
"""

from hivemind.queen.swarm_gate.colonize.nuc.demote import NucDemotion
from hivemind.queen.swarm_gate.colonize.nuc.desk import REPLY_MARGIN_S, NucDesk
from hivemind.queen.swarm_gate.colonize.nuc.facts import nuc_ceilings, nuc_decision
from hivemind.queen.swarm_gate.colonize.nuc.promote import (
    DEMOTE_DEADLINE_S,
    PROMOTE_DEADLINE_S,
    NucPromotion,
)

__all__ = [
    "DEMOTE_DEADLINE_S",
    "PROMOTE_DEADLINE_S",
    "REPLY_MARGIN_S",
    "NucDemotion",
    "NucDesk",
    "NucPromotion",
    "nuc_ceilings",
    "nuc_decision",
]
