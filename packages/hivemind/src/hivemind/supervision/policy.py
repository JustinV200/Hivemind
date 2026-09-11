"""Define EscalationPolicy: policy as data mapping (Alarm kind, attempts) to an action.

Codingrules section 8.8 and 13 (the "policy-as-data" idea): each level's escalation policy is
data, not code, loaded from TOML, so a Warden's playbook for "a Worker crashed twice" can change
without a code change. `PolicyRule` is one row: an `AlarmKind` (or `None` for a wildcard row that
matches any kind) plus a minimum attempt count and the action to take once an Alarm has been
through that many attempts. `decide` is the pure function every `Supervisor` implementation calls
once it has an Alarm in hand: it never mutates the Alarm or the policy, and the same inputs always
produce the same action.

TOML shape this module's `load_policy` validates (see `supervision/defaults/default-policy.toml`)::

    default = "ESCALATE"  # The PolicyAction used when no rule below matches.

    [[rules]]
    kind = "WORKER_FAILED"  # An AlarmKind member name; omit the key for a wildcard rule.
    min_attempts = 1  # >= 1: decide() never matches a rule at attempts < 1.
    action = "RETRY"  # A PolicyAction member name.

`decide`'s matching rule (codingrules section 8.8): among the rules whose `kind` equals the
Alarm's own kind and whose `min_attempts` is at most `alarm.attempts`, the one with the highest
`min_attempts` wins; if none match, the same search runs again over the wildcard (`kind = None`)
rows; if that also finds nothing, `default` applies. An Alarm with `attempts == 0` -- freshly
raised, no resolution attempt made yet at this level -- matches no rule at all (every `min_attempts`
is at least 1) and always gets `default`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Loaded once per process (the Queen's
    and each Warden's own policy file, from `[supervision] policy_file` in the manifest) and read
    by whichever `Supervisor` implementation decides what to do about a HANDLING Alarm. Calls into
    `hivemind.supervision.alarm` and `hivemind.supervision.errors` only.

Key invariants:
    - EscalationPolicy and PolicyRule are frozen and forbid extras, like every boundary value here.
    - PolicyRule.min_attempts is always >= 1 (Field(ge=1)): a rule can never fire on an Alarm that
      has not yet had a single resolution attempt made against it.
    - decide is pure: it reads `policy` and `alarm` only, and always returns the same PolicyAction
      for the same two inputs.
    - load_policy raises PolicyError for a missing file, a TOML syntax error, or a document that
      fails EscalationPolicy's own validation; it never returns a partially-built policy.

See Also:
    - .claude/codingrules.md section 8.8 for "each level's EscalationPolicy is data (TOML)".
    - .claude/codingrules.md section 13 for the policy-as-data idea this module follows.
    - supervision/defaults/default-policy.toml for the Hive's shipped default policy.
    - hivemind.supervision.alarm for Alarm and AlarmKind, the model and enum decide reads.
    - hivemind.supervision.errors for PolicyError, the error load_policy raises.
"""

from __future__ import annotations

import tomllib
from enum import Enum
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hivemind.supervision.alarm import Alarm, AlarmKind
from hivemind.supervision.errors import PolicyError

DEFAULT_POLICY_FILENAME = "default-policy.toml"  # The shipped table, inside _DEFAULTS_PACKAGE.
# The data-only package the two shipped tables live in, addressed by dotted name so they resolve
# the same from a checkout and from an installed wheel (see that package's own docstring).
_DEFAULTS_PACKAGE = "hivemind.supervision.defaults"

__all__ = [
    "DEFAULT_POLICY_FILENAME",
    "EscalationPolicy",
    "PolicyAction",
    "PolicyRule",
    "decide",
    "load_policy",
]


class PolicyAction(Enum):
    """What a supervisor does about an Alarm, as an EscalationPolicy row names it."""

    RETRY = "RETRY"  # Try the same action again, no change of bee or model.
    RESPAWN = "RESPAWN"  # Start a fresh bee for the task, from its last Handoff if one exists.
    REBIND = "REBIND"  # Move the bee to another model slot (Supervisor.intervene's Rebind).
    TAKEOVER = (
        "TAKEOVER"  # The supervisor resumes the task itself (Supervisor.intervene's Takeover).
    )
    ESCALATE = "ESCALATE"  # Forward the same alarm id to the next supervisor up the chain.
    CANCEL = "CANCEL"  # Stop the task for good.


class PolicyRule(BaseModel):
    """One row of an EscalationPolicy: at this many attempts (or more), take this action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AlarmKind | None = Field(
        default=None,
        description="The AlarmKind this row governs; None matches any kind (a wildcard row, "
        "tried only after every kind-specific row has been checked).",
    )
    min_attempts: int = Field(
        ge=1,
        description="The Alarm's attempts must be at least this for the row to match. Always "
        ">= 1: a fresh Alarm (attempts == 0) matches no row.",
    )
    action: PolicyAction = Field(description="What to do once this row matches.")


class EscalationPolicy(BaseModel):
    """A supervisor's whole escalation playbook: its rows, plus the action when none match."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: tuple[PolicyRule, ...] = Field(description="Every row, in no particular order.")
    default: PolicyAction = Field(
        description="The action decide() returns when no row in `rules` matches."
    )


def load_policy(path: Path | None = None) -> EscalationPolicy:
    """Load and validate an EscalationPolicy from a TOML file, or from the shipped default.

    Args:
        path: The policy file, from the manifest's `[supervision] policy_file`. None, the
            default, reads the table shipped inside `hivemind.supervision.defaults`, which is
            what leaving that manifest field unset means: policy is data an operator may
            override, not data every operator must supply.

    Returns:
        The validated EscalationPolicy.

    Raises:
        PolicyError: `path` was given and does not exist or cannot be read, or the document read
            is not valid TOML or fails EscalationPolicy's own pydantic validation.
    """
    source = str(path) if path is not None else f"the shipped {DEFAULT_POLICY_FILENAME}"
    try:
        raw = tomllib.loads(_read_policy_text(path))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"Could not read escalation policy from {source}: {exc}") from exc

    try:
        return EscalationPolicy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyError(f"Escalation policy at {source} is invalid: {exc}") from exc


def _read_policy_text(path: Path | None) -> str:
    """Return the policy document's text, from `path` or from the shipped package resource."""
    if path is not None:
        return path.read_text(encoding="utf-8")
    return (files(_DEFAULTS_PACKAGE) / DEFAULT_POLICY_FILENAME).read_text(encoding="utf-8")


def decide(policy: EscalationPolicy, alarm: Alarm) -> PolicyAction:
    """Return the action `policy` prescribes for `alarm`, given its kind and attempt count.

    Args:
        policy: The escalation playbook to consult.
        alarm: The Alarm being decided about; only its `kind` and `attempts` are read.

    Returns:
        The highest-`min_attempts` kind-specific row that `alarm.attempts` clears; failing that,
        the highest-`min_attempts` wildcard row it clears; failing that, `policy.default`.
    """
    specific = _best_matching_action(policy.rules, alarm, kind=alarm.kind)
    if specific is not None:
        return specific
    wildcard = _best_matching_action(policy.rules, alarm, kind=None)
    if wildcard is not None:
        return wildcard
    return policy.default


def _best_matching_action(
    rules: tuple[PolicyRule, ...], alarm: Alarm, *, kind: AlarmKind | None
) -> PolicyAction | None:
    """Return the highest-min_attempts action among `rules` of `kind` that `alarm` clears.

    Args:
        rules: The candidate rows to search.
        alarm: Supplies the attempt count a row's min_attempts is compared against.
        kind: The exact `PolicyRule.kind` to match (`None` searches the wildcard rows).

    Returns:
        The matching row with the largest `min_attempts`, or None if no row of `kind` matches.
    """
    candidates = [
        rule for rule in rules if rule.kind == kind and alarm.attempts >= rule.min_attempts
    ]
    if not candidates:
        return None
    # "Most specific first" within one kind means the row that required the most attempts to
    # unlock: it is the closest match to how many attempts have actually been made.
    return max(candidates, key=lambda rule: rule.min_attempts).action
