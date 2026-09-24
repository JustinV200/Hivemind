"""Define the ``[guard]`` section: the operator's overrides of the Guard policy.

The Guard (``hivemind.guard``, the Hive's policy engine) starts from a policy shipped inside the
package: each role's default capability set, a hive-wide deny list and an escalation table
(ADR-0031). ``[guard]`` is how an operator changes it without touching code: ``policy_file``
replaces the shipped policy with a file of the same shape, ``[guard.roles.<role>]`` replaces one
role's ``allow`` list (and, for the ``device`` role, its ``proposed`` list), ``deny`` adds
capabilities no principal may exercise, and ``[guard.escalation]`` maps an enforcement point to
what a denial there leads to. This module checks only the shape of what it holds: every
capability entry is a non-empty string with no whitespace. Whether an entry parses, and whether a
role, point or action name exists, is decided by ``hivemind.guard`` when it builds the policy
(``load_guard_policy``), because the manifest sits a layer below ``guard`` and may not import it;
that build runs in the composition root, so a bad table still stops the Hive at start and the
error names the offending entry. ``[guard.untrusted_content]`` (roadmap step 10.6b, ADR-0035) is
the untrusted-content scanner's own slice: how much outside text it reads before matching, and the
score at which it labels text harder or drops it, per Comb Shield tier (the Cell's security tier).
Its patterns are not here: they ship as data inside ``hivemind.guard.defaults``.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``; read by ``hivemind.cli.compose.deps``,
    which resolves ``policy_file`` against the manifest and hands both to
    ``hivemind.guard.policy.load_guard_policy``. The shipped policy file has this same shape,
    less ``policy_file``, and is validated with these same models. Calls into pydantic only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - An empty ``policy_file`` means "the policy shipped in ``hivemind.guard.defaults``"; a set
      one is resolved relative to the manifest's own directory (``HiveManifest.resolve_path``),
      never read here.
    - Nothing here can widen anything by itself: a role override is still checked against every
      enforcement point's rules, and ``deny`` only ever removes.
    - Every tier's drop threshold is at or above its label threshold, and a stricter tier never
      has a laxer threshold than a looser one (MEADOW >= PROPOLIS >= NIGHT_VEIL, both numbers):
      a Night Veil Cell labels and drops at least as soon as a Meadow one.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the policy model.
    - hivemind.guard.policy.defaults for load_guard_policy, which validates the names and entries.
    - hivemind.guard.defaults for the shipped policy.toml these fields overlay, and for
      untrusted-content.toml, the scanner's patterns ``untrusted_content`` scores against.
    - docs/guard/untrusted-content.md for what the thresholds mean in practice.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The scanner's input bound: 64 KiB of text is far more than any one injection needs and small
# enough that matching every pattern stays well inside a millisecond budget per call.
DEFAULT_MAX_SCAN_CHARS = 65_536
MIN_MAX_SCAN_CHARS = 1_024  # Below this the scanner would miss an injection placed past a header.
MAX_MAX_SCAN_CHARS = 1_048_576  # Above this a hostile document could make the scanner slow.

__all__ = [
    "DEFAULT_MAX_SCAN_CHARS",
    "GuardRoleSection",
    "GuardSection",
    "ScanThresholds",
    "UntrustedContentSection",
]

# One capability string as the manifest holds it: non-empty with no whitespace. The grammar
# itself is hivemind.guard's to check, one layer up (codingrules section 4).
_CapabilitySpec = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class GuardRoleSection(BaseModel):
    """One ``[guard.roles.<role>]`` table: a role's default capability set, replacing the policy's.

    The role name is the table's key; ``hivemind.guard`` refuses a name that is not a policy role.
    """

    model_config = _MODEL_CONFIG

    allow: tuple[_CapabilitySpec, ...] = Field(
        description="The role's whole default set, replacing the policy's list for this role; "
        "may name the {scratch} placeholder, filled with a lease's scratch root."
    )
    proposed: tuple[_CapabilitySpec, ...] | None = Field(
        default=None,
        description="For the device role only: what an approval grants when the operator names "
        "no capabilities; never wider than allow. None keeps the policy's own list; any other "
        "role giving one is refused when the policy is built.",
    )


class ScanThresholds(BaseModel):
    """The two scores that change what one Comb Shield tier does with scanned outside text.

    The untrusted-content scanner (``hivemind.guard.scanner``) adds up the weights of the pattern
    families a text trips; at or above ``label`` the text still reaches the model but inside a
    harder label that says it was flagged, and at or above ``drop`` it never reaches the model at
    all (a withheld notice with its keyed hash stands in for it). Either way the flag is recorded
    as ``guard.injection_suspected``; a flag alone never stops the bee.
    """

    model_config = _MODEL_CONFIG

    label: float = Field(
        gt=0, description="The score at or above which scanned text is labelled harder."
    )
    drop: float = Field(
        gt=0, description="The score at or above which scanned text never reaches a model."
    )

    @model_validator(mode="after")
    def _drop_is_not_below_label(self) -> ScanThresholds:
        """Refuse a drop threshold below the label one: dropping is the stronger response."""
        if self.drop < self.label:
            raise ValueError(f"drop ({self.drop}) must be at or above label ({self.label}).")
        return self


class UntrustedContentSection(BaseModel):
    """``[guard.untrusted_content]``: the scanner's input bound and its thresholds per tier.

    One table per Comb Shield tier (``meadow``, ``propolis``, ``night_veil``); the manifest sits
    below ``hivemind.cell`` and cannot name the tier enum itself, so the scanner maps each tier to
    its field. A stricter tier reacts at least as early as a looser one, checked here, so an
    operator cannot make a Night Veil Cell more permissive than a Meadow one by accident.
    """

    model_config = _MODEL_CONFIG

    max_scan_chars: int = Field(
        default=DEFAULT_MAX_SCAN_CHARS,
        ge=MIN_MAX_SCAN_CHARS,
        le=MAX_MAX_SCAN_CHARS,
        description="How many characters of one text the scanner reads before matching; the rest "
        "is neither matched nor shown to a model (the verdict says how much was read), so a "
        "hostile document can never make the scanner the slow path nor ride in past it.",
    )
    meadow: ScanThresholds = Field(
        default_factory=lambda: ScanThresholds(label=3.0, drop=7.0),
        description="MEADOW (the default tier): label at 3, drop at 7. One strong family "
        "(an imperative addressed to the model) labels; three together drop.",
    )
    propolis: ScanThresholds = Field(
        default_factory=lambda: ScanThresholds(label=2.5, drop=6.0),
        description="PROPOLIS (the hardened tier): label at 2.5, drop at 6.",
    )
    night_veil: ScanThresholds = Field(
        default_factory=lambda: ScanThresholds(label=2.0, drop=5.0),
        description="NIGHT_VEIL (the anonymous tier): label at 2, drop at 5.",
    )

    @model_validator(mode="after")
    def _stricter_tiers_react_sooner(self) -> UntrustedContentSection:
        """Refuse a stricter tier whose label or drop threshold is above a looser tier's."""
        ladder = (
            ("meadow", self.meadow),
            ("propolis", self.propolis),
            ("night_veil", self.night_veil),
        )
        # Walk each adjacent pair looser -> stricter; both numbers must never rise along it.
        for (looser, lax), (stricter, strict) in pairwise(ladder):
            if strict.label > lax.label or strict.drop > lax.drop:
                raise ValueError(
                    f"[guard.untrusted_content.{stricter}] must react at least as soon as "
                    f"[guard.untrusted_content.{looser}]: its label and drop may not be higher."
                )
        return self


class GuardSection(BaseModel):
    """``[guard]``: a replacement policy file, per-role overrides, denials and escalation."""

    model_config = _MODEL_CONFIG

    policy_file: str = Field(
        default="",
        description="A Guard policy TOML file replacing the one shipped in "
        "hivemind.guard.defaults, resolved relative to the manifest's own directory; empty "
        "means the shipped policy.",
    )
    deny: tuple[_CapabilitySpec, ...] = Field(
        default=(),
        description="Capabilities no principal may exercise, whatever it holds; added to the "
        "policy's own deny list, never replacing it.",
    )
    roles: dict[str, GuardRoleSection] = Field(
        default_factory=dict,
        description="[guard.roles.<role>] overrides keyed by policy role name (queen, operator, "
        "warden, device, swarm_device, or a lowercase Worker role); a role absent here keeps the "
        "policy's own set.",
    )
    escalation: dict[str, str] = Field(
        default_factory=dict,
        description="[guard.escalation]: enforcement point name to escalation action name "
        "('refuse', 'alarm' or 'ask_human'); a point absent here keeps the policy's own action, "
        "and one the policy does not name refuses.",
    )
    untrusted_content: UntrustedContentSection = Field(
        default_factory=UntrustedContentSection,
        description="[guard.untrusted_content]: the untrusted-content scanner's input bound and "
        "its label and drop thresholds per Comb Shield tier (roadmap step 10.6b). Read from the "
        "manifest only: the Guard policy loader ignores a policy file's copy.",
    )
