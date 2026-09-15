"""Define JudgeRubric and load_judge_rubrics: the per-risk-tier rubric an independent judge reads.

Codingrules section 8.12: "Judge review runs on ModelSlot.JUDGE with a rubric and no shared
context with the proposer." A rubric is what the judge checks a proposal against -- one per
`RiskTier` (`hivemind.supervision.capping.tiers`), because what counts as acceptable at
`read_only` (nothing to review) is not what counts as acceptable at `irreversible` (no rollback to
fall back on). `load_judge_rubrics` reads `supervision/defaults/judge-rubrics.toml` the same way
`hivemind.supervision.capping.tiers.load_tiers` and `hivemind.supervision.policy.load_policy` read
their own shipped tables: through `importlib.resources`, so the rubrics ship inside the installed
distribution and resolve identically from a checkout, a wheel, or neither.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    checks`. Read once by whichever composition root builds a `hivemind.supervision.capping.
    checks.judge.JudgeCheck` (a later dispatch, at the Warden layer); the rubric itself is carried
    on every `hivemind.supervision.capping.checks.judge.JudgeRequest`. Calls into `hivemind.
    supervision.capping.errors`, `hivemind.supervision.capping.tiers` and the standard library
    only.

Key invariants:
    - JudgeRubric is frozen and forbids extras, like every boundary value in this repository.
    - load_judge_rubrics raises CappingError for a missing file, a TOML syntax error, or a document
      that fails validation; it never returns a partially-built mapping.
    - A TOML `[rubrics.<name>]` section name is the RiskTier member name lowercased, matching the
      manifest-key convention `hivemind.supervision.capping.tiers.load_tiers` already follows for
      `[tiers.<name>]`.

See Also:
    - .claude/codingrules.md section 8.12 for the judge-independence rule this module supports.
    - .claude/codingrules.md section 13 for "policy as data", the pattern this loader follows.
    - supervision/defaults/judge-rubrics.toml for the Hive's shipped v0 rubric table.
    - hivemind.supervision.capping.tiers for load_tiers, the sibling loader this module's shape
      mirrors.
    - hivemind.supervision.capping.checks.judge for JudgeRequest, which carries one JudgeRubric.
"""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.tiers import RiskTier

DEFAULT_RUBRICS_FILENAME = "judge-rubrics.toml"  # The shipped table, inside _DEFAULTS_PACKAGE.
# The data-only package the shipped rubric table lives in, addressed by dotted name so it resolves
# the same from a checkout and from an installed wheel (see that package's own docstring).
_DEFAULTS_PACKAGE = "hivemind.supervision.defaults"
MAX_RUBRIC_ID_CHARS = 64  # A short, stable slug ("outside_scratch_write-v1"), never a sentence.
MAX_RUBRIC_TEXT_CHARS = 4_000  # A rubric is a paragraph or two, not a policy document.

__all__ = ["DEFAULT_RUBRICS_FILENAME", "JudgeRubric", "load_judge_rubrics"]


class JudgeRubric(BaseModel):
    """What an independent judge checks for at one risk tier.

    One value of the mapping `load_judge_rubrics` returns, loaded from one `[rubrics.<name>]`
    section of `supervision/defaults/judge-rubrics.toml`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rubric_id: Annotated[str, Field(max_length=MAX_RUBRIC_ID_CHARS)] = Field(
        description="Stable id echoed on every JudgeVerdict scored against this rubric, so a "
        "later change to this file's wording can be told apart, on the trail and in audit "
        "findings, from an older verdict scored under a previous version of the same tier's text."
    )
    risk_tier: RiskTier = Field(description="Which tier this rubric applies to.")
    text: Annotated[str, Field(max_length=MAX_RUBRIC_TEXT_CHARS)] = Field(
        description="The rubric body: what the judge checks for, folded into the judge's own "
        "prompt by whichever JudgeReviewer implementation calls a model."
    )


class _JudgeRubricTable(BaseModel):
    """The whole of judge-rubrics.toml, validated: one JudgeRubric per configured RiskTier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rubrics: dict[RiskTier, JudgeRubric] = Field(
        description="Every configured tier's rubric. A tier absent here has no rubric, so "
        "JudgeCheck fails closed for it (codingrules section 8.12: fail closed)."
    )

    @field_validator("rubrics", mode="before")
    @classmethod
    def _lowercase_section_names_are_tier_names(cls, value: object) -> object:
        """Translate a TOML `[rubrics.read_only]`-style key into a RiskTier member.

        Mirrors `hivemind.supervision.capping.tiers.TierTable`'s identically-named validator: a
        raw TOML mapping has lowercase string keys, while a caller building this table directly in
        Python (every builder and test) passes RiskTier members as keys already. Also folds the
        resolved key into each entry's own `risk_tier` field, so a TOML section never has to name
        its own tier twice (once as the section name, once as a `risk_tier = "..."` line) while a
        `JudgeRubric` built directly in Python still states it explicitly.

        Args:
            value: The raw `rubrics` mapping.

        Returns:
            `value` unchanged if it is not a mapping; otherwise a new mapping with every string key
            uppercased so pydantic can parse it as a RiskTier by value (and stamped onto that
            entry's `risk_tier` field when the entry is itself a plain dict without one already),
            and every non-string key (already a RiskTier member) left exactly as given.
        """
        if not isinstance(value, dict):
            return value
        result: dict[object, object] = {}
        for key, entry in value.items():
            tier_key = key.upper() if isinstance(key, str) else key
            if isinstance(entry, dict) and "risk_tier" not in entry:
                entry = {**entry, "risk_tier": tier_key}
            result[tier_key] = entry
        return result


def load_judge_rubrics(path: Path | None = None) -> dict[RiskTier, JudgeRubric]:
    """Load and validate the per-tier judge rubrics from a TOML file, or from the shipped default.

    Args:
        path: The rubric file, from wherever a composition root configures it. None, the default,
            reads the table shipped inside `hivemind.supervision.defaults`, the same way
            `hivemind.supervision.capping.tiers.load_tiers` reads its own.

    Returns:
        Every configured tier's JudgeRubric, keyed by RiskTier.

    Raises:
        CappingError: `path` was given and does not exist or cannot be read, or the document read
            is not valid TOML or fails validation.
    """
    source = str(path) if path is not None else f"the shipped {DEFAULT_RUBRICS_FILENAME}"
    try:
        raw = tomllib.loads(_read_rubrics_text(path))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CappingError(f"Could not read judge rubrics from {source}: {exc}") from exc

    try:
        table = _JudgeRubricTable.model_validate(raw)
    except ValidationError as exc:
        raise CappingError(f"Judge rubrics at {source} are invalid: {exc}") from exc
    return dict(table.rubrics)


def _read_rubrics_text(path: Path | None) -> str:
    """Return the rubric document's text, from `path` or from the shipped package resource."""
    if path is not None:
        return path.read_text(encoding="utf-8")
    return (files(_DEFAULTS_PACKAGE) / DEFAULT_RUBRICS_FILENAME).read_text(encoding="utf-8")
