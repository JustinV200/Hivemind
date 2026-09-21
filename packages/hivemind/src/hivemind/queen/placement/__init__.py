"""Placement: the Queen's pure choice of which Cell -- Real or Virtual -- runs a ready task.

Roadmap step 5.7 (ADR-0028): placement grew from choosing among attached Real Cells alone (v0) to
choosing between reusing a Real Cell, resuming an Overwintered Virtual Cell
(`docs/adr/0029-overwintering-policy.md`) or provisioning a fresh Virtual one. The whole decision
is one pure function, `decide`, over three plain-data inputs its caller (`hivemind.queen.
dispatcher`) precomputes: an `Inventory` snapshot of every candidate and the Cell Wax that weighs
on them, a `ForageView` of what a Virtual candidate's own promised capacity must cover, and a
`PlacementPolicy` read from the manifest's `[placement]`/`[virtual_cells]` sections. `rules` holds
one small, individually-tested function per ADR-0028 rule.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen` package. Called
    once per ready task by `hivemind.queen.dispatcher.dispatch_ready`. Calls into `hivemind.cell`
    (Isolation, TaskNeeds, CombShieldLevel), `hivemind.hive` (VirtualCellSpec, NetworkPolicy,
    BackendCapabilities), `hivemind.forage` (RoleFootprint), `hivemind.queen.errors` (QueenError)
    and waggle only.

Key invariants:
    - `decide` is pure: given the same `Inventory`/`ForageView`/`PlacementPolicy`, it always
      returns the same `Placement`, or raises the same shape of `PlacementError`.
    - `decide` never reads `cell.kind`: `scripts/check_no_kind_branches.py` allowlists
      `hivemind/queen/placement/` in full (codingrules section 8.7: placement is one of exactly two
      callers CellKind matters to).
    - `Placement` is a union (`ReuseReal`, `ReuseDormant`, `ProvisionVirtual`), never a single
      shape; every variant carries its own `reason`.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the design this package implements.
    - docs/adr/0029-overwintering-policy.md for what `ReuseDormant` resumes.
    - .claude/codingrules.md section 8.7 for "placement is a pure decision".
    - .claude/roadmap.md step 5.7 for the rules and exit criteria this package satisfies.
    - hivemind.queen.dispatcher for dispatch_ready, this package's one caller.

Public API:
    - Placement, ReuseReal, ReuseDormant, ProvisionVirtual: the placement decision itself (models).
    - Inventory, RealCandidate, VirtualBackendCandidate, DormantCandidate, WaxMention, ForageView:
      the pure snapshot `decide` reads, precomputed by its caller (inventory).
    - PlacementPolicy, VirtualSpecTemplate, Prefer: the `[placement]`-derived value object `decide`
      reads (policy).
    - PlacementError, decide: the pipeline itself, and the error it raises when nothing fits
      (decide).
    - rules: the individually-tested rule functions `decide` runs, reached as
      `hivemind.queen.placement.rules.<name>` rather than re-exported here one by one.
"""

from hivemind.queen.placement import rules
from hivemind.queen.placement.decide import PlacementError, decide
from hivemind.queen.placement.inventory import (
    DormantCandidate,
    ForageView,
    Inventory,
    RealCandidate,
    VirtualBackendCandidate,
    WaxMention,
)
from hivemind.queen.placement.models import Placement, ProvisionVirtual, ReuseDormant, ReuseReal
from hivemind.queen.placement.policy import PlacementPolicy, Prefer, VirtualSpecTemplate

__all__ = [
    "DormantCandidate",
    "ForageView",
    "Inventory",
    "Placement",
    "PlacementError",
    "PlacementPolicy",
    "Prefer",
    "ProvisionVirtual",
    "RealCandidate",
    "ReuseDormant",
    "ReuseReal",
    "VirtualBackendCandidate",
    "VirtualSpecTemplate",
    "WaxMention",
    "decide",
    "rules",
]
