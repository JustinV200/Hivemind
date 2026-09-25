"""Evaluate the Guard Bee's rules against what it has read of the trail: the pure decision.

Each enabled rule looks back over its own window, keeps the facts its matchers count, joins each
through the episode index to the key it groups by (a bee, a Cell, a tier, an address, the whole
Hive), and drops those a finding for that rule and key already counted (`consumed`, a `Mark`:
the moment the last finding counted up to, and which events at exactly that moment it counted, so
two events stamped in the same millisecond are never confused): a burst is reported once, and only
what arrives after it can make the rule fire again. A COUNT rule fires when a key's events (or
distinct values of another key, or summed spend) reach the threshold; a RATIO rule when one set of
events over another reaches its fraction, once the second set is big enough to mean something; a
SEQUENCE rule when a key's first leading event (an injection flag) is followed by enough of the
events it counts (denials). Every `Finding` names
the rule, the key, every fact it counted (oldest first; a sequence's leading event leads), what it
measured, and the mark reporting it leaves. `targets_of` reads what a finding touches off its
sightings (the one Cell, the bees, tasks and grants), and `allowed_actions` which of the Guard
Bee's levers those targets make possible: a Cell-level request needs a Cell, a quarantine a bee or
a task. Nothing here reads a clock or the trail, so every rule is testable over a hand-built set of
facts (codingrules 8.3).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Called once per
    round by `.bee.GuardBee`; its findings go to `.judge` (when the rule asks for judgement) and
    `.respond`. Calls into `.facts` and `.rules` only.

Key invariants:
    - Pure: the same facts, joins, time and consumption always give the same findings.
    - A fact a rule and key's mark covers is never counted again; one it does not, always is.
    - A sequence counts only events at or after its leading event, for the same key.
    - A report built from `targets_of` never names more than `MAX_TARGETS` of anything.

See Also:
    - hivemind.workers.roles.guard_bee.rules for the shapes and keys evaluated here.
    - docs/guard/guard-bee.md for what each shipped rule counts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from hivemind.guard import GuardAction
from hivemind.guard.report import MAX_TARGETS
from hivemind.workers.roles.guard_bee.facts import TrailFact
from hivemind.workers.roles.guard_bee.rules import (
    GroupKey,
    GuardRule,
    Matcher,
    Measure,
    RuleShape,
)

HIVE_KEY = "hive"  # The key every fact of a hive-wide rule shares.

__all__ = [
    "HIVE_KEY",
    "Consumed",
    "FactSource",
    "Finding",
    "Mark",
    "Sighting",
    "Targets",
    "allowed_actions",
    "evaluate",
    "targets_of",
]


@dataclass(frozen=True, slots=True)
class Mark:
    """How far one rule's findings for one key are reported: a moment and its events counted."""

    through: datetime  # The newest counted event's time.
    ids: frozenset[str] = frozenset()  # Every counted event recorded at exactly `through`.

    def covers(self, fact: TrailFact) -> bool:
        """Say whether `fact` was already counted: before the mark, or one of its own at it."""
        return fact.at < self.through or (fact.at == self.through and fact.event_id in self.ids)

    def merge(self, other: Mark) -> Mark:
        """Return the further of two marks; at the same moment, one covering both sets of ids."""
        if other.through != self.through:
            return other if other.through > self.through else self
        return Mark(through=self.through, ids=self.ids | other.ids)


# (rule key, group key) -> how far findings for them are already reported.
Consumed = Mapping[tuple[str, str], Mark]


class FactSource(Protocol):
    """Where evaluation reads facts from: `.watch.TrailWatch` in production, a list in tests."""

    def facts_of(self, kind: str) -> Sequence[TrailFact]:
        """Return every held fact of `kind`, oldest first."""
        ...

    def resolve(self, fact: TrailFact) -> Mapping[str, str]:
        """Return `fact`'s ids with every join the source knows filled in."""
        ...


@dataclass(frozen=True, slots=True)
class Sighting:
    """One counted fact, with the ids the episode index joined to it."""

    fact: TrailFact  # The fact as read from the trail.
    ids: Mapping[str, str]  # Its own ids and every joined one (bee, task, cell, grant, tier).


@dataclass(frozen=True, slots=True)
class Finding:
    """One rule firing for one key: what it counted, what it measured, and up to when.

    Attributes:
        rule: The rule that fired.
        key: The value of its group key ("hive" for a hive-wide rule).
        sightings: Every fact it counted, oldest first; a sequence's leading event first.
        measure: The count, distinct count, summed spend, or (for a ratio) the fraction.
        mark: How far reporting it consumes: its newest counted time and the events at it.
        base: A ratio's counted-against events; None for any other shape.
    """

    rule: GuardRule
    key: str
    sightings: tuple[Sighting, ...]
    measure: float
    mark: Mark
    base: int | None = None

    @property
    def through(self) -> datetime:
        """The newest counted fact's time."""
        return self.mark.through


@dataclass(frozen=True, slots=True)
class Targets:
    """What one finding touches: its Cell, and its bees, tasks and grants, each list bounded."""

    cell: str | None  # The one Cell a Cell-level request would name: the key, or the first seen.
    bees: tuple[str, ...]  # Worker and warden ids, first seen first.
    tasks: tuple[str, ...]
    grants: tuple[str, ...]


def targets_of(finding: Finding) -> Targets:
    """Read what `finding` touches off its sightings' joined ids.

    Args:
        finding: A rule's finding.

    Returns:
        The Cell (the key itself for a rule grouped by Cell), and every distinct bee, task and
        grant its sightings name, first seen first, at most `MAX_TARGETS` of each.
    """
    cells = _distinct(finding, "cell")
    cell = finding.key if finding.rule.group_by is GroupKey.CELL else next(iter(cells), None)
    return Targets(
        cell=cell,
        bees=_distinct(finding, "bee"),
        tasks=_distinct(finding, "task"),
        grants=_distinct(finding, "grant"),
    )


def allowed_actions(targets: Targets) -> frozenset[GuardAction]:
    """Return the actions a report about `targets` can recommend without guessing a target.

    Args:
        targets: What a finding touches.

    Returns:
        OBSERVE always; QUARANTINE_BEE with a bee or a task; ISOLATE_CELL and STING_CUT with a
        Cell. The narrowing actions are a rule's alone and never offered here.
    """
    allowed = {GuardAction.OBSERVE}
    if targets.bees or targets.tasks:
        allowed.add(GuardAction.QUARANTINE_BEE)
    if targets.cell is not None:
        allowed.update({GuardAction.ISOLATE_CELL, GuardAction.STING_CUT})
    return frozenset(allowed)


def evaluate(
    rules: Iterable[GuardRule], source: FactSource, now: datetime, consumed: Consumed
) -> tuple[Finding, ...]:
    """Return every finding the rules make of the facts in their windows at `now`.

    Args:
        rules: The rules to evaluate; a disabled one is skipped.
        source: The facts and their joins.
        now: The end of every window.
        consumed: Per rule and key, the moment a finding last counted up to.

    Returns:
        Every finding, rule by rule in the order given, keys in sorted order within a rule.
    """
    findings: list[Finding] = []
    for rule in rules:
        if rule.enabled:
            findings.extend(_evaluate_rule(rule, source, now, consumed))
    return tuple(findings)


def _evaluate_rule(
    rule: GuardRule, source: FactSource, now: datetime, consumed: Consumed
) -> list[Finding]:
    """Evaluate one rule by its shape."""
    since = now - timedelta(seconds=rule.window_s)
    counted = _gather(rule, rule.counts, source, since, consumed)
    if rule.shape is RuleShape.RATIO:
        base = _gather(rule, rule.of, source, since, consumed)
        return [f for key in sorted(base) if (f := _ratio(rule, key, counted, base)) is not None]
    if rule.shape is RuleShape.SEQUENCE:
        leads = _gather(rule, rule.first, source, since, consumed)
        return [
            f for key in sorted(leads) if (f := _sequence(rule, key, leads, counted)) is not None
        ]
    return [f for key in sorted(counted) if (f := _count(rule, key, counted[key])) is not None]


def _gather(
    rule: GuardRule,
    matchers: Sequence[Matcher],
    source: FactSource,
    since: datetime,
    consumed: Consumed,
) -> dict[str, list[Sighting]]:
    """Group every fact the matchers count inside the window, not yet consumed, by rule key."""
    groups: dict[str, list[Sighting]] = {}
    seen: set[str] = set()
    for matcher in matchers:
        for fact in source.facts_of(matcher.kind):
            # Outside the window, counted by an earlier matcher, or not this matcher's fields.
            if (
                fact.at < since
                or fact.event_id in seen
                or not matcher.matches(fact.kind, fact.fields)
            ):
                continue
            ids = source.resolve(fact)
            key = HIVE_KEY if rule.group_by is GroupKey.HIVE else ids.get(rule.group_by.value)
            mark = consumed.get((rule.key, key)) if key is not None else None
            if key is None or (mark is not None and mark.covers(fact)):
                continue  # The fact names no key for this rule, or its burst was reported.
            seen.add(fact.event_id)
            groups.setdefault(key, []).append(Sighting(fact=fact, ids=ids))
    for sightings in groups.values():
        sightings.sort(key=lambda sighting: (sighting.fact.at, sighting.fact.event_id))
    return groups


def _count(rule: GuardRule, key: str, sightings: list[Sighting]) -> Finding | None:
    """Fire a COUNT rule when its events, distinct values or spend reach the threshold."""
    if rule.distinct is not None:
        # Distinct values of another key: a device locked twice is still one device.
        values = {s.ids.get(rule.distinct.value) for s in sightings} - {None}
        measure = float(len(values))
    elif rule.measure is Measure.COST_USD:
        measure = sum(s.fact.amount for s in sightings)
    else:
        measure = float(len(sightings))
    if measure < rule.threshold:
        return None
    return Finding(rule, key, tuple(sightings), measure, _mark_of(sightings))


def _ratio(
    rule: GuardRule, key: str, counted: dict[str, list[Sighting]], base: dict[str, list[Sighting]]
) -> Finding | None:
    """Fire a RATIO rule when `counts` over `of` reaches its fraction over enough `of` events."""
    over, under = counted.get(key, []), base[key]
    # A fraction over too few events means nothing; `threshold` is the smallest meaningful base.
    if len(under) < rule.threshold or rule.ratio is None or not over:
        return None
    fraction = len(over) / len(under)
    if fraction < rule.ratio:
        return None
    # Both sets are reported together: a later ratio starts from events neither one counted.
    return Finding(rule, key, tuple(over), fraction, _mark_of([*over, *under]), base=len(under))


def _sequence(
    rule: GuardRule, key: str, leads: dict[str, list[Sighting]], counted: dict[str, list[Sighting]]
) -> Finding | None:
    """Fire a SEQUENCE rule when a key's first leading event is followed by enough others."""
    lead = leads[key][0]
    # Only what came at or after the leading event: a denial before the flag is not its effect.
    following = [s for s in counted.get(key, []) if s.fact.at >= lead.fact.at]
    if len(following) < rule.threshold:
        return None
    cited = (lead, *following)
    return Finding(rule, key, cited, float(len(following)), _mark_of(cited))


def _distinct(finding: Finding, id_kind: str) -> tuple[str, ...]:
    """Return every distinct `id_kind` id the finding's sightings name, first seen first."""
    found: dict[str, None] = {}
    for sighting in finding.sightings:
        value = sighting.ids.get(id_kind)
        if value is not None and len(found) < MAX_TARGETS:
            found.setdefault(value, None)
    return tuple(found)


def _mark_of(sightings: Sequence[Sighting]) -> Mark:
    """Return the mark reporting `sightings` leaves: their newest time and its events."""
    through = max(sighting.fact.at for sighting in sightings)
    at_through = frozenset(s.fact.event_id for s in sightings if s.fact.at == through)
    return Mark(through=through, ids=at_through)
