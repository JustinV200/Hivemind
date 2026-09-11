"""Mirror waggle's RiskTier, and define TierSpec/TierTable: risk tiers as data.

Codingrules section 8.12: "Tiers are data, checks are layered, cheapest first." Every proposal
declares a `RiskTier` (this module's mirror of `waggle.messages.capping.RiskTier`, member for
member, with `from_wire`/`to_wire` and a sync test per codingrules section 6.1); a `TierTable`
maps each tier to a `TierSpec` -- the `CheckKind`s (waggle's own enum, not mirrored: it names no
behaviour of its own, just which rung to run) that tier requires, the subset of those that must
run even when the tier's own list is empty (`floor`, read by a later phase's Tempo-driven
shortening -- codingrules section 8.14: "It can never remove a check the tier table marks as a
floor"), whether the gate snapshots the Cell before applying, and a byte cap on an inline diff.
`load_tiers` reads `supervision/defaults/capping-tiers.toml`, the operator-facing table an operator
edits to add or loosen a tier without touching code (codingrules section 13: "policy as data").

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Loaded
    once by whichever composition root builds a `hivemind.supervision.capping.gate.GateDeps`
    (a Warden, roadmap step 3.19) from `[supervision] capping_tiers_file`; read by
    `hivemind.supervision.capping.gate.CappingGate` to decide which checks a proposal's tier
    requires. Calls into `hivemind.supervision.capping.errors` and waggle only.

Key invariants:
    - RiskTier's member names and values are identical to waggle.messages.capping.RiskTier's
      (tests/unit/supervision/capping/test_tiers.py checks it member for member).
    - TierTable and TierSpec are frozen and forbid unknown keys, like every boundary value here.
    - load_tiers raises CappingError for a missing file, a TOML syntax error, or a document that
      fails TierTable's own pydantic validation; it never returns a partially-built table.
    - A TOML `[tiers.<name>]` section name is the RiskTier member name lowercased
      ("outside_scratch_write" for OUTSIDE_SCRATCH_WRITE), matching the manifest-key convention
      (codingrules section 13) other Hive Manifest sections already use for enum keys.

See Also:
    - .claude/codingrules.md section 8.12 for "Tiers are data, checks are layered, cheapest first."
    - .claude/codingrules.md section 8.14 for why `floor` can never be removed by Tempo.
    - .claude/codingrules.md section 6.1 for the mirror-with-sync-test convention this module
      follows for RiskTier.
    - supervision/defaults/capping-tiers.toml for the Hive's shipped v0 tier table.
    - waggle.messages.capping for RiskTier (the wire form) and CheckKind (used directly, not
      mirrored).
    - hivemind.supervision.capping.errors for CappingError, the error load_tiers raises.
"""

from __future__ import annotations

import tomllib
from enum import Enum
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from hivemind.supervision.capping.errors import CappingError
from waggle.messages.capping import CheckKind
from waggle.messages.capping import RiskTier as WireRiskTier

DEFAULT_TIERS_FILENAME = "capping-tiers.toml"  # The shipped table, inside _DEFAULTS_PACKAGE.
# The data-only package the two shipped tables live in, addressed by dotted name so they resolve
# the same from a checkout and from an installed wheel (see that package's own docstring).
_DEFAULTS_PACKAGE = "hivemind.supervision.defaults"

__all__ = ["DEFAULT_TIERS_FILENAME", "RiskTier", "TierSpec", "TierTable", "load_tiers"]


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
