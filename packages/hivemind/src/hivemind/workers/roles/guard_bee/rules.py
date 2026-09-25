"""Load the Guard Bee's rules: what each counts on the trail, over what window, and what it asks.

The Guard Bee (a Worker specialised in security and monitoring, roadmap step 10.6) watches the
Pheromone Trail (the Hive's append-only audit log) with deterministic rules, and the rules are
data (ADR-0043): `rules.toml`, shipped beside this module and read through `importlib.resources`,
holds one table per rule, and the manifest's `[guard.bee.rules.<key>]` tables override any field
but what a rule counts. A rule names the trail kinds it counts (each a `Matcher`: a kind and,
optionally, the values some of its payload fields must hold), the key it groups them by, a window,
a threshold, its own verdict (a `GuardConfidence` and a `GuardAction`), and whether a finding
waits on an awake episode's judgement first. Three shapes cover every rule: COUNT (events,
distinct values or a summed cost per key), RATIO (one set of events over another per key) and
SEQUENCE (a first event followed by others for the same key). Loading checks everything a typo
could break: every kind is one the trail declares, every shape carries exactly its own fields, and
a rule that narrows the whole Hive never waits on judgement.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Called by
    `hivemind.workers.roles.guard_bee.bee.build_guard_bee` in the composition root; the rules it
    returns are read by `.watch` (which kinds to keep), `.evaluate` (what fires) and `.respond`
    (what a finding asks for). Calls into `hivemind.guard` (GuardAction, GuardConfidence),
    `hivemind.manifest.schema.guard` (GuardBeeRuleOverride), `hivemind.pheromone` (the kind
    vocabulary) and `hivemind.workers.errors`.

Key invariants:
    - A rule that narrows the whole Hive (raise_audit_rate, reduce_entrance) never needs
      judgement: narrowing is always safe to do by rule alone (codingrules 8.15, ADR-0043).
    - A raise_audit_rate rule groups by tier, because a raise names exactly one Capping tier.
    - Every matcher's kind is a declared trail kind, or the one kind the Guard Bee derives
      (`SUBJECT_FORGED_KIND`); loading fails, naming the rule, otherwise.

See Also:
    - docs/guard/guard-bee.md for every shipped rule, what it counts and what it recommends.
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.workers.roles.guard_bee.evaluate for how a rule is evaluated against the trail.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from enum import Enum
from importlib.resources import files
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hivemind.guard import GuardAction, GuardConfidence
from hivemind.manifest.schema.guard import MAX_GUARD_WINDOW_S, GuardBeeRuleOverride
from hivemind.pheromone import KIND_PATTERN, UnknownEventFamilyError, event_class_for
from hivemind.workers.errors import GuardRulesError

RULES_FILENAME = "rules.toml"  # The shipped rule data, inside this package.
_RULES_PACKAGE = "hivemind.workers.roles.guard_bee"  # Addressed by name: same from a wheel.
MAX_TITLE_CHARS = 100  # A rule's title is a noun phrase for a human, never prose about content.
MAX_WHERE_VALUES = 16  # Accepted values per matched field: enum names, never a list of hosts.
# The actions that narrow the whole Hive: the Guard Bee takes them alone, by rule (ADR-0043).
NARROWING_ACTIONS = frozenset({GuardAction.RAISE_AUDIT_RATE, GuardAction.REDUCE_ENTRANCE})
# The one kind the Guard Bee derives rather than reads (roadmap step 10.6): an event a Cell's node
# recorded about what another Cell owns, as evidence against the recording node's Cell. Its family
# is no trail family, so it can never be mistaken for, or collide with, a recorded kind.
SUBJECT_FORGED_KIND = "guard_bee.subject_forged"
DERIVED_KINDS = frozenset({SUBJECT_FORGED_KIND})

__all__ = [
    "DERIVED_KINDS",
    "MAX_TITLE_CHARS",
    "NARROWING_ACTIONS",
    "RULES_FILENAME",
    "SUBJECT_FORGED_KIND",
    "GroupKey",
    "GuardRule",
    "GuardRules",
    "Matcher",
    "Measure",
    "RuleShape",
    "load_guard_rules",
]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every model here (codingrules 8.5).
_Values = tuple[str, ...]


class RuleShape(Enum):
    """How a rule turns the events it counts into a finding."""

    COUNT = "count"  # Events, distinct values or a summed cost for one key reach the threshold.
    RATIO = "ratio"  # `counts` over `of` for one key reaches `ratio` once `of` is big enough.
    SEQUENCE = "sequence"  # A `first` event is followed, for the same key, by enough `counts`.


class GroupKey(Enum):
    """What a rule groups the events it counts by; each is an id or a label the trail carries."""

    HIVE = "hive"  # Everything together: the Hive is the key.
    BEE = "bee"  # One worker or warden id; one worker id is one spawned attempt, one episode.
    TASK = "task"
    CELL = "cell"
    GRANT = "grant"
    DEVICE = "device"  # An enrolled client device, at the Hive Entrance.
    ADDRESS = "address"  # A peer address as the Entrance records it on the trail.
    TIER = "tier"  # A Capping risk tier.


class Measure(Enum):
    """What a COUNT rule adds up for one key."""

    EVENTS = "events"  # One per event.
    COST_USD = "cost_usd"  # The model spend an llm.call event carries.


class Matcher(BaseModel):
    """One kind of trail event a rule counts, optionally only with some payload values."""

    model_config = _MODEL_CONFIG

    kind: str = Field(pattern=KIND_PATTERN, description="The trail kind counted.")
    where: dict[str, _Values] = Field(
        default_factory=dict,
        description="Payload field to its accepted values; every named field must hold one.",
    )

    @model_validator(mode="after")
    def _bounded_values(self) -> Self:
        """Refuse an empty or oversized value list: `where` names enum values, not data."""
        for field_name, values in self.where.items():
            if not 0 < len(values) <= MAX_WHERE_VALUES:
                raise ValueError(f"where.{field_name} must list 1 to {MAX_WHERE_VALUES} values.")
        return self

    def matches(self, kind: str, fields: Mapping[str, str]) -> bool:
        """Say whether an event of `kind` carrying `fields` is one this matcher counts.

        Args:
            kind: The event's trail kind.
            fields: Its payload values as strings (`hivemind.workers.roles.guard_bee.facts`).

        Returns:
            True when the kind is this matcher's and every `where` field holds an accepted value.
        """
        if kind != self.kind:
            return False
        return all(fields.get(name) in accepted for name, accepted in self.where.items())


class GuardRule(BaseModel):
    """One deterministic Guard Bee rule, as `rules.toml` and the manifest's overrides make it."""

    model_config = _MODEL_CONFIG

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="The rule's name.")
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS, description="For the human.")
    shape: RuleShape = Field(default=RuleShape.COUNT, description="How the rule fires.")
    counts: tuple[Matcher, ...] = Field(min_length=1, description="The events it counts.")
    of: tuple[Matcher, ...] = Field(default=(), description="A ratio's counted-against events.")
    first: tuple[Matcher, ...] = Field(default=(), description="A sequence's leading events.")
    group_by: GroupKey = Field(description="The key events are grouped by.")
    distinct: GroupKey | None = Field(default=None, description="Count distinct values instead.")
    measure: Measure = Field(default=Measure.EVENTS, description="What a COUNT adds up.")
    window_s: float = Field(gt=0, le=MAX_GUARD_WINDOW_S, description="Seconds of trail counted.")
    threshold: float = Field(gt=0, description="Where it fires; a ratio's smallest `of` count.")
    ratio: float | None = Field(default=None, gt=0, le=1, description="A ratio's firing fraction.")
    confidence: GuardConfidence = Field(description="How sure the rule's own verdict is.")
    action: GuardAction = Field(description="What the rule's own verdict recommends.")
    judgement: bool = Field(default=False, description="Whether a finding is judged first.")
    enabled: bool = Field(default=True, description="False switches the rule off.")

    @model_validator(mode="after")
    def _shape_has_its_own_fields(self) -> Self:
        """Refuse a field that belongs to another shape, or a shape missing its own."""
        is_ratio, is_sequence = self.shape is RuleShape.RATIO, self.shape is RuleShape.SEQUENCE
        if is_ratio != (bool(self.of) and self.ratio is not None):
            raise ValueError(f"Rule {self.key}: `of` and `ratio` belong to ratio rules, together.")
        if is_sequence != bool(self.first):
            raise ValueError(
                f"Rule {self.key}: `first` belongs to sequence rules, and they need it."
            )
        counting = self.shape is RuleShape.COUNT
        if not counting and (self.distinct is not None or self.measure is not Measure.EVENTS):
            raise ValueError(f"Rule {self.key}: `distinct` and `measure` belong to count rules.")
        return self

    @model_validator(mode="after")
    def _narrowing_is_rule_alone(self) -> Self:
        """Refuse judgement on a narrowing rule, and a raise that does not name one tier."""
        if self.action in NARROWING_ACTIONS and self.judgement:
            raise ValueError(f"Rule {self.key} narrows the whole Hive: it takes no judgement.")
        if self.action is GuardAction.RAISE_AUDIT_RATE and self.group_by is not GroupKey.TIER:
            raise ValueError(f"Rule {self.key} raises an audit rate: it must group by tier.")
        return self

    @property
    def matchers(self) -> tuple[Matcher, ...]:
        """Every matcher the rule uses, whatever its role in the shape."""
        return (*self.first, *self.counts, *self.of)


class GuardRules(BaseModel):
    """Every rule the Guard Bee runs, in file order; a disabled rule is kept but never evaluated."""

    model_config = _MODEL_CONFIG

    rules: tuple[GuardRule, ...] = Field(description="The rules, keys unique.")

    @model_validator(mode="after")
    def _keys_are_unique(self) -> Self:
        """Refuse two rules under one key: a key names one rule in every report and alert."""
        keys = [rule.key for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("Two Guard Bee rules share a key.")
        return self

    @property
    def enabled(self) -> tuple[GuardRule, ...]:
        """The rules that run."""
        return tuple(rule for rule in self.rules if rule.enabled)

    def horizons(self) -> dict[str, float]:
        """Return, per trail kind an enabled rule counts, the longest window any of them uses."""
        horizons: dict[str, float] = {}
        for rule in self.enabled:
            for matcher in rule.matchers:
                horizons[matcher.kind] = max(horizons.get(matcher.kind, 0.0), rule.window_s)
        return horizons

    def watched_fields(self) -> frozenset[str]:
        """Return every payload field an enabled rule's matcher reads."""
        return frozenset(
            name for rule in self.enabled for matcher in rule.matchers for name in matcher.where
        )


def load_guard_rules(
    overrides: Mapping[str, GuardBeeRuleOverride] | None = None, text: str | None = None
) -> GuardRules:
    """Load the shipped rules (or `text`), apply the manifest's overrides, and validate them.

    Args:
        overrides: `[guard.bee.rules.<key>]` tables by rule key; a field left out keeps the
            shipped value.
        text: A rules document in the shipped file's shape, for a test; None reads `rules.toml`.

    Returns:
        The validated rules, in document order.

    Raises:
        GuardRulesError: The document is not valid TOML, an override names a rule that does not
            exist, or a rule (after its override) is invalid or counts an undeclared kind.
    """
    source = text if text is not None else _shipped_text()
    try:
        tables: object = tomllib.loads(source).get("rules", {})
    except tomllib.TOMLDecodeError as exc:
        raise GuardRulesError(f"The Guard Bee's rules are not valid TOML: {exc}") from exc
    if not isinstance(tables, dict):
        raise GuardRulesError("The Guard Bee's rules document has no [rules.<key>] tables.")
    named = overrides or {}
    unknown = sorted(set(named) - set(tables))
    if unknown:
        raise GuardRulesError(f"[guard.bee.rules] names rules that do not exist: {unknown}.")
    rules = tuple(_build_rule(key, table, named.get(key)) for key, table in tables.items())
    try:
        return GuardRules(rules=rules)
    except ValidationError as exc:
        raise GuardRulesError(f"The Guard Bee's rules are invalid: {exc}") from exc


def _shipped_text() -> str:
    """Return the shipped `rules.toml` as text, through importlib.resources."""
    return (files(_RULES_PACKAGE) / RULES_FILENAME).read_text(encoding="utf-8")


def _build_rule(key: str, table: object, override: GuardBeeRuleOverride | None) -> GuardRule:
    """Build one rule from its table, the override's named fields laid over it, and check kinds."""
    if not isinstance(table, dict):
        raise GuardRulesError(f"Guard Bee rule {key!r} is not a table.")
    fields: dict[str, object] = {**table, "key": key}
    if override is not None:
        # Only the fields the operator named replace the shipped ones (codingrules 13: defaults
        # live in the data, an override says what differs).
        fields.update(override.model_dump(exclude_none=True))
    try:
        rule = GuardRule.model_validate(fields)
    except ValidationError as exc:
        raise GuardRulesError(f"Guard Bee rule {key!r} is invalid: {exc}") from exc
    for matcher in rule.matchers:
        _require_declared_kind(key, matcher.kind)
    return rule


def _require_declared_kind(key: str, kind: str) -> None:
    """Refuse a matcher kind the trail does not declare, nor the Guard Bee derives."""
    try:
        declared = kind in DERIVED_KINDS or kind in event_class_for(kind).KINDS
    except UnknownEventFamilyError:
        declared = False
    if not declared:
        raise GuardRulesError(f"Guard Bee rule {key!r} counts {kind!r}, which is no trail kind.")
