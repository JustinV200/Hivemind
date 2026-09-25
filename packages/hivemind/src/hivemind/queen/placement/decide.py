"""Define PlacementError and decide: the Queen's pure Real-vs-Virtual-Cell placement pipeline.

`decide(needs, inventory, forage, policy) -> Placement` is ADR-0028's own ordered pipeline: Night
Veil first (hard rule 2, always a fresh Virtual Cell), then isolation (hard rule 1, excludes every
Real Cell), then each side's own candidates are filtered first by the goal's capability set
(roadmap step 10.3, ADR-0039: a candidate the goal does not allow is excluded before anything
else, whatever `prefer` says, with a reason naming the missing capability) and then by the hard
rules in
`hivemind.queen.placement.rules` (a `BLOCK` Cell Wax or `allow_hive_stand = false`, then fit, then
Forage) and ranked (a `CAUTION` note behind a clean candidate, a dormant Cell before a fresh
provision, attachment order breaking every other tie). Only once both sides are known does
`policy.prefer` get read at all: the preferred side wins if it has a candidate; otherwise the other
side is used and the reason says why (this is how a `BLOCK` Cell Wax on the Hive Stand turns
`prefer = "real"` into a Virtual placement, per the roadmap's own exit criterion); if neither side
has one, `PlacementError` names every rule that eliminated a candidate, on both sides, and
carries `denied`: the capabilities the goal lacked, set only when those alone left no candidate
(so the dispatcher records `guard.denied` for a refusal the goal's ceiling caused, never for a
capacity shortfall). `decide` performs no I/O of its own (ADR-0028): every candidate, every Cell
Wax note and every headroom
figure it reads already sits on `inventory`, precomputed by `hivemind.queen.dispatcher`'s snapshot
helper before this is ever called. So does a backend's rest: one whose provisions keep failing is
held back by the dispatcher a while (`VirtualBackendCandidate.held_back`), and skipped here like a
backend with no headroom left, under the reason the dispatcher gave.

Roadmap step 6.12 (ADR-0031, "Needs travel with the task"): an Exoskeleton need (a display, input,
audio or browser attachment) takes a Real Cell only where attach could honour it there -- a
browser-only need where the Cell has a browser, a desktop need where it can start a display or
lends its operator's own, an audio need where it also has a sound server, each within what the
Cell's access level ever grants -- and otherwise lands on a Virtual Cell whose spec provisions one
(the manifest's desktop image). Every Real Cell it skips is named in the reason with the one
requirement it lacks, so a `prefer = "real"` Hive falling back to Virtual explains itself.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.placement`
    sub-package. Called once per ready task by `hivemind.queen.dispatcher`. Calls into
    `hivemind.cell` (CombShieldLevel, TaskNeeds), `hivemind.guard` (Capability), `hivemind.hive`
    (NetworkPolicy, VirtualCellSpec), `hivemind.queen.errors` (QueenError), this package's own
    `inventory`/`models`/`policy`/`rules` modules and waggle only.

Key invariants:
    - `decide` is pure: given the same arguments, it always returns the same `Placement` or raises
      the same shape of `PlacementError`.
    - `decide` never reads `cell.kind`: every check runs over `TaskNeeds`, `CellCapabilities`, a
      Real Cell's access level, a `VirtualCellSpec`'s own fields, or Cell Wax set membership
      (`scripts/check_no_kind_branches.py` allowlists this whole package for exactly that reason --
      `hivemind/queen/placement/` is a path-fragment match, so every module here is covered).
    - A `BLOCK`ed or non-fitting candidate is never returned, on either side (a hypothesis property
      test in `tests/unit/queen/placement/test_decide_properties.py` checks this over random
      inventories), and neither is one the goal's capability set does not allow, nor a Cell on a
      backend held back.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the pipeline this module
      implements almost verbatim.
    - docs/adr/0029-overwintering-policy.md for the dormant-before-fresh-provision preference.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for what an Exoskeleton need
      asks of a Real Cell before placement may lend it.
    - .claude/roadmap.md steps 5.7 and 6.12 for the exit criteria this pipeline satisfies.
    - hivemind.queen.placement.rules for the individually-tested rule functions this pipeline runs.
    - hivemind.queen.dispatcher for the snapshot helper that builds `decide`'s own `inventory`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import ClassVar, Literal

import hivemind.queen.placement.rules as rules
from hivemind.cell import CombShieldLevel, TaskNeeds
from hivemind.guard import Capability, CapabilitySet
from hivemind.hive import NetworkPolicy, VirtualCellSpec
from hivemind.hive.models import NIGHT_VEIL_IMAGE
from hivemind.queen.errors import QueenError
from hivemind.queen.placement.inventory import (
    DormantCandidate,
    ForageView,
    Inventory,
    RealCandidate,
    VirtualBackendCandidate,
)
from hivemind.queen.placement.models import Placement, ProvisionVirtual, ReuseDormant, ReuseReal
from hivemind.queen.placement.policy import PlacementPolicy, check_night_veil

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

    def __init__(
        self, message: str, denied: tuple[Capability, ...] = (), *, final: bool = False
    ) -> None:
        """Build the error.

        Args:
            message: A full sentence naming every rule that eliminated a candidate.
            denied: The capabilities the goal's set lacked, only when those alone left no
                candidate (roadmap step 10.3): the dispatcher records one `guard.denied` for
                each. Empty for any other failure, however many candidates the goal excluded.
            final: True when no later pass could place the task either (roadmap step 10.3a: a
                Night Veil rule the task or this Hive's configuration breaks), so the
                dispatcher cancels it with this message rather than retrying every tick.
        """
        super().__init__(message)
        self.denied = denied
        self.final = final


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
        else _rank_real(needs, inventory, forage.goal_capabilities, policy)
    )
    virtual_pick, virtual_eliminated = _pick_virtual(needs, inventory, forage, policy)

    placement = _prefer_first(
        policy.prefer_for(_ROLE_KEY), real_ranked, virtual_pick, real_eliminated, virtual_eliminated
    )
    if placement is not None:
        return placement

    reasons = real_eliminated + virtual_eliminated
    named = "; ".join(reasons) if reasons else "no candidates in inventory"
    denied = _capability_denials(needs, inventory, forage.goal_capabilities)
    raise PlacementError(f"No Cell fits {needs!r}: {named}.", denied)


# ──────────────────────────────────────────────────────────────────────────────
# Real side: rank every fitting, unblocked candidate.
# ──────────────────────────────────────────────────────────────────────────────


def _rank_real(
    needs: TaskNeeds, inventory: Inventory, goal: CapabilitySet | None, policy: PlacementPolicy
) -> tuple[tuple[RealCandidate, ...], tuple[str, ...]]:
    """Return (ranked fitting candidates, elimination reasons for every candidate that is not)."""
    eligible: list[tuple[RealCandidate, int]] = []
    eliminated: list[str] = []
    for index, candidate in enumerate(inventory.real):
        # The goal's ceiling first (roadmap step 10.3), then ADR-0028's own hard rules.
        lacking = rules.goal_lacks(goal, rules.placement_needs(candidate))
        reason = (
            f"goal lacks {lacking}"
            if lacking is not None
            else _real_exclusion_reason(needs, candidate, inventory, policy)
        )
        if reason is not None:
            eliminated.append(f"{_label(candidate)}: {reason}")
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
    # Roadmap step 6.12: rule 4c already words what the Cell lacks (a browser, a display it may
    # start or drive, a sound server) and which fact fails it, so the reason is used as it is.
    shortfall = rules.exoskeleton_shortfall(needs, candidate.capabilities, candidate.access_level)
    if shortfall is not None:
        return shortfall
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
        unusable = _backend_unusable(backend)
        if unusable is not None:
            eliminated.append(f"Backend {backend.name}: {unusable}")
            continue
        spec = _best_spec(needs, forage, backend.specs, eliminated, backend.name)
        if spec is None:
            continue
        dormant = _matching_dormant(
            inventory, spec.image, needs.comb_shield, forage.goal_capabilities
        )
        if dormant is not None:
            return _VirtualPick("dormant", dormant, backend.name, spec), tuple(eliminated)
        return _VirtualPick("provision", None, backend.name, spec), tuple(eliminated)
    if not inventory.virtual_backends:
        eliminated.append("No Virtual backend is registered")
    return None, tuple(eliminated)


def _backend_unusable(backend: VirtualBackendCandidate) -> str | None:
    """Return why `backend` can make no Cell now (held back, or no headroom), or None."""
    if backend.held_back is not None:
        # Its provisions keep failing: the caller rests it a while, and says why.
        return backend.held_back
    if not rules.virtual_has_headroom(backend.capabilities.headroom):
        return "no headroom"
    return None


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
    """Return why `spec` is excluded, or None once it clears the goal ceiling, 4a/4b/4c and 5c."""
    lacking = rules.goal_lacks(
        forage.goal_capabilities, rules.virtual_placement_needs(spec.comb_shield)
    )
    if lacking is not None:
        return f"goal lacks {lacking}"
    if not rules.virtual_fits_os(needs, spec):
        return "os mismatch"
    if not rules.virtual_fits_exoskeleton(needs, spec):
        return "Exoskeleton needed but this spec provisions none (not a desktop image)"
    if not rules.virtual_fits_network_scopes(needs, spec):
        return "network scopes not reachable"
    if not rules.virtual_has_forage(spec, forage.footprint):
        return "insufficient Forage capacity"
    return None


def _matching_dormant(
    inventory: Inventory, image: str, comb_shield: CombShieldLevel, goal: CapabilitySet | None
) -> DormantCandidate | None:
    """Rule 6: prefer a dormant Cell with `image` over a fresh provision (docs/adr/0029)."""
    for candidate in inventory.dormant:
        if candidate.cell_id in inventory.blocked:
            continue  # Rule 3a applies to a dormant Cell exactly as it does to any other.
        if rules.goal_lacks(goal, rules.virtual_placement_needs(candidate.comb_shield)):
            continue  # The goal ceiling applies to a dormant Cell's own tier too.
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


_NIGHT_VEIL_LABEL = "hivemind.comb_shield"  # Mirrors hive.backends.docker's own label constant.


def _place_night_veil(
    needs: TaskNeeds, inventory: Inventory, forage: ForageView, policy: PlacementPolicy
) -> Placement:
    """Rule 2: NIGHT_VEIL is always a fresh Virtual Cell; dormant Cells are never even looked at.

    Roadmap step 5.7a: ADR-0028's rule 2 also requires a human-originated request and a network
    profile that can actually route Night Veil traffic (ADR-0030), checked by
    `hivemind.queen.placement.policy.check_night_veil` before any backend is even considered --
    a task that fails these never places, however much Virtual headroom is free.
    """
    _check_night_veil_rules(needs, forage, policy)
    unceiled = dataclasses.replace(forage, goal_capabilities=None)
    eliminated: list[str] = []
    for backend in _order_backends(inventory.virtual_backends, policy):
        refusal = _night_veil_refusal(backend)
        if refusal is not None:
            eliminated.append(refusal)
            continue
        base = _best_spec(needs, unceiled, backend.specs, eliminated, backend.name)
        if base is None:
            continue
        # model_copy skips validation (pydantic v2), but every field it sets here is exactly the
        # pair VirtualCellSpec's own validators require together: VPN_TOR <-> NIGHT_VEIL, and an
        # empty allowlist for every policy but ALLOWLIST -- the result is provably still valid.
        spec = base.model_copy(
            update={
                # Roadmap step 10.3a: the tier's own image, whatever the template names, since no
                # backend runs VPN_TOR on another (a phase 5 gap once the profile was built).
                "image": NIGHT_VEIL_IMAGE,
                "comb_shield": CombShieldLevel.NIGHT_VEIL,
                "network_policy": NetworkPolicy.VPN_TOR,
                "network_allowlist": (),
                # Roadmap step 5.7a: stamped on the spec itself (not only read back off
                # spec.comb_shield) so every backend's own label-only orphan sweep
                # (hive.backends.docker.backend._to_record, hive.backends.qemu's own equivalent)
                # can filter on this label alone, the same way it already filters on hive_id.
                "labels": {**base.labels, _NIGHT_VEIL_LABEL: CombShieldLevel.NIGHT_VEIL.value},
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


def _night_veil_refusal(backend: VirtualBackendCandidate) -> str | None:
    """Return why `backend` cannot take a fresh Night Veil Cell now, or None when it can.

    Only a backend declaring `can_night_veil` ever holds one (a QEMU backend does not: its
    teardown cannot yet meet codingrules section 12, so it is refused fail-closed), and only
    one that could make any Cell now (not held back after failed provisions, and with headroom).
    """
    if not backend.capabilities.can_night_veil:
        return f"Backend {backend.name}: cannot hold a Night Veil Cell"
    unusable = _backend_unusable(backend)
    if unusable is not None:
        return f"Backend {backend.name}: {unusable}"
    return None


def _check_night_veil_rules(needs: TaskNeeds, forage: ForageView, policy: PlacementPolicy) -> None:
    """Refuse a NIGHT_VEIL placement ADR-0030's rules or the goal's own set forbid.

    Raises:
        PlacementError: A Night Veil rule is broken (final), or the goal lacks `cell:virtual` or
            the tier (`denied` names it).
    """
    violations = check_night_veil(needs, forage.request_origin, forage.night_veil_hosting, policy)
    if violations:
        # Loud and once (roadmap step 10.3a): an incomplete tier profile or a non-human origin is
        # no better on the next tick, so the task is cancelled with every violation named.
        raise PlacementError(f"NIGHT_VEIL placement refused: {'; '.join(violations)}.", final=True)
    # The goal ceiling, once, for the tier every candidate here really carries (roadmap step
    # 10.3): every spec below is re-stamped NIGHT_VEIL, so its own listed tier is never checked.
    _check_night_veil_ceiling(forage)


def _check_night_veil_ceiling(forage: ForageView) -> None:
    """Refuse a NIGHT_VEIL placement the goal's set does not allow `cell:virtual` and the tier for.

    Raises:
        PlacementError: The goal lacks one of them; `denied` names it for the dispatcher.
    """
    needed = rules.virtual_placement_needs(CombShieldLevel.NIGHT_VEIL)
    lacking = rules.goal_lacks(forage.goal_capabilities, needed)
    if lacking is not None:
        raise PlacementError(f"NIGHT_VEIL placement refused: goal lacks {lacking}.", (lacking,))


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


def _label(candidate: RealCandidate) -> str:
    """Name an attached candidate in a reason: "Hive Stand" for it, else its Cell id."""
    return "Hive Stand" if candidate.is_hive_stand else f"Cell {candidate.cell_id}"


def _capability_denials(
    needs: TaskNeeds, inventory: Inventory, goal: CapabilitySet | None
) -> tuple[Capability, ...]:
    """Return what the goal lacked, when the goal alone left no candidate; else ().

    Walks every candidate `decide` looked at (the Real side only without required isolation) and
    asks the goal ceiling of each. A goal that admits even one of them failed for another reason
    (capacity, fit, wax), which is no refusal of the goal's; nor is an empty inventory.
    """
    if goal is None:
        return ()  # No ceiling: nothing the goal lacked could have excluded anything.
    tiers = [spec.comb_shield for backend in inventory.virtual_backends for spec in backend.specs]
    needed = [rules.virtual_placement_needs(tier) for tier in tiers]
    needed += [rules.virtual_placement_needs(cell.comb_shield) for cell in inventory.dormant]
    if not rules.isolation_requires_virtual(needs):
        needed += [rules.placement_needs(candidate) for candidate in inventory.real]
    lacking = [rules.goal_lacks(goal, each) for each in needed]
    if not lacking or any(capability is None for capability in lacking):
        return ()
    # Distinct, in the order candidates were met: one guard.denied per missing capability.
    return tuple(dict.fromkeys(capability for capability in lacking if capability is not None))


def _to_virtual_placement(pick: _VirtualPick, reason: str) -> Placement:
    """Build the ReuseDormant or ProvisionVirtual `pick` names, with `reason`."""
    if pick.kind == "dormant":
        if pick.dormant is None:
            # `_pick_virtual` only ever sets kind="dormant" together with a DormantCandidate; a
            # None here would be this module's own bug, not a caller's, so it fails loudly.
            raise PlacementError("internal: a 'dormant' pick carried no DormantCandidate.")
        return ReuseDormant(pick.dormant.cell_id, pick.dormant.warden_id, reason)
    return ProvisionVirtual(pick.spec, pick.backend, reason)
