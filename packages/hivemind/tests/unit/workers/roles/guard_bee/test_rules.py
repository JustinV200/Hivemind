"""Tests for hivemind.workers.roles.guard_bee.rules: the Guard Bee's rules as data.

The shipped rules load and cover every family roadmap 10.6 names; a manifest override changes
only what it names; and loading refuses everything a typo could break: an unknown rule, an
undeclared kind, a shape missing its own fields, a narrowing rule with judgement, a raise that
does not name one tier, a document that is not TOML.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/rules.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.rules for load_guard_rules and GuardRule.
    - docs/guard/guard-bee.md for the table the shipped rules are documented in.
"""

from __future__ import annotations

import pytest

from hivemind.guard import GuardAction, GuardConfidence
from hivemind.manifest.schema.guard import GuardBeeRuleOverride
from hivemind.workers.roles.guard_bee import (
    NARROWING_ACTIONS,
    GroupKey,
    GuardRulesError,
    Matcher,
    RuleShape,
    load_guard_rules,
)

# Every family of evidence roadmap step 10.6 names, by the trail kinds that carry it.
_FAMILIES = {
    "denial rates": "guard.denied",
    "out-of-scratch touches": "cell.touched_outside_scratch",
    "over-grant spend": "alarm.raised",
    "spend per grant": "llm.call",
    "Capping rejections": "capping.rejected",
    "Capping rollbacks": "capping.rolled_back",
    "failed sampled audits": "capping.audited",
    "injection flags": "guard.injection_suspected",
    "signature or replay failures": "guard.entrance_login_failed",
    "lockouts": "guard.entrance_locked",
    "invite-route abuse": "guard.entrance_redeem_failed",
    "travel-lock triggers": "guard.entrance_travel_lock",
}

_ONE_RULE = """
[rules.sample]
title = "a sample"
counts = [{ kind = "guard.denied" }]
group_by = "bee"
window_s = 60.0
threshold = 2
confidence = "medium"
action = "quarantine_bee"
"""


def test_the_shipped_rules_load_and_count_every_family_the_roadmap_names() -> None:
    rules = load_guard_rules()

    counted = {matcher.kind for rule in rules.enabled for matcher in rule.matchers}

    assert len(rules.rules) == 16
    assert {family: kind in counted for family, kind in _FAMILIES.items()} == dict.fromkeys(
        _FAMILIES, True
    )


def test_every_narrowing_rule_decides_alone_and_every_raise_names_a_tier() -> None:
    rules = load_guard_rules().enabled

    narrowing = [rule for rule in rules if rule.action in NARROWING_ACTIONS]

    assert narrowing and not any(rule.judgement for rule in narrowing)
    assert all(
        rule.group_by is GroupKey.TIER
        for rule in narrowing
        if rule.action is GuardAction.RAISE_AUDIT_RATE
    )


def test_the_injection_correlation_counts_a_lease_boundary_refusal_as_a_denial() -> None:
    rule = next(r for r in load_guard_rules().rules if r.key == "injection_then_denial")

    assert rule.shape is RuleShape.SEQUENCE and rule.group_by is GroupKey.BEE
    assert Matcher(kind="guard.injection_suspected") in rule.first
    assert Matcher(kind="capping.rejected", where={"failing_check": ("ALLOWLIST",)}) in rule.counts
    assert rule.confidence is GuardConfidence.HIGH and not rule.judgement


def test_an_override_changes_only_what_it_names() -> None:
    override = GuardBeeRuleOverride(threshold=8, confidence="high", enabled=False)

    rules = load_guard_rules({"denial_burst": override})

    rule = next(r for r in rules.rules if r.key == "denial_burst")
    assert (rule.threshold, rule.confidence, rule.enabled) == (8.0, GuardConfidence.HIGH, False)
    assert rule.window_s == 300.0 and rule.judgement  # Everything unnamed keeps the shipped value.
    assert "denial_burst" not in {r.key for r in rules.enabled}


def test_an_override_for_a_rule_that_does_not_exist_is_refused() -> None:
    with pytest.raises(GuardRulesError, match="do not exist"):
        load_guard_rules({"no_such_rule": GuardBeeRuleOverride(threshold=1)})


def test_an_override_that_asks_a_narrowing_rule_for_judgement_is_refused() -> None:
    with pytest.raises(GuardRulesError, match="takes no judgement"):
        load_guard_rules({"request_forgery": GuardBeeRuleOverride(judgement=True)})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ('counts = [{ kind = "guard.nonsense" }]', "no trail kind"),
        ('counts = [{ kind = "nosuchfamily.denied" }]', "no trail kind"),
        ('shape = "ratio"', "belong to ratio rules"),
        ('first = [{ kind = "guard.injection_suspected" }]', "belongs to sequence rules"),
        ('action = "raise_audit_rate"', "must group by tier"),
        ('distinct = "device"\nshape = "sequence"', "sequence"),
    ],
)
def test_a_broken_rule_is_refused_naming_it(change: str, message: str) -> None:
    table = "\n".join(
        line for line in _ONE_RULE.splitlines() if not line.startswith(change.split(" ")[0])
    )

    with pytest.raises(GuardRulesError, match=message):
        load_guard_rules(text=f"{table}\n{change}\n")


def test_a_document_that_is_not_toml_is_refused() -> None:
    with pytest.raises(GuardRulesError, match="not valid TOML"):
        load_guard_rules(text="[rules.broken\n")


def test_the_horizons_are_the_longest_window_per_kind() -> None:
    horizons = load_guard_rules().horizons()

    assert horizons["capping.audited"] == 86_400.0
    assert horizons["guard.denied"] == 900.0  # The correlation's window outlasts denial_burst's.
    assert "worker.spawned" not in horizons  # Joins are the watch's to keep, not a rule's.
