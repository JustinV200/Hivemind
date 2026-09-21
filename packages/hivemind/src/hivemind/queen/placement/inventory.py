"""Define Inventory and ForageView: the pure snapshot `decide` reads, precomputed by its caller.

ADR-0028: "`decide` performs no I/O: the caller precomputes blocked and cautioned Cells, headroom
and the dormant list." This module holds the plain-data shapes that precomputed snapshot takes:
`RealCandidate` (one attached Warden's own Real Cell, with the free-capacity flag the caller
already measured against the placed bee's own `RoleFootprint`), `VirtualBackendCandidate` (one
registered `hivemind.hive.CellBackend`, its declared `BackendCapabilities` headroom -- already
narrowed to *remaining* room by the caller, so `hivemind.queen.placement.decide` never has to
subtract a live count itself -- and the `VirtualCellSpec`s it can provision right now),
`DormantCandidate` (one Overwintered Virtual Cell, `docs/adr/0029-overwintering-policy.md`,
waiting to be resumed instead of provisioned) and `WaxMention` (one Cell Wax note's id and text,
just enough for a `Placement.reason` to name it without decide ever reading the note itself).
`Inventory` gathers every candidate plus the blocked/cautioned Cell Wax maps ADR-0028 calls "the
current Cell inventory[,] each candidate's Cell Wax"; `ForageView` is the other half of `decide`'s
signature, "what is needed to answer 'Forage covers the grant' without I/O" for a Virtual
candidate -- a Real Cell's own free-capacity flag already lives on its `RealCandidate` and a
backend's headroom already lives on its own `BackendCapabilities`, so `ForageView` carries only
the one thing neither of those already has: the footprint the task's own placed bee would cost,
checked against a candidate `VirtualCellSpec`'s own promised capacity.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.placement`
    sub-package. Built by `hivemind.queen.dispatcher`'s snapshot helper (the one place allowed to
    do the I/O -- reading Cell Wax, the attached Wardens' own Cells, and `QueenDeps.
    virtual_backends`/`.dormant_cells` -- that fills these fields); read by `hivemind.queen.
    placement.decide.decide` and `hivemind.queen.placement.rules`. Calls into `hivemind.cell`
    (CellCapabilities, CombShieldLevel), `hivemind.forage` (RoleFootprint), `hivemind.hive`
    (BackendCapabilities, VirtualCellSpec) and `waggle.ids` only.

Key invariants:
    - Every type here is a frozen, slotted dataclass (codingrules section 8.5): a snapshot is a
      value built once per placement decision, never mutated afterwards.
    - `Inventory`'s own defaults (empty tuples and mappings) mean `Inventory()` is "nothing to
      place on", not "unset"; `decide` raises `PlacementError` rather than treating it specially.
    - `VirtualBackendCandidate.capabilities.headroom` is already the caller's own *remaining*
      figure (roadmap step 5.6's retry path zeroes it for one backend after a failed provision,
      ADR-0028 Consequences): `decide` only ever compares it against zero, never subtracts.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the Inventory/ForageView split
      this module implements verbatim ("the caller precomputes... before each decision").
    - docs/adr/0029-overwintering-policy.md for DormantCandidate, the Cell ReuseDormant resumes.
    - hivemind.queen.placement.decide for decide, this snapshot's one reader.
    - hivemind.queen.placement.models for Placement, decide's own return value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from hivemind.cell import CellCapabilities, CombShieldLevel
from hivemind.forage import RoleFootprint
from hivemind.hive import BackendCapabilities, VirtualCellSpec
from waggle.ids import CellId, WardenId

__all__ = [
    "DormantCandidate",
    "ForageView",
    "Inventory",
    "RealCandidate",
    "VirtualBackendCandidate",
    "WaxMention",
]


@dataclass(frozen=True, slots=True)
class WaxMention:
    """One Cell Wax note's id and text: enough for a `Placement.reason` to name it (ADR-0028)."""

    id: str
    text: str


@dataclass(frozen=True, slots=True)
class RealCandidate:
    """One attached Warden's own Real Cell, as `decide` needs to see it.

    Attributes:
        warden_id: The Warden that owns this Cell; a `ReuseReal` names it directly.
        cell_id: The Cell itself.
        capabilities: What the Cell is and can do (os, display, network scopes).
        comb_shield: The Cell's own security tier.
        is_hive_stand: Whether this is the Hive Stand (`cell.source == "hive_stand"`); rule 3's
            own `allow_hive_stand = false` check reads this, never the Cell's own source string.
        has_free_capacity: Whether Forage already has room for one more bee on this Cell, as the
            caller measured it against the placed bee's own `RoleFootprint` -- `decide` never
            recomputes this (ADR-0028: "a Real Cell needs free capacity... the caller
            precomputes").
    """

    warden_id: WardenId
    cell_id: CellId
    capabilities: CellCapabilities
    comb_shield: CombShieldLevel
    is_hive_stand: bool
    has_free_capacity: bool


@dataclass(frozen=True, slots=True)
class VirtualBackendCandidate:
    """One registered `hivemind.hive.CellBackend`: its headroom, and the specs it can provision.

    Attributes:
        name: The backend's own registry name ("docker", "qemu", "fake").
        capabilities: What this backend can do, including its own *remaining* headroom (module
            docstring's own key invariant): `decide` only ever compares it against zero.
        specs: The `VirtualCellSpec`s this backend can provision right now, in the caller's own
            preference order; `decide` picks the first one that fits a task's needs and Forage.
    """

    name: str
    capabilities: BackendCapabilities
    specs: tuple[VirtualCellSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class DormantCandidate:
    """One Overwintered Virtual Cell (`docs/adr/0029`), waiting to be resumed, not provisioned.

    Attributes:
        cell_id: The dormant Cell itself.
        warden_id: Its own Warden, if the caller still has an id for it (a resumed Cell's Warden
            reconnects fresh, so this is commonly `None` until then).
        image: The image it was provisioned from; `decide` matches this against a candidate
            Virtual spec's own `image` before ever returning `ReuseDormant`.
        comb_shield: Its own security tier; never `NIGHT_VEIL` (`docs/adr/0029`: Night Veil Cells
            are excluded from the Overwintering pool outright).
    """

    cell_id: CellId
    warden_id: WardenId | None
    image: str
    comb_shield: CombShieldLevel


@dataclass(frozen=True, slots=True)
class Inventory:
    """Every Cell `decide` may choose among, precomputed by the caller (ADR-0028): no I/O here.

    Attributes:
        real: Every attached Warden's own Real Cell, in attachment order (rule 6's own tie-break).
        virtual_backends: Every registered Virtual backend with room to provision, in the caller's
            own preference order.
        dormant: Every Overwintered Virtual Cell available to resume instead.
        blocked: Cell id to the one `WaxMention` that excludes it outright (rule 3: a WRITTEN
            `WaxSeverity.BLOCK` note).
        cautioned: Cell id to the one `WaxMention` that ranks it behind a clean candidate (rule 6:
            a WRITTEN `WaxSeverity.CAUTION` note is a penalty, never an exclusion).
    """

    real: tuple[RealCandidate, ...] = ()
    virtual_backends: tuple[VirtualBackendCandidate, ...] = ()
    dormant: tuple[DormantCandidate, ...] = ()
    blocked: Mapping[CellId, WaxMention] = field(default_factory=dict)
    cautioned: Mapping[CellId, WaxMention] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ForageView:
    """What `decide` needs to check "Forage covers the grant" against a Virtual spec, no I/O.

    A Real Cell's own free-capacity flag already lives on its `RealCandidate` and a backend's
    headroom already lives on its own `BackendCapabilities` (module docstring); `footprint` is the
    one figure neither already carries: what the task's own placed bee would cost, checked against
    a candidate `VirtualCellSpec.cpu_cores`/`.memory_bytes`/`.capacity.max_sub_bees`.

    Attributes:
        footprint: The `RoleFootprint` of the bee this task would run as -- ordinarily
            `deps.footprints[WorkerRole.DRONE]`, the only Worker role phase 3 implements.
    """

    footprint: RoleFootprint
