"""Define the ``[security]`` and ``[honey.clearance]`` sections: egress tiers and data labels.

Codingrules section 13: "``[security]`` and ``[honey.clearance]`` are declared here as schema with
defaults, so the example manifests never drift; they are enforced in phases 5, 7 and 10." This
module is that declaration, nothing more: ``SecuritySection`` names, per Comb Shield tier
(``MEADOW``, ``PROPOLIS``, ``NIGHT_VEIL``), which egress profile a task on that tier's Cell must
run through, and ``HoneyClearanceSection`` names, per tier, which Honey clearance labels (``C0``
public, ``C1`` internal, ``C2`` personal or sensitive) a Cell at that tier may read and write. The
one validator this module enforces now rather than deferring is the one codingrules section 6.1
states as a hard fact about the label itself, not an operational check: "any user personal detail
... is C2", and Night Veil Cells "only ever touch C0/C1 data" -- so ``C2`` on ``NIGHT_VEIL`` is
rejected at schema time, not merely documented.

Both tier enums come from ``waggle.messages`` directly rather than from
``hivemind.cell.tiers``'s mirror of them: codingrules section 4 ranks ``cell`` (Layer 2) above
``manifest`` (Layer 1), so ``manifest`` may not import it, and the wire form is exactly the
shape a manifest's TOML string needs to become. Whatever later loads a Cell's own
``CombShieldLevel``/``HoneyClearance`` (Layer 2 or above) converts a manifest value with that
mirror's own ``from_wire``, since both sides share the same member names and values by
construction.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``. Calls into ``waggle.messages`` only, for
    the wire ``CombShieldLevel`` and ``HoneyClearance`` enums.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - ``SecuritySection.tiers`` and ``HoneyClearanceSection.matrix`` default to an entry for every
      Comb Shield tier, so a manifest that omits either sub-table still gets sane, documented
      defaults (roadmap step 3.1: "so the example manifests never drift").
    - ``HoneyClearanceSection.matrix[NIGHT_VEIL]`` never lists ``C2`` in ``read`` or ``write``;
      this is the one enforcement this module performs itself, everything else is deferred.

See Also:
    - .claude/roadmap.md step 3.1 for the field-by-field description this module implements.
    - .claude/codingrules.md section 13 for "declared here with defaults, enforced later".
    - .claude/codingrules.md section 6.1 for the Comb Shield and Honey Clearance vocabulary.
    - .claude/codingrules.md section 4 for why this module cannot import hivemind.cell.
    - hivemind.cell.tiers for the hivemind-side mirror of the same two wire enums.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waggle.messages import CombShieldLevel, HoneyClearance

DEFAULT_COMB_SHIELD = CombShieldLevel.MEADOW  # The baseline tier: any machine, no VPN required.

__all__ = [
    "DEFAULT_COMB_SHIELD",
    "ClearanceMatrix",
    "HoneyClearanceSection",
    "HoneySection",
    "SecuritySection",
    "TierProfile",
]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class TierProfile(BaseModel):
    """One Comb Shield tier's egress and control-channel posture."""

    model_config = _MODEL_CONFIG

    egress_profile: str = Field(
        description="How task traffic on this tier must egress: 'open', 'vpn_only' or 'vpn_tor'."
    )
    vpn_profile: str = Field(
        default="", description="The OpenVPN profile name to use; empty when this tier needs none."
    )
    tor_socks: str = Field(
        default="", description="The Tor SOCKS proxy address; empty when this tier needs none."
    )
    route_checks: tuple[str, ...] = Field(
        default=(), description="Named route checks proving egress actually follows the profile."
    )
    dns_leak_checks: tuple[str, ...] = Field(
        default=(), description="Named DNS leak checks; enforced in the phase that wires egress."
    )
    control_channel: str = Field(
        default="direct",
        description="How this tier's own Waggle control channel must bind: 'direct', 'vpn_only' "
        "or 'tor_hidden_service'.",
    )


class SecuritySection(BaseModel):
    """``[security]``: the default Comb Shield tier, and every tier's egress profile."""

    model_config = _MODEL_CONFIG

    default_comb_shield: CombShieldLevel = Field(
        default=DEFAULT_COMB_SHIELD, description="The tier a Cell gets when nothing else sets one."
    )
    tiers: dict[CombShieldLevel, TierProfile] = Field(
        default_factory=lambda: dict(_DEFAULT_TIER_PROFILES),
        description="Per-tier egress and control-channel posture; defaults cover all three tiers.",
    )


class ClearanceMatrix(BaseModel):
    """One Comb Shield tier's Honey Clearance read/write allowance."""

    model_config = _MODEL_CONFIG

    read: tuple[HoneyClearance, ...] = Field(
        description="Clearance labels a Cell at this tier may read."
    )
    write: tuple[HoneyClearance, ...] = Field(
        description="Clearance labels a Cell at this tier may write."
    )


class HoneyClearanceSection(BaseModel):
    """``[honey.clearance]``: default label, allowed flows, and the per-tier read/write matrix."""

    model_config = _MODEL_CONFIG

    default_label: HoneyClearance = Field(
        default=HoneyClearance.C1,
        description="The clearance a new item gets absent other evidence.",
    )
    allowed_flows: tuple[str, ...] = Field(
        default=(), description="Permitted label transitions, each written 'C0->C1' style."
    )
    matrix: dict[CombShieldLevel, ClearanceMatrix] = Field(
        default_factory=lambda: dict(_DEFAULT_CLEARANCE_MATRIX),
        description="Per-tier read/write allowance; defaults cover all three tiers.",
    )

    @model_validator(mode="after")
    def _night_veil_never_reaches_c2(self) -> HoneyClearanceSection:
        """Reject C2 in NIGHT_VEIL's read or write set (codingrules 6.1: it only touches C0/C1)."""
        night_veil = self.matrix.get(CombShieldLevel.NIGHT_VEIL)
        if night_veil is not None and (
            HoneyClearance.C2 in night_veil.read or HoneyClearance.C2 in night_veil.write
        ):
            raise ValueError(
                "[honey.clearance.matrix.NIGHT_VEIL] names C2; a Night Veil Cell only ever "
                "touches C0/C1 data (codingrules section 6.1)."
            )
        return self


class HoneySection(BaseModel):
    """``[honey]``: the wrapper TOML's dotted `[honey.clearance]` header nests under.

    A dotted TOML section header (`[honey.clearance]`) parses into a genuinely nested table, not a
    flat key named ``"honey.clearance"``; this thin wrapper is what lets `HiveManifest.honey`
    carry that nesting the same way `LlmSection.providers` carries `[llm.providers.<name>]`'s.
    Phase 3 gives `[honey]` exactly one sub-section; a later phase may add siblings beside it.
    """

    model_config = _MODEL_CONFIG

    clearance: HoneyClearanceSection = Field(
        default_factory=HoneyClearanceSection,
        description="Honey Clearance defaults and the per-tier read/write matrix.",
    )


# Sane per-tier defaults (roadmap step 3.1): MEADOW is the open baseline, PROPOLIS is VPN-only,
# NIGHT_VEIL layers Tor on top of the VPN and moves its own control channel off the open network.
_DEFAULT_TIER_PROFILES: dict[CombShieldLevel, TierProfile] = {
    CombShieldLevel.MEADOW: TierProfile(egress_profile="open", control_channel="direct"),
    CombShieldLevel.PROPOLIS: TierProfile(egress_profile="vpn_only", control_channel="vpn_only"),
    CombShieldLevel.NIGHT_VEIL: TierProfile(
        egress_profile="vpn_tor", control_channel="tor_hidden_service"
    ),
}

# MEADOW and PROPOLIS read and write the full label range; NIGHT_VEIL is capped at C1 by the
# validator above as well as by this default, so an operator who deletes the table still gets it.
_FULL_RANGE = (HoneyClearance.C0, HoneyClearance.C1, HoneyClearance.C2)
_LIMITED_RANGE = (HoneyClearance.C0, HoneyClearance.C1)
_DEFAULT_CLEARANCE_MATRIX: dict[CombShieldLevel, ClearanceMatrix] = {
    CombShieldLevel.MEADOW: ClearanceMatrix(read=_FULL_RANGE, write=_FULL_RANGE),
    CombShieldLevel.PROPOLIS: ClearanceMatrix(read=_FULL_RANGE, write=_FULL_RANGE),
    CombShieldLevel.NIGHT_VEIL: ClearanceMatrix(read=_LIMITED_RANGE, write=_LIMITED_RANGE),
}
