"""Tests for hivemind.workers.roles.guard_bee.evaluate: the pure decision, shape by shape.

Over hand-built facts (no trail, no clock): a COUNT fires at its threshold by events, distinct
values or summed spend; a RATIO only over enough counted-against events; a SEQUENCE only for what
follows its leading event; nothing outside the window or at or before a consumption mark counts;
and what a finding touches decides which actions a report about it may recommend.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/evaluate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.workers.roles.guard_bee.test_evaluate_shipped for every shipped rule.
    - tests.unit.workers.roles.guard_bee.test_evaluate_correlation for the injection rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from hivemind.guard import GuardAction
from hivemind.workers.roles.guard_bee import (
    GuardRule,
    Mark,
    TrailFact,
    allowed_actions,
    evaluate,
    targets_of,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_task_id, new_worker_id

_T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)  # Every fact here is timed from this moment.
_CLOCK = FakeClock()  # Mints ids only; the facts carry their own times.


class _Facts:
    """A FactSource over a list: every fact's ids are already what it resolves to."""

    def __init__(self, facts: Sequence[TrailFact]) -> None:
        self._facts = sorted(facts, key=lambda fact: fact.at)

    def facts_of(self, kind: str) -> Sequence[TrailFact]:
        return [fact for fact in self._facts if fact.kind == kind]

    def resolve(self, fact: TrailFact) -> Mapping[str, str]:
        return fact.ids


def _fact(kind: str, second: float, amount: float = 0.0, **ids: str) -> TrailFact:
    fields = {key: value for key, value in ids.items() if key in {"outcome", "reason"}}
    return TrailFact(
        event_id=new_event_id(_CLOCK),
        at=_T0 + timedelta(seconds=second),
        kind=kind,
        node_id="node_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        ids={key: value for key, value in ids.items() if key not in fields},
        fields=fields,
        amount=amount,
    )


def _rule(**fields: object) -> GuardRule:
    base: dict[str, object] = {
        "key": "sample",
        "title": "a sample",
        "counts": [{"kind": "guard.denied"}],
        "group_by": "bee",
        "window_s": 60.0,
        "threshold": 3,
        "confidence": "medium",
        "action": "quarantine_bee",
    }
    return GuardRule.model_validate({**base, **fields})


def _at(second: float) -> datetime:
    return _T0 + timedelta(seconds=second)


def test_a_count_fires_at_its_threshold_and_not_one_under() -> None:
    bee = new_worker_id(_CLOCK)
    facts = [_fact("guard.denied", float(i), bee=bee) for i in range(3)]

    fired = evaluate([_rule()], _Facts(facts), _at(10), {})
    under = evaluate([_rule()], _Facts(facts[:2]), _at(10), {})

    [finding] = fired
    assert (finding.key, finding.measure, finding.through) == (bee, 3.0, _at(2))
    assert under == ()


def test_nothing_outside_the_window_or_consumed_is_counted() -> None:
    bee = new_worker_id(_CLOCK)
    facts = [_fact("guard.denied", float(i), bee=bee) for i in (0, 30, 40, 50)]

    reported = Mark(_at(30), frozenset({facts[1].event_id}))
    unmarked = Mark(_at(30))  # The same moment, but not the event recorded at it.

    windowed = evaluate([_rule()], _Facts(facts), _at(95), {})
    consumed = evaluate([_rule()], _Facts(facts), _at(55), {("sample", bee): reported})
    tied = evaluate([_rule()], _Facts(facts), _at(55), {("sample", bee): unmarked})

    assert windowed == ()  # Only 40 and 50 are inside the minute that ends at 95.
    assert consumed == ()  # 0 and 30 were reported already; 40 and 50 are two, not three.
    assert [finding.measure for finding in tied] == [3.0]  # 30 itself was never reported.


def test_a_distinct_count_counts_values_not_events() -> None:
    rule = _rule(group_by="hive", distinct="device", threshold=2)
    device = "device_01HZZZZZZZZZZZZZZZZZZZZZZ1"
    twice = [_fact("guard.denied", float(i), device=device) for i in range(3)]

    once = evaluate([rule], _Facts(twice), _at(10), {})
    fired = evaluate(
        [rule], _Facts([*twice, _fact("guard.denied", 4.0, device=device[:-1] + "2")]), _at(10), {}
    )

    assert once == ()
    assert [finding.measure for finding in fired] == [2.0]


def test_a_spend_count_sums_the_cost_its_events_carry() -> None:
    rule = _rule(counts=[{"kind": "llm.call"}], group_by="grant", measure="cost_usd", threshold=1.0)
    grant = "grant_01HZZZZZZZZZZZZZZZZZZZZZZZ"

    under = evaluate([rule], _Facts([_fact("llm.call", 1, 0.6, grant=grant)]), _at(5), {})
    facts = [_fact("llm.call", 1, 0.6, grant=grant), _fact("llm.call", 2, 0.5, grant=grant)]
    [finding] = evaluate([rule], _Facts(facts), _at(5), {})

    assert under == ()
    assert round(finding.measure, 2) == 1.1


def test_a_ratio_needs_enough_counted_against_events_and_its_fraction() -> None:
    rule = _rule(
        shape="ratio",
        counts=[{"kind": "capping.audited", "where": {"outcome": ["REJECT"]}}],
        of=[{"kind": "capping.audited"}],
        group_by="tier",
        threshold=4,
        ratio=0.5,
        action="raise_audit_rate",
    )
    tier = "SCRATCH_WRITE"
    outcomes = ["REJECT", "APPROVE", "REJECT", "APPROVE"]
    facts = [_fact("capping.audited", i, tier=tier, outcome=o) for i, o in enumerate(outcomes)]

    too_few = evaluate([rule], _Facts(facts[:3]), _at(10), {})
    [finding] = evaluate([rule], _Facts(facts), _at(10), {})
    diluted = [*facts, _fact("capping.audited", 5, tier=tier, outcome="APPROVE")]

    assert too_few == ()  # Two of three is two thirds, but three samples mean nothing yet.
    assert (finding.measure, finding.base, len(finding.sightings)) == (0.5, 4, 2)
    assert evaluate([rule], _Facts(diluted), _at(10), {}) == ()


def test_a_sequence_counts_only_what_follows_its_leading_event() -> None:
    rule = _rule(shape="sequence", first=[{"kind": "guard.injection_suspected"}], threshold=1)
    bee = new_worker_id(_CLOCK)
    before = _fact("guard.denied", 1, bee=bee)
    lead = _fact("guard.injection_suspected", 2, bee=bee)

    none = evaluate([rule], _Facts([before, lead]), _at(10), {})
    [finding] = evaluate(
        [rule], _Facts([before, lead, _fact("guard.denied", 3, bee=bee)]), _at(10), {}
    )

    assert none == ()
    assert finding.sightings[0].fact is lead and len(finding.sightings) == 2


def test_a_disabled_rule_is_never_evaluated() -> None:
    bee = new_worker_id(_CLOCK)
    facts = [_fact("guard.denied", float(i), bee=bee) for i in range(5)]

    assert evaluate([_rule(enabled=False)], _Facts(facts), _at(10), {}) == ()


def test_what_a_finding_touches_decides_what_its_report_may_ask_for() -> None:
    bee, task, cell = new_worker_id(_CLOCK), new_task_id(_CLOCK), new_cell_id(_CLOCK)
    facts = [_fact("guard.denied", float(i), bee=bee, task=task, cell=cell) for i in range(3)]
    [finding] = evaluate([_rule()], _Facts(facts), _at(10), {})
    lonely = [
        _fact("guard.denied", float(i), device="device_01HZZZZZZZZZZZZZZZZZZZZZZZ")
        for i in range(3)
    ]
    [hive_wide] = evaluate([_rule(group_by="hive")], _Facts(lonely), _at(10), {})

    targets = targets_of(finding)

    assert (targets.cell, targets.bees, targets.tasks) == (cell, (bee,), (task,))
    assert allowed_actions(targets) == {
        GuardAction.OBSERVE,
        GuardAction.QUARANTINE_BEE,
        GuardAction.ISOLATE_CELL,
        GuardAction.STING_CUT,
    }
    assert allowed_actions(targets_of(hive_wide)) == {GuardAction.OBSERVE}
