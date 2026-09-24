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
Its patterns are not here: they ship as data inside ``hivemind.guard.defaults``. Roadmap step 10.6
(ADR-0035) adds the Guard Bee's (the Hive's security watcher's) slice: ``request_confidence``, the
floor a report aimed at one Cell or one bee must reach before it is filed as a request to the
Queen, ``requests_per_hour``, the cap on those requests, and ``[guard.bee]``, its cadence, its
judge's bound, how far it raises a Capping tier's sampled-audit rate, and
``[guard.bee.rules.<key>]`` overrides of the rules it ships as data. Confidence and action names
are spelled here as the literal values of ``hivemind.guard.GuardConfidence`` and ``GuardAction``,
since this layer may not import them; a test holds the two spellings in step.
``dire_patterns`` (roadmap step 10.6a, ADR-0035) names the Guard Bee's rule keys whose requests the
Queen acts on by autopilot rule, with no awake episode: isolate the Cell (or, on the Hive Stand,
quarantine the bee and hold its goal off it); every other request is judged awake.

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
    - ``[guard.bee]`` can only tune how the Guard Bee watches: whether a rule's key exists, and
      whether an override leaves a valid rule, is decided by the Guard Bee when it loads its rules
      in the composition root, so a bad override stops the Hive at start.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the policy model.
    - hivemind.guard.policy.defaults for load_guard_policy, which validates the names and entries.
    - hivemind.guard.defaults for the shipped policy.toml these fields overlay, and for
      untrusted-content.toml, the scanner's patterns ``untrusted_content`` scores against.
    - docs/guard/untrusted-content.md for what the thresholds mean in practice.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The scanner's input bound: 64 KiB of text is far more than any one injection needs and small
# enough that matching every pattern stays well inside a millisecond budget per call.
DEFAULT_MAX_SCAN_CHARS = 65_536
MIN_MAX_SCAN_CHARS = 1_024  # Below this the scanner would miss an injection placed past a header.
MAX_MAX_SCAN_CHARS = 1_048_576  # Above this a hostile document could make the scanner slow.
# Roadmap step 10.6: only a rule that is sure files a request, so a noisy rule cannot crowd the
# Queen's attention (ADR-0035's Consequences); every weaker report is still a guard.alert.
DEFAULT_REQUEST_CONFIDENCE: Final = "high"
DEFAULT_REQUESTS_PER_HOUR = 6  # One every ten minutes: far above a healthy Hive's rate of findings.
MAX_REQUESTS_PER_HOUR = 600  # Past one every six seconds a cap no longer caps anything.
DEFAULT_GUARD_BEE_INTERVAL_S = 5.0  # How often the Guard Bee reads the trail: quick, never busy.
DEFAULT_COALESCE_WINDOW_S = 900.0  # Repeats of one rule on one Cell inside 15 minutes are one ask.
DEFAULT_JUDGE_TIMEOUT_S = (
    60.0  # One judge call, thinking included; past it the rule's verdict stands.
)
DEFAULT_AUDIT_RAISE_STEP = 0.25  # One raise samples a quarter more of a tier's completed work.
DEFAULT_AUDIT_RAISE_HOLD_S = 86_400.0  # A raise lasts a day, then the tier table's rate applies.
MAX_GUARD_WINDOW_S = 604_800.0  # A week: the longest window any Guard Bee setting may name.

# The rule keys the Queen acts on by rule (roadmap step 10.6a). Shipped: the correlation roadmap
# step 10.6 singles out, a scanner flag then a denial in the same episode (a bee that read an
# injection and then tried to act on it); every other rule's request is judged awake.
DEFAULT_DIRE_PATTERNS = ("injection_then_denial",)
MAX_DIRE_PATTERNS = 64  # Far more rules than the Guard Bee ships; a list is data, never unbounded.

__all__ = [
    "DEFAULT_AUDIT_RAISE_HOLD_S",
    "DEFAULT_AUDIT_RAISE_STEP",
    "DEFAULT_COALESCE_WINDOW_S",
    "DEFAULT_DIRE_PATTERNS",
    "DEFAULT_GUARD_BEE_INTERVAL_S",
    "DEFAULT_JUDGE_TIMEOUT_S",
    "DEFAULT_MAX_SCAN_CHARS",
    "DEFAULT_REQUESTS_PER_HOUR",
    "DEFAULT_REQUEST_CONFIDENCE",
    "MAX_GUARD_WINDOW_S",
    "MAX_REQUESTS_PER_HOUR",
    "GuardActionName",
    "GuardBeeRuleOverride",
    "GuardBeeSection",
    "GuardConfidenceName",
    "GuardRoleSection",
    "GuardSection",
    "ScanThresholds",
    "UntrustedContentSection",
]

# One capability string as the manifest holds it: non-empty with no whitespace. The grammar
# itself is hivemind.guard's to check, one layer up (codingrules section 4).
_CapabilitySpec = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]
# One Guard Bee rule key, in the shape a GuardReport's `rule` takes (hivemind.guard.report).
_RuleKey = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.]*$")]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

# hivemind.guard.GuardConfidence's values, lowest first, and GuardAction's: spelled out because the
# manifest sits below hivemind.guard (codingrules section 4); a test holds each list to its enum.
GuardConfidenceName = Literal["low", "medium", "high", "critical"]
GuardActionName = Literal[
    "isolate_cell",
    "quarantine_bee",
    "sting_cut",
    "raise_audit_rate",
    "reduce_entrance",
    "observe",
]


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


class GuardBeeRuleOverride(BaseModel):
    """One ``[guard.bee.rules.<key>]`` table: an operator's change to one shipped Guard Bee rule.

    Every field is optional; one left out keeps the shipped value. What a rule counts (its
    matchers) is not overridable here: that is the rule, and a different one is a new rule in the
    shipped data. The Guard Bee refuses a key it does not ship, and an override that leaves an
    invalid rule (a narrowing action with judgement, say), when it loads its rules.
    """

    model_config = _MODEL_CONFIG

    enabled: bool | None = Field(default=None, description="False switches the rule off.")
    window_s: float | None = Field(
        default=None, gt=0, le=MAX_GUARD_WINDOW_S, description="Seconds of trail the rule counts."
    )
    threshold: float | None = Field(
        default=None,
        gt=0,
        description="The count, distinct count or summed cost at which the rule fires; for a "
        "ratio rule, the fewest counted-against events before the ratio means anything.",
    )
    ratio: float | None = Field(
        default=None, gt=0, le=1, description="A ratio rule's firing fraction; others refuse it."
    )
    confidence: GuardConfidenceName | None = Field(
        default=None, description="How sure the rule's own verdict is."
    )
    action: GuardActionName | None = Field(
        default=None, description="What the rule's own verdict recommends."
    )
    judgement: bool | None = Field(
        default=None,
        description="True hands each finding to one awake episode on the judge slot first; a "
        "rule whose action narrows the whole Hive never takes judgement.",
    )


class GuardBeeSection(BaseModel):
    """``[guard.bee]``: how the Guard Bee reads the trail, judges, narrows, and which rules differ.

    The Guard Bee (roadmap step 10.6, ADR-0035) runs on the Queen's tick; this table sets its
    cadence, the bound on one awake episode, how far one raise lifts a Capping tier's sampled-audit
    rate and for how long, the window in which repeats of one rule against one Cell are coalesced
    into one request, and per-rule overrides of the shipped rule data.
    """

    model_config = _MODEL_CONFIG

    interval_s: float = Field(
        default=DEFAULT_GUARD_BEE_INTERVAL_S,
        gt=0,
        le=3_600,
        description="Seconds between two readings of the trail; the Queen's tick runs it when due.",
    )
    coalesce_window_s: float = Field(
        default=DEFAULT_COALESCE_WINDOW_S,
        ge=0,
        le=MAX_GUARD_WINDOW_S,
        description="Repeats of one rule against one Cell inside this window are coalesced into "
        "the first request (still a guard.alert each); 0 files every one.",
    )
    judge_timeout_s: float = Field(
        default=DEFAULT_JUDGE_TIMEOUT_S,
        gt=0,
        le=3_600,
        description="The longest one awake episode may take; past it the rule's verdict stands.",
    )
    audit_raise_step: float = Field(
        default=DEFAULT_AUDIT_RAISE_STEP,
        gt=0,
        le=1,
        description="How far one raise lifts a Capping tier's sampled-audit rate, capped at 1.0.",
    )
    audit_raise_hold_s: float = Field(
        default=DEFAULT_AUDIT_RAISE_HOLD_S,
        gt=0,
        le=MAX_GUARD_WINDOW_S,
        description="How long a raise lasts before the tier table's own rate applies again.",
    )
    rules: dict[str, GuardBeeRuleOverride] = Field(
        default_factory=dict,
        description="[guard.bee.rules.<key>] overrides keyed by shipped rule key; a rule absent "
        "here keeps its shipped values.",
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
    """``[guard]``: policy file, role overrides, denials, escalation, and the Guard Bee's slice."""

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
    # Roadmap step 10.6 (ADR-0035): the Guard Bee's floor and cap on requests, and its own table.
    request_confidence: GuardConfidenceName = Field(
        default=DEFAULT_REQUEST_CONFIDENCE,
        description="The confidence a Guard Bee report aimed at one Cell or one bee must reach to "
        "be filed as a request to the Queen; a weaker one is a guard.alert and nothing more.",
    )
    requests_per_hour: int = Field(
        default=DEFAULT_REQUESTS_PER_HOUR,
        ge=1,
        le=MAX_REQUESTS_PER_HOUR,
        description="The most requests the Guard Bee files in any hour; one over the cap is still "
        "a guard.alert, recorded as capped.",
    )
    bee: GuardBeeSection = Field(
        default_factory=GuardBeeSection,
        description="[guard.bee]: the Guard Bee's cadence, judge bound, audit raise and overrides.",
    )
    untrusted_content: UntrustedContentSection = Field(
        default_factory=UntrustedContentSection,
        description="[guard.untrusted_content]: the untrusted-content scanner's input bound and "
        "its label and drop thresholds per Comb Shield tier (roadmap step 10.6b). Read from the "
        "manifest only: the Guard policy loader ignores a policy file's copy.",
    )
    dire_patterns: tuple[_RuleKey, ...] = Field(
        default=DEFAULT_DIRE_PATTERNS,
        max_length=MAX_DIRE_PATTERNS,
        description="The Guard Bee rule keys whose requests the Queen decides by autopilot rule "
        "(roadmap step 10.6a): isolate the Cell, or on the Hive Stand quarantine the bee and "
        "hold its goal off it. A key naming no rule never fires; every other request is judged "
        "by an awake episode.",
    )
