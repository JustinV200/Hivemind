"""Mirror waggle's RiskTier, define TierSpec/TierTable, and fold a task's tempo into the ladder.

Codingrules section 8.12: "Tiers are data, checks are layered, cheapest first." Every proposal
declares a `RiskTier` (this module's mirror of `waggle.messages.capping.RiskTier`, member for
member, with `from_wire`/`to_wire` and a sync test per codingrules section 6.1); a `TierTable` maps
each tier to a `TierSpec` -- the `CheckKind`s (waggle's own enum, not mirrored: it names no
behaviour of its own, just which rung to run) that tier requires, the subset of those that must run
even when the tier's own list is empty (`floor`, which a task's tempo may never remove --
codingrules section 8.14: "It can never remove a check the tier table marks as a floor"), whether
the gate snapshots the Cell before applying, and a byte cap on an inline diff. Roadmap step 4.10
(judge review and sampled audit) adds two more columns: `judge`, whether `CheckKind.JUDGE` belongs
in this tier's real-time ladder at all (an operator toggle, independent of listing `JUDGE` in
`checks`/`floor` by hand), and `audit_rate`, the fraction of this tier's completed proposals
`hivemind.supervision.capping.audit.sampler.AuditSampler` samples for after-the-fact judge review
when `judge` is False (codingrules section 8.12: "What cannot be gated is sampled"). `load_tiers`
reads `supervision/defaults/capping-tiers.toml`, the operator-facing table an operator edits to add
or loosen a tier without touching code (codingrules section 13: "policy as data").

`checks_for`, at the bottom of this module, is the other half of roadmap step 4.10: codingrules
section 8.14's "Capping reads tempo, within floors" rule, combining one `TierSpec` with one task's
`Tempo` (`hivemind.forage.tempo`) into the actual check ladder `CappingGate.run` walks for one
proposal. It lives beside `TierSpec` rather than in a separate module because the two are one
concept read together at exactly one call site (`hivemind.supervision.capping.gate`) and splitting
them only pushed this package's directory over codingrules 5.6's fan-out limit for no benefit
(5.2: "a split made only for size... folds back once 5.1 allows it").

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Loaded
    once by whichever composition root builds a `hivemind.supervision.capping.gate.GateDeps`
    (a Warden, roadmap step 3.19) from `[supervision] capping_tiers_file`; `checks_for` is read by
    `hivemind.supervision.capping.gate.CappingGate` (`_run_checks`) once per proposal. Calls into
    `hivemind.forage.tempo`, `hivemind.supervision.capping.errors` and waggle only.

Key invariants:
    - RiskTier's member names and values are identical to waggle.messages.capping.RiskTier's
      (tests/unit/supervision/capping/test_tiers.py checks it member for member).
    - TierTable and TierSpec are frozen and forbid unknown keys, like every boundary value here.
    - load_tiers raises CappingError for a missing file, a TOML syntax error, or a document that
      fails TierTable's own pydantic validation; it never returns a partially-built table.
    - A TOML `[tiers.<name>]` section name is the RiskTier member name lowercased
      ("outside_scratch_write" for OUTSIDE_SCRATCH_WRITE), matching the manifest-key convention
      (codingrules section 13) other Hive Manifest sections already use for enum keys.
    - checks_for never removes a floor check (a member of `tier.floor`), whatever tempo says, and
      never introduces `JUDGE` into a ladder that never carried it through `checks`/`floor`/`judge`
      in the first place; it only shortens or lengthens what a tier already names.

See Also:
    - .claude/codingrules.md section 8.12 for "Tiers are data, checks are layered, cheapest first."
    - .claude/codingrules.md section 8.14 for the tempo-reads-capping rule checks_for implements.
    - .claude/codingrules.md section 6.1 for the mirror-with-sync-test convention this module
      follows for RiskTier.
    - supervision/defaults/capping-tiers.toml for the Hive's shipped v0 tier table.
    - waggle.messages.capping for RiskTier (the wire form) and CheckKind (used directly, not
      mirrored).
    - hivemind.supervision.capping.errors for CappingError, the error load_tiers raises.
    - hivemind.supervision.capping.gate for CappingGate, checks_for's one caller.
"""

from __future__ import annotations

import tomllib
from enum import Enum
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.supervision.capping.errors import CappingError
from waggle.messages.capping import CheckKind
from waggle.messages.capping import RiskTier as WireRiskTier

DEFAULT_TIERS_FILENAME = "capping-tiers.toml"  # The shipped table, inside _DEFAULTS_PACKAGE.
# The data-only package the two shipped tables live in, addressed by dotted name so they resolve
# the same from a checkout and from an installed wheel (see that package's own docstring).
_DEFAULTS_PACKAGE = "hivemind.supervision.defaults"
# Below this many seconds of latency budget, an urgent proposal cannot afford a judge review's own
# turnaround (a model call, typically seconds to tens of seconds); checks_for may drop JUDGE
# (unless it is a floor check) once the task's budget falls under this, alongside a plain LOW bar.
SHORTEN_LATENCY_BUDGET_S = 60.0

__all__ = [
    "DEFAULT_TIERS_FILENAME",
    "SHORTEN_LATENCY_BUDGET_S",
    "RiskTier",
    "TierSpec",
    "TierTable",
    "checks_for",
    "load_tiers",
]


class RiskTier(Enum):
    """A proposal's declared risk: which check ladder applies and whether apply is snapshotted.

    Mirrors waggle.messages.capping.RiskTier member for member (codingrules section 6.1); checks
    may raise a proposal's declared tier, never lower it (waggle.messages.capping.proposals'
    ProposalSubmitted.risk_tier docstring).
    """

    READ_ONLY = "READ_ONLY"
    SCRATCH_WRITE = "SCRATCH_WRITE"
    OUTSIDE_SCRATCH_WRITE = "OUTSIDE_SCRATCH_WRITE"
    NETWORK_EGRESS = "NETWORK_EGRESS"
    SPEND = "SPEND"
    DEVICE_COMMAND = "DEVICE_COMMAND"
    IRREVERSIBLE = "IRREVERSIBLE"

    @classmethod
    def from_wire(cls, wire: WireRiskTier) -> RiskTier:
        """Convert the wire form of this tier into hivemind's own enum.

        Args:
            wire: The waggle.messages.capping.RiskTier value read off an Envelope.

        Returns:
            The hivemind RiskTier member with the same name.
        """
        return cls(wire.value)

    def to_wire(self) -> WireRiskTier:
        """Convert this tier into the wire form waggle.messages.capping carries on an Envelope.

        Returns:
            The waggle.messages.capping.RiskTier member with the same name.
        """
        return WireRiskTier(self.value)


class TierSpec(BaseModel):
    """One risk tier's check ladder: which checks, which of those are a floor, and its apply rules.

    One value of `TierTable.tiers`, loaded from one `[tiers.<name>]` section of
    `supervision/defaults/capping-tiers.toml`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    checks: tuple[CheckKind, ...] = Field(
        default=(),
        description="The checks this tier requires, cheapest first. Every member must have an "
        "implementation in the gate's Mapping[CheckKind, Check], or the proposal is rejected "
        "with reason 'check unavailable' (codingrules section 8.12: fail closed).",
    )
    floor: tuple[CheckKind, ...] = Field(
        default=(),
        description="Checks that run even when `checks` is empty, and that a later phase's "
        "Tempo-driven shortening may never remove (codingrules section 8.14).",
    )
    snapshot_before: bool = Field(
        default=False,
        description="Whether the gate snapshots the Cell before applying this tier's action, so "
        "a failed postcondition can restore the whole Cell instead of one file at a time.",
    )
    max_diff_bytes: int | None = Field(
        default=None,
        gt=0,
        description="The largest inline diff this tier's DiffSizeCapCheck allows; None means no "
        "cap (a tier whose actions never carry a diff).",
    )
    max_copy_bytes: int | None = Field(
        default=None,
        gt=0,
        description="The largest source file a COPY action (roadmap step 5.0e, the `keep` tool) "
        "may move at this tier, checked against ProposedAction.copy_size by the same "
        "DiffSizeCapCheck; None means no cap (a tier whose actions never carry a COPY).",
    )
    judge: bool = Field(
        default=False,
        description="Whether CheckKind.JUDGE belongs in this tier's real-time ladder. True adds "
        "it even when `checks`/`floor` do not name it by hand (hivemind.supervision.capping."
        "ladder.checks_for); an operator flips this on once a JudgeReviewer is wired in. False "
        "(v0's shipped default for every tier) is what makes a tier a candidate for `audit_rate`.",
    )
    audit_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="The fraction of this tier's completed proposals sampled for after-the-fact "
        "judge review (hivemind.supervision.capping.audit.sampler.AuditSampler) when `judge` is "
        "False. "
        "0.0 (no sampling) is the sensible default once `judge` is True: a live review already "
        "covers every proposal, so nothing is left ungated to sample.",
    )


class TierTable(BaseModel):
    """Every risk tier's TierSpec, keyed by RiskTier: the whole of capping-tiers.toml, validated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tiers: dict[RiskTier, TierSpec] = Field(
        description="Every configured tier's check ladder. A tier absent from this mapping has "
        "no configured checks at all; the gate rejects a proposal at such a tier with reason "
        "'tier not configured'."
    )

    @field_validator("tiers", mode="before")
    @classmethod
    def _lowercase_section_names_are_tier_names(cls, value: object) -> object:
        """Translate a TOML `[tiers.read_only]`-style lowercase key into a RiskTier member.

        Args:
            value: The raw `tiers` mapping. From `tomllib`, `[tiers.<name>]` sections decode into
                a `dict[str, ...]` keyed by the lowercase section name; a caller building a
                TierTable directly in Python (every builder and test in this codebase) instead
                passes `RiskTier` members as keys already, which must pass through unchanged.

        Returns:
            `value` unchanged if it is not a mapping (pydantic's own type check then reports the
            problem); otherwise a new mapping with every *string* key uppercased so pydantic can
            parse it as a RiskTier by value, and every non-string key (already a RiskTier member)
            left exactly as given.
        """
        if not isinstance(value, dict):
            return value
        return {(key.upper() if isinstance(key, str) else key): spec for key, spec in value.items()}


def load_tiers(path: Path | None = None) -> TierTable:
    """Load and validate a TierTable from a TOML file, or from the shipped default.

    Args:
        path: The tier file, from the manifest's `[supervision] capping_tiers_file`. None, the
            default, reads the table shipped inside `hivemind.supervision.defaults`, the same
            way `hivemind.supervision.policy.load_policy` reads its own.

    Returns:
        The validated TierTable.

    Raises:
        CappingError: `path` was given and does not exist or cannot be read, or the document read
            is not valid TOML or fails TierTable's own pydantic validation.
    """
    source = str(path) if path is not None else f"the shipped {DEFAULT_TIERS_FILENAME}"
    try:
        raw = tomllib.loads(_read_tiers_text(path))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CappingError(f"Could not read capping tiers from {source}: {exc}") from exc

    try:
        return TierTable.model_validate(raw)
    except ValidationError as exc:
        raise CappingError(f"Capping tiers at {source} are invalid: {exc}") from exc


def _read_tiers_text(path: Path | None) -> str:
    """Return the tier document's text, from `path` or from the shipped package resource."""
    if path is not None:
        return path.read_text(encoding="utf-8")
    return (files(_DEFAULTS_PACKAGE) / DEFAULT_TIERS_FILENAME).read_text(encoding="utf-8")


def checks_for(tier: TierSpec, tempo: Tempo) -> tuple[CheckKind, ...]:
    """Compute the check ladder one proposal actually walks, from its tier and its task's tempo.

    Args:
        tier: The proposal's risk tier's configured checks, floor and judge flag.
        tempo: The task's speed-against-accuracy setting.

    Returns:
        `CheckKind` members in cheapest-first order (`waggle.messages.capping.CheckKind`'s own
        declaration order); `JUDGE` may appear twice, for a CRITICAL bar's second pass.
    """
    ladder = list(_base_ladder(tier))
    has_judge = CheckKind.JUDGE in ladder
    removable = has_judge and CheckKind.JUDGE not in tier.floor
    if removable and _wants_shorter(tempo):
        # An urgent or low-bar task skips the judge on a tier where it is not a floor check.
        ladder.remove(CheckKind.JUDGE)
    elif has_judge and tempo.accuracy is AccuracyBar.CRITICAL:
        # A CRITICAL bar asks for extra scrutiny: a second, independent pass through the same
        # JUDGE rung, on top of whatever the tier's own ladder already ran.
        ladder.append(CheckKind.JUDGE)
    return tuple(ladder)


def _base_ladder(tier: TierSpec) -> tuple[CheckKind, ...]:
    """Return `tier`'s checks, floor and judge flag, unioned, in CheckKind's own declared order.

    `tier.judge` adds `CheckKind.JUDGE` to that union when `checks`/`floor` do not already carry
    it, so an operator can turn a tier's real-time judge on with one boolean rather than editing
    its `checks` list by hand.
    """
    members = set(tier.checks) | set(tier.floor)
    if tier.judge:
        members = members | {CheckKind.JUDGE}
    return tuple(kind for kind in CheckKind if kind in members)


def _wants_shorter(tempo: Tempo) -> bool:
    """Return whether `tempo` asks for a shorter ladder: a low bar, or a tight latency budget.

    A CRITICAL bar never shortens, even alongside a tight budget: it asks for more scrutiny, not
    less, so it always takes the lengthening branch above instead.
    """
    if tempo.accuracy is AccuracyBar.CRITICAL:
        return False
    tight_budget = (
        tempo.latency_budget_s is not None and tempo.latency_budget_s < SHORTEN_LATENCY_BUDGET_S
    )
    return tempo.accuracy is AccuracyBar.LOW or tight_budget
