"""Define the Cell abstraction shared by every kind of machine the Hive runs work on.

Provides Cell, CellKind (REAL for a borrowed device or VIRTUAL for a provisioned one), CellSession
(a terminal session on it), leases, TaskNeeds (what a task requires from the Cell that runs it) and
the three security tier enums it defines: AccessLevel (how much of a Real Cell the Hive may
touch), CombShieldLevel (a Cell's security tier: what network egress a task on it must run
through) and HoneyClearance (the data-sensitivity label carried by every memory tier). It knows
what a Cell is, never how one is made. Phase 2 step 2.3a builds the security enums and TaskNeeds
first, ahead of Cell, CellKind, CellSession and leases (phase 3), because brood_chamber.TaskSpec
carries TaskNeeds and both are pure data with no dependency on the rest of this package.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by hive, swarm, exoskeleton
    and royal_jelly (Layer 3) and every layer above them. Calls into hivemind.common,
    hivemind.forage (for Tempo) and waggle only; it defines the security enums guard interprets
    rather than importing them.

Key invariants:
    - AccessLevel, CombShieldLevel and HoneyClearance mirror waggle.messages.labels's enums of
      the same names, member for member (tests/unit/cell/test_tiers.py checks it).
    - A TaskNeeds with comb_shield == CombShieldLevel.NIGHT_VEIL always has
      isolation == Isolation.REQUIRED: Night Veil is virtual-only.
    - Cell, CellKind, CellSession and leases are not implemented yet; they land in phase 3
      (roadmap), which is also where the layout described above becomes literally true.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - .claude/codingrules.md section 8.7 for the Cell model this package will complete in phase 3.
    - .claude/roadmap.md phase 2 step 2.3a for the work that first populates this package, and
      phase 3 for the rest.

Public API:
    - AccessLevel, CombShieldLevel, HoneyClearance: the three Cell security dimensions
      (hivemind.cell.tiers).
    - Isolation, OsFamily, TaskNeeds: what a task requires from its Cell (hivemind.cell.needs).
"""

from hivemind.cell.needs import Isolation, OsFamily, TaskNeeds
from hivemind.cell.tiers import AccessLevel, CombShieldLevel, HoneyClearance

__all__ = [
    "AccessLevel",
    "CombShieldLevel",
    "HoneyClearance",
    "Isolation",
    "OsFamily",
    "TaskNeeds",
]
