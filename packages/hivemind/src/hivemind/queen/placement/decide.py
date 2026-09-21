"""Define PlacementError and decide: the Queen's pure Real-vs-Virtual-Cell placement pipeline.

`decide(needs, inventory, forage, policy) -> Placement` is ADR-0028's own ordered pipeline: Night
Veil first (hard rule 2, always a fresh Virtual Cell), then isolation (hard rule 1, excludes every
Real Cell), then each side's own candidates are filtered by the hard rules in
`hivemind.queen.placement.rules` (a `BLOCK` Cell Wax or `allow_hive_stand = false`, then fit, then
Forage) and ranked (a `CAUTION` note behind a clean candidate, a dormant Cell before a fresh
provision, attachment order breaking every other tie). Only once both sides are known does
`policy.prefer` get read at all: the preferred side wins if it has a candidate; otherwise the other
side is used and the reason says why (this is how a `BLOCK` Cell Wax on the Hive Stand turns
`prefer = "real"` into a Virtual placement, per the roadmap's own exit criterion); if neither side
has one, `PlacementError` names every rule that eliminated a candidate, on both sides. `decide`
performs no I/O of its own (ADR-0028): every candidate, every Cell Wax note and every headroom
figure it reads already sits on `inventory`, precomputed by `hivemind.queen.dispatcher`'s snapshot
helper before this is ever called.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.placement`
    sub-package. Called once per ready task by `hivemind.queen.dispatcher`. Calls into
    `hivemind.cell` (CombShieldLevel, TaskNeeds), `hivemind.hive` (NetworkPolicy, VirtualCellSpec),
    `hivemind.queen.errors` (QueenError), this package's own `inventory`/`models`/`policy`/`rules`
    modules and waggle only.

Key invariants:
    - `decide` is pure: given the same arguments, it always returns the same `Placement` or raises
      the same shape of `PlacementError`.
    - `decide` never reads `cell.kind`: every check runs over `TaskNeeds`, `CellCapabilities`, a
      `VirtualCellSpec`'s own fields, or Cell Wax set membership
      (`scripts/check_no_kind_branches.py` allowlists this whole package for exactly that reason --
      `hivemind/queen/placement/` is a path-fragment match, so every module here is covered).
    - A `BLOCK`ed or non-fitting candidate is never returned, on either side (a hypothesis property
      test in `tests/unit/queen/placement/test_decide_properties.py` checks this over random
      inventories).

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the pipeline this module
      implements almost verbatim.
    - docs/adr/0029-overwintering-policy.md for the dormant-before-fresh-provision preference.
    - .claude/roadmap.md step 5.7 for the exit criteria this pipeline satisfies.
    - hivemind.queen.placement.rules for the individually-tested rule functions this pipeline runs.
    - hivemind.queen.dispatcher for the snapshot helper that builds `decide`'s own `inventory`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal

import hivemind.queen.placement.rules as rules
from hivemind.cell import CombShieldLevel, TaskNeeds
from hivemind.hive import NetworkPolicy, VirtualCellSpec
from hivemind.queen.errors import QueenError
from hivemind.queen.placement.inventory import (
    DormantCandidate,
    ForageView,
    Inventory,
    RealCandidate,
    VirtualBackendCandidate,
    WaxMention,
)
from hivemind.queen.placement.models import Placement, ProvisionVirtual, ReuseDormant, ReuseReal
from hivemind.queen.placement.policy import PlacementPolicy
from waggle.ids import CellId

# The only Worker role phase 3 implements (mirrors manifest.schema.forage.REQUIRED_ROLE); a real
# per-role override needs a `role` field on TaskSpec/TaskNeeds that does not exist yet, so this is
# the one role key `prefer_for` is ever asked about until a later phase adds more roles.
_ROLE_KEY = "drone"

__all__ = ["PlacementError", "decide"]


class PlacementError(QueenError):
    """Raise when neither a Real nor a Virtual candidate fits a task's `TaskNeeds`.

    Roots at `hivemind.queen.errors.QueenError`, next to no other member: ADR-0028's own
    convention keeps a subsystem's specific errors beside the one function that raises them.
    """

    code: ClassVar[str] = "hivemind.queen.placement_error"


def decide(
    needs: TaskNeeds, inventory: Inventory, forage: ForageView, policy: PlacementPolicy
) -> Placement:
    """Choose where a task with `needs` runs: an attached Real Cell, or a Virtual one.

    Args:
        needs: What the task requires from its Cell.
        inventory: Every candidate this decision may choose among, precomputed by the caller.
        forage: What `decide` needs to check "Forage covers the grant" for a Virtual candidate.
        policy: The `[placement]`-derived value object: `prefer`, `allow_hive_stand`, per-role
            overrides and the manifest's own default Virtual spec template.

    Returns:
        A `Placement` naming the highest-ranked fitting candidate (ADR-0028's own ordered rules).

    Raises:
        PlacementError: No candidate, Real or Virtual, both fits `needs` and clears every hard
            rule; the message names every rule that eliminated a candidate.
    """
    if rules.night_veil_requires_virtual(needs):
        return _place_night_veil(needs, inventory, forage, policy)

    isolation_virtual_only = rules.isolation_requires_virtual(needs)
    real_ranked, real_eliminated = (
        ((), ("isolation=REQUIRED excludes every Real Cell",))
        if isolation_virtual_only
        else _rank_real(needs, inventory, policy)
    )
    virtual_pick, virtual_eliminated = _pick_virtual(needs, inventory, forage, policy)

    placement = _prefer_first(
        policy.prefer_for(_ROLE_KEY), real_ranked, virtual_pick, real_eliminated, virtual_eliminated
    )
    if placement is not None:
        return placement

    reasons = real_eliminated + virtual_eliminated
    named = "; ".join(reasons) if reasons else "no candidates in inventory"
    raise PlacementError(f"No Cell fits {needs!r}: {named}.")


# ──────────────────────────────────────────────────────────────────────────────
# Real side: rank every fitting, unblocked candidate.
# ──────────────────────────────────────────────────────────────────────────────


def _rank_real(
    needs: TaskNeeds, inventory: Inventory, policy: PlacementPolicy
) -> tuple[tuple[RealCandidate, ...], tuple[str, ...]]:
    """Return (ranked fitting candidates, elimination reasons for every candidate that is not)."""
    eligible: list[tuple[RealCandidate, int]] = []
    eliminated: list[str] = []
    for index, candidate in enumerate(inventory.real):
        reason = _real_exclusion_reason(needs, candidate, inventory, policy)
        if reason is not None:
            eliminated.append(f"Cell {candidate.cell_id}: {reason}")
            continue
        eligible.append((candidate, index))
    # Rule 6: an uncautioned candidate outranks a cautioned one; attachment order (the original
    # index) breaks every other tie, so v0's single-Warden case still picks exactly the same Cell.
    ranked = sorted(
        eligible,
        key=lambda pair: (rules.caution_rank(pair[0].cell_id, inventory.cautioned), pair[1]),
    )
    return tuple(candidate for candidate, _ in ranked), tuple(eliminated)


def _real_exclusion_reason(
    needs: TaskNeeds, candidate: RealCandidate, inventory: Inventory, policy: PlacementPolicy
) -> str | None:
    """Return why `candidate` is excluded, or None once it clears every hard rule (rules 3-5)."""
    wax = inventory.blocked.get(candidate.cell_id)
    if wax is not None:
        return f"BLOCK Cell Wax {wax.id} ({wax.text})"
    if rules.excluded_by_hive_stand_policy(candidate.is_hive_stand, policy):
        return "allow_hive_stand=false excludes the Hive Stand"
    if not rules.fits_os(needs, candidate.capabilities):
        return f"os mismatch (needs {needs.os}, has {candidate.capabilities.os})"
    if not rules.fits_network_scopes(needs, candidate.capabilities):
        return "network scopes not reachable"
    if not rules.fits_exoskeleton(needs, candidate.capabilities):
        return "Exoskeleton needed but no display and cannot start one"
    if not rules.real_has_forage(candidate):
        return "no free Forage capacity"
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Virtual side: pick the best fitting backend/spec, preferring a dormant match.
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _VirtualPick:
    """One chosen Virtual candidate: a dormant Cell to resume, or a fresh spec to provision."""

    kind: Literal["dormant", "provision"]
    dormant: DormantCandidate | None
    backend: str
    spec: VirtualCellSpec


def _pick_virtual(
    needs: TaskNeeds, inventory: Inventory, forage: ForageView, policy: PlacementPolicy
) -> tuple[_VirtualPick | None, tuple[str, ...]]:
    """Return (the best Virtual candidate, elimination reasons for every backend that is not)."""
    eliminated: list[str] = []
    for backend in _order_backends(inventory.virtual_backends, policy):
        if not rules.virtual_has_headroom(backend.capabilities.headroom):
            eliminated.append(f"Backend {backend.name}: no headroom")
            continue
        spec = _best_spec(needs, forage, backend.specs, eliminated, backend.name)
        if spec is None:
            continue
        dormant = _matching_dormant(
            inventory.dormant, inventory.blocked, spec.image, needs.comb_shield
        )
        if dormant is not None:
            return _VirtualPick("dormant", dormant, backend.name, spec), tuple(eliminated)
        return _VirtualPick("provision", None, backend.name, spec), tuple(eliminated)
    if not inventory.virtual_backends:
        eliminated.append("No Virtual backend is registered")
    return None, tuple(eliminated)


def _best_spec(
    needs: TaskNeeds,
    forage: ForageView,
    specs: tuple[VirtualCellSpec, ...],
    eliminated: list[str],
    backend_name: str,
) -> VirtualCellSpec | None:
    """Return the first spec in `specs` that fits `needs` and Forage, recording why any do not."""
    for spec in specs:
        reason = _virtual_exclusion_reason(needs, forage, spec)
        if reason is not None:
            eliminated.append(f"Backend {backend_name} image {spec.image}: {reason}")
            continue
        return spec
    return None


def _virtual_exclusion_reason(
    needs: TaskNeeds, forage: ForageView, spec: VirtualCellSpec
) -> str | None:
    """Return why `spec` is excluded, or None once it clears rules 4a/4b/4c and 5c."""
    if not rules.virtual_fits_os(needs, spec):
        return "os mismatch"
    if not rules.virtual_fits_exoskeleton(needs, spec):
        return "Exoskeleton needed but this image does not provision one"
    if not rules.virtual_fits_network_scopes(needs, spec):
        return "network scopes not reachable"
    if not rules.virtual_has_forage(spec, forage.footprint):
        return "insufficient Forage capacity"
    return None


def _matching_dormant(
    dormant: tuple[DormantCandidate, ...],
    blocked: Mapping[CellId, WaxMention],
    image: str,
    comb_shield: CombShieldLevel,
) -> DormantCandidate | None:
    """Rule 6: prefer a dormant Cell with `image` over a fresh provision (docs/adr/0029)."""
    for candidate in dormant:
        if candidate.cell_id in blocked:
            continue  # Rule 3a applies to a dormant Cell exactly as it does to any other.
        if candidate.image == image and candidate.comb_shield is comb_shield:
            return candidate
    return None


def _order_backends(
    backends: tuple[VirtualBackendCandidate, ...], policy: PlacementPolicy
) -> tuple[VirtualBackendCandidate, ...]:
    """Sort the manifest's own configured default backend first; attachment order otherwise."""
    template = policy.default_virtual_spec
    if template is None:
        return backends
    return tuple(sorted(backends, key=lambda backend: backend.name != template.backend))


# ──────────────────────────────────────────────────────────────────────────────
# Night Veil: always a fresh Virtual Cell, forced VPN_TOR + NIGHT_VEIL, never dormant.
# ──────────────────────────────────────────────────────────────────────────────


def _place_night_veil(
    needs: TaskNeeds, inventory: Inventory, forage: ForageView, policy: PlacementPolicy
) -> Placement:
    """Rule 2: NIGHT_VEIL is always a fresh Virtual Cell; dormant Cells are never even looked at."""
    eliminated: list[str] = []
    for backend in _order_backends(inventory.virtual_backends, policy):
        if not rules.virtual_has_headroom(backend.capabilities.headroom):
            eliminated.append(f"Backend {backend.name}: no headroom")
            continue
        base = _best_spec(needs, forage, backend.specs, eliminated, backend.name)
        if base is None:
            continue
        # model_copy skips validation (pydantic v2), but every field it sets here is exactly the
        # pair VirtualCellSpec's own validators require together: VPN_TOR <-> NIGHT_VEIL, and an
        # empty allowlist for every policy but ALLOWLIST -- the result is provably still valid.
        spec = base.model_copy(
            update={
                "comb_shield": CombShieldLevel.NIGHT_VEIL,
                "network_policy": NetworkPolicy.VPN_TOR,
                "network_allowlist": (),
            }
        )
        return ProvisionVirtual(
            spec=spec,
            backend=backend.name,
            reason="NIGHT_VEIL always provisions a fresh Virtual Cell (codingrules section 8.7).",
        )
    if not inventory.virtual_backends:
        eliminated.append("No Virtual backend is registered")
    named = "; ".join(eliminated) if eliminated else "no candidates in inventory"
    raise PlacementError(f"NIGHT_VEIL requires a Virtual Cell but none fit: {named}.")


# ──────────────────────────────────────────────────────────────────────────────
# Assembling the final Placement.
# ──────────────────────────────────────────────────────────────────────────────


def _prefer_first(
    prefer: Literal["real", "virtual"],
    real_ranked: tuple[RealCandidate, ...],
    virtual_pick: _VirtualPick | None,
    real_eliminated: tuple[str, ...],
    virtual_eliminated: tuple[str, ...],
) -> Placement | None:
    """Rule 6: return the preferred side's own top candidate, or the other side with its reason."""
    if prefer == "real":
        if real_ranked:
            top = real_ranked[0]
            reason = f"[placement] prefer=real: placed on {top.cell_id}."
            return ReuseReal(top.cell_id, top.warden_id, reason)
        if virtual_pick is not None:
            named = "; ".join(real_eliminated) or "no Real candidate fit"
            reason = f"[placement] prefer=real found no Real Cell ({named}); using Virtual instead."
            return _to_virtual_placement(virtual_pick, reason)
        return None
    if virtual_pick is not None:
        reason = "[placement] prefer=virtual: placed on a Virtual Cell."
        return _to_virtual_placement(virtual_pick, reason)
    if real_ranked:
        top = real_ranked[0]
        named = "; ".join(virtual_eliminated) or "no Virtual candidate fit"
        reason = (
            f"[placement] prefer=virtual found no Virtual capacity ({named}); "
            f"using Real Cell {top.cell_id} instead."
        )
        return ReuseReal(top.cell_id, top.warden_id, reason)
    return None


def _to_virtual_placement(pick: _VirtualPick, reason: str) -> Placement:
    """Build the ReuseDormant or ProvisionVirtual `pick` names, with `reason`."""
    if pick.kind == "dormant":
        if pick.dormant is None:
            # `_pick_virtual` only ever sets kind="dormant" together with a DormantCandidate; a
            # None here would be this module's own bug, not a caller's, so it fails loudly.
            raise PlacementError("internal: a 'dormant' pick carried no DormantCandidate.")
        return ReuseDormant(pick.dormant.cell_id, pick.dormant.warden_id, reason)
    return ProvisionVirtual(pick.spec, pick.backend, reason)
