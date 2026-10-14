"""Give a colonized device a model server of its own, take it away, and keep it honest.

Level 2 of the runtime ladder (ADR-0052) is a colonized device whose Warden also runs a model
server on it, so its sub-bees keep thinking when the link to the Hive Stand is lost. The Queen
decides it from data (`facts`: what the device's Warden declared, what the Hive allows, what fits
the VRAM it has free, and the pressure and the autonomy a task needs), asks the Guard like for every
rung, and carries it out as a flow: `NucPromotion` sets the Warden's ceilings, orders the server
started, moves the rung and writes the device's hosting plan with its own models first;
`NucDemotion` is the way back. `NucRestoration` is the flow for a Warden that restarted under a
device recorded as a Nuc, which runs no server: it starts the server again or lowers the rung, so
the registry never says a device has a server it has not. `NucDesk` is where the gate keeps what
device Wardens declared and reported and where the orders to them wait for their answers. A
sub-package of `colonize` because these are more rungs of the same ladder, and its directory is at
the fan-out limit.

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
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for a Nuc across a restart.
    - hivemind.swarm.nuc for ``decide_nuc``, the pure decision these flows act on.
    - hivemind.wardens.ticks.nuc for the Warden's side.

Public API:
    - NucDesk, Reported, REPLY_MARGIN_S: the declared servers, the reported terms, the proof of a
      running server, and the orders in flight (desk).
    - nuc_decision, nuc_facts, nuc_ceilings, restore_order, RestoreOrder: the pure halves of a
      promotion and of a restoration (facts).
    - NucPromotion, write_nuc_plan, PROMOTE_DEADLINE_S, DEMOTE_DEADLINE_S: the way up (promote).
    - NucDemotion, lower_to_colonized, tidy_nuc, take_sources_off_the_map: the way down (demote).
    - NucRestoration: the way back after a Warden restarted (restore).
"""

from hivemind.queen.swarm_gate.colonize.nuc.demote import (
    NucDemotion,
    lower_to_colonized,
    take_sources_off_the_map,
    tidy_nuc,
)
from hivemind.queen.swarm_gate.colonize.nuc.desk import REPLY_MARGIN_S, NucDesk, Reported
from hivemind.queen.swarm_gate.colonize.nuc.facts import (
    RestoreOrder,
    nuc_ceilings,
    nuc_decision,
    nuc_facts,
    restore_order,
)
from hivemind.queen.swarm_gate.colonize.nuc.promote import (
    DEMOTE_DEADLINE_S,
    PROMOTE_DEADLINE_S,
    NucPromotion,
    write_nuc_plan,
)
from hivemind.queen.swarm_gate.colonize.nuc.restore import NucRestoration

__all__ = [
    "DEMOTE_DEADLINE_S",
    "PROMOTE_DEADLINE_S",
    "REPLY_MARGIN_S",
    "NucDemotion",
    "NucDesk",
    "NucPromotion",
    "NucRestoration",
    "Reported",
    "RestoreOrder",
    "lower_to_colonized",
    "nuc_ceilings",
    "nuc_decision",
    "nuc_facts",
    "restore_order",
    "take_sources_off_the_map",
    "tidy_nuc",
    "write_nuc_plan",
]
