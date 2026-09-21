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

Extension point for Night Veil (roadmap step 5.7a, a later agent): this step implements ADR-0028's
hard rule 2 in full ("`comb_shield == NIGHT_VEIL` must make `decide` return Virtual-only, never
dormant, with `NetworkPolicy.VPN_TOR` and `comb_shield NIGHT_VEIL` on the spec" -- see
`hivemind.queen.placement.rules.night_veil_requires_virtual` and `hivemind.queen.placement.decide.
_place_night_veil`); the "human-originated" and "local-only hosting plan" checks that same rule
also names belong to 5.7a, not here. When that step lands, it adds its own fields to this
dataclass (a `night_veil` sub-value, most likely) rather than editing `decide`'s hard-rule order.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.placement`
    sub-package. Built by whichever composition root converts a loaded `HiveManifest` (the CLI in
    production, a test builder in tests) from `HiveManifest.placement`/`.virtual_cells`; read by
    `hivemind.queen.placement.decide.decide` as its own fourth argument. Calls into `hivemind.hive`
    (NetworkPolicy) only.

Key invariants:
    - `PlacementPolicy` and `VirtualSpecTemplate` are frozen, slotted dataclasses (codingrules
      section 8.5): a policy is a value read once per `decide` call, never mutated.
    - `PlacementPolicy()` (every field at its default) means "prefer Real, allow the Hive Stand, no
      per-role override, no Virtual template" -- exactly v0's own behaviour, so a caller that never
      builds one from a manifest still gets today's placement unchanged.
    - `role_overrides` is keyed by the lowercase `waggle.messages.task.WorkerRole` member name,
      mirroring every other per-role manifest table (`hivemind.manifest.schema.forage.
      ForageSection.roles`); `prefer_for` falls back to `prefer` for any role not in the mapping.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the hard rules and the "otherwise
      honour prefer" rule this policy feeds.
    - .claude/roadmap.md step 5.7a for the Night Veil extension point named above.
    - hivemind.manifest.schema.placement for PlacementSection/VirtualCellsSection, the TOML shapes
      a composition root converts into one of these.
    - hivemind.queen.placement.decide for decide, this value object's one reader.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from hivemind.hive import NetworkPolicy

__all__ = ["PlacementPolicy", "Prefer", "VirtualSpecTemplate"]

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
    """

    prefer: Prefer = "real"
    allow_hive_stand: bool = True
    role_overrides: Mapping[str, Prefer] = field(default_factory=dict)
    default_virtual_spec: VirtualSpecTemplate | None = None

    def prefer_for(self, role_key: str) -> Prefer:
        """Return the effective `prefer` for `role_key`, falling back to the top-level value.

        Args:
            role_key: The lowercase `WorkerRole` member name (e.g. `"drone"`) to look an override
                up for.

        Returns:
            `role_overrides[role_key]` when present, else `prefer`.
        """
        return self.role_overrides.get(role_key, self.prefer)
