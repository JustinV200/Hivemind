"""Define PlacementPolicy: the `[placement]`-derived value object `decide` reads.

`PlacementPolicy` carries exactly what ADR-0028's rule 6 ("otherwise honour `prefer`") and rule 3
("`allow_hive_stand = false` excludes the Hive Stand") need, plus a `VirtualSpecTemplate` mirroring
the manifest's own `[virtual_cells]` defaults: `decide` never builds a `hivemind.hive.
VirtualCellSpec` from this alone (it has no Hive id or measured `ForageCapacity` to build one
with -- `hivemind.queen.placement.inventory.Inventory.virtual_backends` already carries ready-made
specs), so the template is read only to break a tie between two otherwise-equal Virtual candidates
in favour of the manifest's own configured default backend and image
(`hivemind.queen.placement.decide._order_backends`). This module holds ONLY that value object:
nothing here reads a manifest file or does any I/O of its own (codingrules section 8.3, "pure
core, effectful edges") -- `hivemind.manifest.schema.placement.PlacementSection`/
`VirtualCellsSection` are the TOML-facing shapes the composition root converts into one of these.

Roadmap step 5.7a (this module): beyond ADR-0028's hard rule 2 (`comb_shield == NIGHT_VEIL` makes
`decide` return Virtual-only, never dormant, with `NetworkPolicy.VPN_TOR` and `comb_shield
NIGHT_VEIL` on the spec -- see `hivemind.queen.placement.rules.night_veil_requires_virtual` and
`hivemind.queen.placement.decide._place_night_veil`, both already built), this module adds the two
checks ADR-0030 names as part of placement itself: the request must be explicitly human-originated,
and every model slot the Cell would need must resolve to a local provider. `NightVeilConstraints` is
the `[security]` Night Veil tier profile's own placement-facing shape (built by a composition root
from `hivemind.manifest.schema.security.tiers.TierProfile`, a report item -- see this module's own
docstring for what that conversion looks like); `NightVeilHostingView` is a small, pre-computed
signal `hivemind.queen.forage.night_veil.night_veil_local_only` produces once a candidate Cell's own
`HostingPlan` exists (or, before that Cell exists, a permissive default meaning "not yet knowable,
checked once the plan is written" -- see `check_night_veil`'s own docstring); `check_night_veil` is
the pure function that turns `TaskNeeds`, a `RequestOrigin`, a `NightVeilHostingView` and this
policy's own `night_veil` constraints into a tuple of violation strings, called by
`hivemind.queen.placement.decide._place_night_veil` (ADR-0028's rule 2), which raises
`PlacementError` naming every one.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.placement`
    sub-package. Built by whichever composition root converts a loaded `HiveManifest` (the CLI in
    production, a test builder in tests) from `HiveManifest.placement`/`.virtual_cells`/
    `.security.tiers[NIGHT_VEIL]`; read by `hivemind.queen.placement.decide.decide` as its own
    fourth argument. Calls into `hivemind.cell` (RequestOrigin, TaskNeeds, CombShieldLevel) and
    `hivemind.hive` (NetworkPolicy) only.

Key invariants:
    - `PlacementPolicy`, `VirtualSpecTemplate`, `NightVeilConstraints` and `NightVeilHostingView`
      are frozen, slotted dataclasses (codingrules section 8.5): each is a value read once per
      `decide` call, never mutated.
    - `PlacementPolicy()` (every field at its default) means "prefer Real, allow the Hive Stand, no
      per-role override, no Virtual template, no Night Veil profile configured" -- exactly v0's own
      behaviour, so a caller that never builds one from a manifest still gets today's placement
      unchanged (a NIGHT_VEIL task then always fails placement, since `check_night_veil` treats a
      `None` `night_veil` as "the tier is not configured on this Hive").
    - `role_overrides` is keyed by the lowercase `waggle.messages.task.WorkerRole` member name,
      mirroring every other per-role manifest table (`hivemind.manifest.schema.forage.
      ForageSection.roles`); `prefer_for` falls back to `prefer` for any role not in the mapping.
    - `check_night_veil` returns `()` (no violations) for any `needs.comb_shield` other than
      NIGHT_VEIL: it is safe to call unconditionally, though `decide` only ever calls it from the
      NIGHT_VEIL branch.
    - `NightVeilHostingView()` (every field at its default) means "every slot local, nothing known
      to violate yet" -- the permissive default a caller passes before a candidate Cell's own
      `HostingPlan` exists to check for real (module docstring above).

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the hard rules and the "otherwise
      honour prefer" rule this policy feeds.
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for "placement is constrained
      before anything is provisioned... only on a human-originated request... a hosting plan whose
      every slot resolves to a local provider... A plan that cannot be made local-only fails
      placement; it never spills" -- the decision this module implements.
    - .claude/codingrules.md section 8.7 for the Night Veil placement and routing rules.
    - hivemind.manifest.schema.placement for PlacementSection/VirtualCellsSection, the TOML shapes
      a composition root converts into one of these.
    - hivemind.manifest.schema.security.tiers for TierProfile, the TOML shape NightVeilConstraints
      is built from.
    - hivemind.queen.forage.night_veil for night_veil_local_only/restrict_to_local, the hosting-plan
      half of the same ADR-0030 rule.
    - hivemind.queen.placement.decide for decide, this value object's one reader.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from hivemind.cell import CombShieldLevel, RequestOrigin, TaskNeeds
from hivemind.hive import NetworkPolicy

__all__ = [
    "NightVeilConstraints",
    "NightVeilHostingView",
    "PlacementPolicy",
    "Prefer",
    "VirtualSpecTemplate",
    "check_night_veil",
]

# Which side placement prefers when both a Real and a Virtual candidate fit (ADR-0028 rule 6).
Prefer = Literal["real", "virtual"]


@dataclass(frozen=True, slots=True)
class VirtualSpecTemplate:
    """The manifest's own default shape for a freshly provisioned Virtual Cell (`[virtual_cells]`).

    Attributes:
        image: The default `images/<name>` a fresh Virtual Cell boots.
        cpu_cores: Logical cores the default template reserves.
        memory_bytes: Memory the default template reserves.
        disk_bytes: Disk the default template reserves.
        network_policy: The default outbound network shape (`hivemind.hive.NetworkPolicy`).
        backend: The `hivemind.hive.CellBackend` registry name this template provisions from.
    """

    image: str
    cpu_cores: float
    memory_bytes: int
    disk_bytes: int
    network_policy: NetworkPolicy
    backend: str


@dataclass(frozen=True, slots=True)
class NightVeilConstraints:
    """The `[security]` Night Veil tier profile's own placement-facing shape (roadmap step 5.7a).

    Built by a composition root from `hivemind.manifest.schema.security.tiers.SecuritySection.
    tiers[CombShieldLevel.NIGHT_VEIL]` (a `TierProfile`): `required_network_policy` is always
    `NetworkPolicy.VPN_TOR` (`TierProfile.egress_profile == "vpn_tor"`), `hive_stand_onion_address`
    is `TierProfile.hidden_service_address`, `socks_proxy_url` is `TierProfile.tor_socks`, and
    `locale_profile` is `TierProfile.locale_profile`. Kept as this module's own value object,
    distinct from the manifest's TOML-facing shape, for the same reason `VirtualSpecTemplate` is:
    `hivemind.manifest` sits below `hivemind.queen` in the layer table (codingrules section 4) and
    is never imported here.

    Attributes:
        required_network_policy: The network profile a NIGHT_VEIL spec must carry; always
            `NetworkPolicy.VPN_TOR` in practice (`hivemind.hive.models.VirtualCellSpec`'s own
            validator already enforces this pairing on the spec itself), named here so
            `check_night_veil` reads it from data rather than a hard-coded literal.
        hive_stand_onion_address: The Hive Stand's own Tor hidden-service address the Cell's Waggle
            transport must dial, never a clearnet address (codingrules section 8.7). Placement only
            checks this is configured at all; dialling it is the Warden's own transport concern.
        socks_proxy_url: The loopback Tor SOCKS proxy URL the Cell's Waggle transport must route
            through. Threaded onto `hivemind.hive.backends.bootstrap.QueenEndpoint.socks_proxy_url`
            by the composition root building a Night Veil Cell's endpoint (a report item: this
            module does not build a `QueenEndpoint` itself).
        locale_profile: The fixed locale Night Veil's location-blind defaults pin every Cell to
            (`images/night-veil-ubuntu`, roadmap step 5.3a); placement only checks this is
            configured, never applies it itself.
    """

    required_network_policy: NetworkPolicy
    hive_stand_onion_address: str
    socks_proxy_url: str
    locale_profile: str


@dataclass(frozen=True, slots=True)
class NightVeilHostingView:
    """Whether a NIGHT_VEIL candidate's model slots can all resolve to a local provider.

    A pre-computed signal, never a live lookup (`decide` performs no I/O, ADR-0028): the caller
    builds this from `hivemind.queen.forage.night_veil.night_veil_local_only(plan, forage_map)`
    once a candidate Cell's own `HostingPlan` exists to check for real. Before that Cell exists --
    every first `decide()` call for a fresh NIGHT_VEIL provision, since a `HostingPlan` needs a live
    `Cell.id` to be written against (`hivemind.queen.forage.hosting.write_hosting_plan`) -- the
    caller passes the default `NightVeilHostingView()`: "not yet knowable, nothing known to
    violate". This does not weaken ADR-0030's "a plan that cannot be made local-only fails
    placement; it never spills": that half of the rule is enforced once the plan is actually
    written, by `hivemind.queen.forage.night_veil.restrict_to_local` raising when a slot would be
    left empty (this module's own report names the exact call site that still needs wiring, since
    it sits in `hivemind.queen.dispatcher`, outside this dispatch's file list).

    Attributes:
        all_local: True when every slot `night_veil_local_only` checked resolved to a local
            provider (or when nothing has been checked yet -- the permissive default).
        non_local_slots: Which slots did not, each entry the description
            `night_veil_local_only` returned for it; empty whenever `all_local` is True.
    """

    all_local: bool = True
    non_local_slots: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlacementPolicy:
    """`[placement]`'s own decision inputs: prefer, allow_hive_stand, per-role overrides, template.

    Attributes:
        prefer: Which side placement honours when both a Real and a Virtual candidate fit
            (ADR-0028 rule 6); `"real"` by default, matching v0's only behaviour.
        allow_hive_stand: Whether the Hive Stand may still be chosen as a Real candidate
            (ADR-0028 rule 3); `True` by default, matching today's manifests.
        role_overrides: `[placement.roles.<role>]` overrides, keyed by lowercase `WorkerRole`
            member name; empty by default, so no role diverges from `prefer` unless the manifest
            says so.
        default_virtual_spec: The manifest's own `[virtual_cells]` default template, or `None`
            when no Virtual backend is configured at all (`hivemind.manifest.schema.placement.
            VirtualCellsSection.backend` unset -- "virtual backend unset => no virtual side").
        night_veil: The `[security]` Night Veil tier profile, or `None` when this Hive's manifest
            configures no Night Veil profile at all -- `check_night_veil` then reports every
            NIGHT_VEIL task as a placement violation, since the tier cannot be honoured without one
            (roadmap step 5.7a).
    """

    prefer: Prefer = "real"
    allow_hive_stand: bool = True
    role_overrides: Mapping[str, Prefer] = field(default_factory=dict)
    default_virtual_spec: VirtualSpecTemplate | None = None
    night_veil: NightVeilConstraints | None = None

    def prefer_for(self, role_key: str) -> Prefer:
        """Return the effective `prefer` for `role_key`, falling back to the top-level value.

        Args:
            role_key: The lowercase `WorkerRole` member name (e.g. `"drone"`) to look an override
                up for.

        Returns:
            `role_overrides[role_key]` when present, else `prefer`.
        """
        return self.role_overrides.get(role_key, self.prefer)


def check_night_veil(
    needs: TaskNeeds,
    request_origin: RequestOrigin,
    hosting_plan_view: NightVeilHostingView,
    policy: PlacementPolicy,
) -> tuple[str, ...]:
    """Return every ADR-0030 Night Veil placement rule `needs`/`request_origin` violates.

    Pure (codingrules section 8.3): no I/O, no store, no clock. A no-op -- always `()` -- for any
    `needs.comb_shield` other than NIGHT_VEIL, so a caller may call this unconditionally; `decide`
    (`hivemind.queen.placement.decide._place_night_veil`) only ever calls it once already inside
    the NIGHT_VEIL branch, and folds every violation into one `PlacementError`.

    Args:
        needs: The task's own `TaskNeeds`; only `comb_shield` is read (the isolation/VPN_TOR pairs
            are already enforced by `TaskNeeds`'s and `VirtualCellSpec`'s own validators).
        request_origin: Who asked for this task (`hivemind.cell.RequestOrigin`), read off
            `TaskSpec.origin`, never `TaskNeeds` itself (roadmap step 5.7a: "who asked" is a fact
            about the request, not about what the Cell must provide).
        hosting_plan_view: What is known so far about whether this candidate's model slots can all
            resolve to a local provider; the permissive default `NightVeilHostingView()` before a
            candidate Cell's own `HostingPlan` exists (see that dataclass's own docstring).
        policy: The `[placement]`-derived policy; `policy.night_veil` carries the Night Veil tier
            profile, or `None` when this Hive configures none.

    Returns:
        One string per violated rule, empty when `needs.comb_shield` is not NIGHT_VEIL or every
        rule holds. Each string names the rule in plain language, ready to fold into a
        `PlacementError` message.
    """
    if needs.comb_shield is not CombShieldLevel.NIGHT_VEIL:
        return ()  # Every other tier: nothing for this function to say.
    violations: list[str] = [
        *_origin_violations(request_origin),
        *_profile_violations(policy.night_veil),
    ]
    # ADR-0030: "a hosting plan whose every slot resolves to a local provider... A plan that
    # cannot be made local-only fails placement; it never spills."
    if not hosting_plan_view.all_local:
        named = "; ".join(hosting_plan_view.non_local_slots)
        violations.append(f"NIGHT_VEIL requires every model slot to resolve locally: {named}.")
    return tuple(violations)


def _origin_violations(request_origin: RequestOrigin) -> tuple[str, ...]:
    """Return why `request_origin` fails ADR-0030's "only on a human-originated request" rule."""
    if request_origin is RequestOrigin.HUMAN:
        return ()
    return (f"NIGHT_VEIL requires a human-originated request; got origin={request_origin.value}.",)


def _profile_violations(night_veil: NightVeilConstraints | None) -> tuple[str, ...]:
    """Return why `night_veil` cannot honour NIGHT_VEIL: unset, or itself incomplete."""
    # Without a configured profile there is no onion address, no SOCKS proxy and no locale to
    # route or pin against, so every remaining check would otherwise have nothing to check.
    if night_veil is None:
        return (
            "NIGHT_VEIL requires a [security] Night Veil tier profile, but none is configured.",
        )
    return _constraint_violations(night_veil)


def _constraint_violations(constraints: NightVeilConstraints) -> tuple[str, ...]:
    """Return every way a configured `NightVeilConstraints` is itself incomplete."""
    violations: list[str] = []
    if constraints.required_network_policy is not NetworkPolicy.VPN_TOR:
        violations.append(
            "NIGHT_VEIL's configured tier profile names "
            f"{constraints.required_network_policy.value}, not VPN_TOR."
        )
    if not constraints.hive_stand_onion_address:
        violations.append(
            "NIGHT_VEIL's configured tier profile carries no Hive Stand hidden-service address."
        )
    if not constraints.socks_proxy_url:
        violations.append("NIGHT_VEIL's configured tier profile carries no Tor SOCKS proxy URL.")
    if not constraints.locale_profile:
        violations.append("NIGHT_VEIL's configured tier profile carries no locale profile.")
    return tuple(violations)
