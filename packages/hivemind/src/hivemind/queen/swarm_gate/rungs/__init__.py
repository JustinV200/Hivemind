"""Decide, from what the Queen knows, which devices to raise a rung or take one back from.

The operator moves a device up or down the runtime ladder with `hive swarm promote` and `demote`.
The roadmap has the Queen decide too: colonize a device whose Forage suffices when the Hive Stand
needs relief (the sub-bees of its Level 0 Wardens take `[swarm.ladder] relief_pressure` of its
cores or memory) or when a task placed on or pinned to it must survive a lost link
(`TaskNeeds.survives_link_loss`); give a colonized device a model server when the shared seats are
held past `[swarm.nuc] seat_pressure` or such a task asks for autonomy. Each pressure bears only on
a device that has a sub-bee running, since moving any other takes nothing off the Hive Stand or the
pool. The operator is always the one who wins: a device with a pending request is left to it, and a
device the operator demoted is pinned (`SwarmNode.ceiling`) and never raised past it. She does not
flap a device: a rung she raised is taken back only after every reason for it has been gone for
`lower_after_s`, and only while the device is idle, and a move that failed is not tried again until
a back-off that doubles with each failure has run out. A package, because the pressure figures, the
tasks' needs, the pure decision, the Queen's memory, the readings and the evaluator are separate
concepts that together outgrow one file (codingrules 5.2).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Built by `SwarmGate` and called by its reconcile pass for each reachable device with no
    request; it moves a device only through `colonize.Colonizer.launch`, so every move is a
    Guard-checked flow of the operator's own. Calls into `hivemind.queen.swarm_gate` (its own
    modules), `hivemind.queen.attach`, `hivemind.queen.trail`, `hivemind.forage`, `hivemind.swarm`
    and waggle only.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.
    - The operator's `desired_level` and `ceiling` are never overridden; the Queen's own moves
      are made through the same flows and the same Guard check.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the Queen's own rung decisions.
    - hivemind.queen.swarm_gate.colonize for the flows these decisions start.
    - hivemind.swarm.level and hivemind.swarm.nuc for the reasons and refusals.

Public API:
    - RungEvaluator: the Queen's own rung decisions for a pass (evaluate).
    - decide_rung, RungFacts, Verdict, Memory, Move, target_rung: the pure decision (decide).
    - RungKeeper: the marks, back-offs and moves in flight she remembers (keeper).
    - Readings, read_world: what she reads off the Hive once per pass (readings).
    - BeeLoad, bees_load, relief_pressure, seat_pressure: the two pressure figures (pressure).
    - survival_cells, pinned_cell: which devices have a task that must outlive its link (needs).
"""

from hivemind.queen.swarm_gate.rungs.decide import (
    Memory,
    Move,
    RungFacts,
    Verdict,
    decide_rung,
    target_rung,
)
from hivemind.queen.swarm_gate.rungs.evaluate import RungEvaluator
from hivemind.queen.swarm_gate.rungs.keeper import RungKeeper
from hivemind.queen.swarm_gate.rungs.needs import pinned_cell, survival_cells
from hivemind.queen.swarm_gate.rungs.pressure import (
    BeeLoad,
    bees_load,
    relief_pressure,
    seat_pressure,
)
from hivemind.queen.swarm_gate.rungs.readings import Readings, read_world

__all__ = [
    "BeeLoad",
    "Memory",
    "Move",
    "Readings",
    "RungEvaluator",
    "RungFacts",
    "RungKeeper",
    "Verdict",
    "bees_load",
    "decide_rung",
    "pinned_cell",
    "read_world",
    "relief_pressure",
    "seat_pressure",
    "survival_cells",
    "target_rung",
]
