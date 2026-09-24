"""Define LeavePolicyTable and load_leave_policy: leave-policy.toml, as data, validated.

Codingrules section 13: "policy as data." Mirrors `hivemind.supervision.capping.tiers.load_tiers`'s
own shape exactly, reading `supervision/defaults/leave-policy.toml` through `importlib.resources`
(so it resolves the same from a checkout and from an installed wheel) unless a caller names a
manifest-configured file instead. The table has one row per `hivemind.supervision.capping.leave.
model.PathClass` (`keep_root`, `executable`, `startup`, `system`, `other`, `home`) plus one general
setting, `max_home_bytes`, the size threshold `hivemind.supervision.capping.leave.policy.decide`
reads for the `home` row's own over-threshold branch.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    leave`. Loaded once by whichever composition root builds a `hivemind.wardens.deps.WardenDeps`
    (production: `cli/compose/deps.py`; tests: `tests/builders/wardens.make_warden_deps`), the
    same way `hivemind.supervision.capping.tiers.load_tiers` already is. Read by `hivemind.
    supervision.capping.leave.policy.decide`. Calls into `hivemind.supervision.capping.errors` and
    `hivemind.supervision.capping.leave.model` and the standard library only.

Key invariants:
    - Every model here is frozen and forbids extras, like every boundary value in this repository.
    - `load_leave_policy` raises CappingError for a missing file, a TOML syntax error, or a
      document that fails validation; it never returns a partially-built table.
    - `max_home_bytes` is always > 0: a zero or negative threshold would make every home-class
      leaving "over threshold", which the operator can already express with `home.hive_stand_
      verdict = "ASK"` instead -- a footgun this validator closes off.

See Also:
    - .claude/roadmap.md step 5.0c for the policy table this module loads.
    - .claude/codingrules.md section 13 for "policy as data."
    - supervision/defaults/leave-policy.toml for the Hive's shipped v0 table.
    - hivemind.supervision.capping.tiers for load_tiers, the sibling loader this module mirrors.
    - hivemind.supervision.capping.leave.policy for decide, this table's one reader.
"""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.leave.model import LeaveVerdict

DEFAULT_LEAVE_POLICY_FILENAME = "leave-policy.toml"  # The shipped table, inside _DEFAULTS_PACKAGE.
# The data-only package the shipped leave policy lives in, addressed by dotted name so it resolves
# the same from a checkout and from an installed wheel (mirrors tiers.py's own _DEFAULTS_PACKAGE).
_DEFAULTS_PACKAGE = "hivemind.supervision.defaults"

__all__ = [
    "DEFAULT_LEAVE_POLICY_FILENAME",
    "ClassPolicy",
    "GeneralSettings",
    "HomePolicy",
    "LeavePolicyClasses",
    "LeavePolicyTable",
    "SingleVerdict",
    "load_leave_policy",
]


class SingleVerdict(BaseModel):
    """One row with a single verdict for every Cell: the `keep_root` and `executable` rows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: LeaveVerdict = Field(description="The verdict for every Cell, Hive Stand or borrowed.")


class ClassPolicy(BaseModel):
    """One path class's verdict, split by whether the Cell is the Hive Stand or borrowed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hive_stand_verdict: LeaveVerdict = Field(description="The verdict on the Hive Stand itself.")
    borrowed_verdict: LeaveVerdict = Field(description="The verdict on a borrowed device.")


class HomePolicy(ClassPolicy):
    """The `home` row: `ClassPolicy` plus the verdict once a leaving is over `max_home_bytes`."""

    over_threshold_verdict: LeaveVerdict = Field(
        description="The verdict once a home-class leaving's size exceeds max_home_bytes, "
        "whichever kind of Cell it is on."
    )


class GeneralSettings(BaseModel):
    """The table's one general setting: the home-class size threshold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_home_bytes: int = Field(
        gt=0,
        description="Above this many bytes, a declared home-class leaving is decided by `home."
        "over_threshold_verdict` instead of `home.hive_stand_verdict`/`borrowed_verdict`.",
    )


class LeavePolicyClasses(BaseModel):
    """Every `hivemind.supervision.capping.leave.model.PathClass`'s own row, keep_root to home."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    keep_root: SingleVerdict = Field(description="The PathClass.KEEP_ROOT row.")
    executable: SingleVerdict = Field(
        description="The row for an executable path outside keep_root, whatever its own class."
    )
    startup: ClassPolicy = Field(description="The PathClass.STARTUP row.")
    system: ClassPolicy = Field(description="The PathClass.SYSTEM row.")
    other: ClassPolicy = Field(
        description="The PathClass.OTHER row: a path classify_path recognised as neither home, "
        "system, startup nor keep_root. Conservative by default (module docstring's own README "
        "pointer): never more permissive than `system`."
    )
    home: HomePolicy = Field(description="The PathClass.HOME row.")


class LeavePolicyTable(BaseModel):
    """The whole of leave-policy.toml, validated: general settings plus every class's row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    general: GeneralSettings = Field(description="Settings that are not per-class.")
    classes: LeavePolicyClasses = Field(description="Every PathClass's own verdict row.")


def load_leave_policy(path: Path | None = None) -> LeavePolicyTable:
    """Load and validate a LeavePolicyTable from a TOML file, or from the shipped default.

    Args:
        path: The leave policy file, from wherever a composition root configures it. None, the
            default, reads the table shipped inside `hivemind.supervision.defaults`, the same way
            `hivemind.supervision.capping.tiers.load_tiers` reads its own.

    Returns:
        The validated LeavePolicyTable.

    Raises:
        CappingError: `path` was given and does not exist or cannot be read, or the document read
            is not valid TOML or fails this table's own pydantic validation.
    """
    source = str(path) if path is not None else f"the shipped {DEFAULT_LEAVE_POLICY_FILENAME}"
    try:
        raw = tomllib.loads(_read_policy_text(path))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CappingError(f"Could not read leave policy from {source}: {exc}") from exc

    try:
        return LeavePolicyTable.model_validate(raw)
    except ValidationError as exc:
        raise CappingError(f"Leave policy at {source} is invalid: {exc}") from exc


def _read_policy_text(path: Path | None) -> str:
    """Return the leave policy document's text, from `path` or from the shipped package resource."""
    if path is not None:
        return path.read_text(encoding="utf-8")
    return (files(_DEFAULTS_PACKAGE) / DEFAULT_LEAVE_POLICY_FILENAME).read_text(encoding="utf-8")
