"""Unit tests for hivemind.supervision.capping.tiers: RiskTier, TierTable, checks_for, loaders."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.tiers import (
    SHORTEN_LATENCY_BUDGET_S,
    RiskTier,
    TierSpec,
    TierTable,
    checks_for,
    load_tiers,
)
from waggle.messages.capping import CheckKind
from waggle.messages.capping import RiskTier as WireRiskTier

_NORMAL_TEMPO = Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL)

# The repository root, six parents up from this test file
# (packages/hivemind/tests/unit/supervision/capping/test_tiers.py).
_REPO_ROOT = Path(__file__).resolve().parents[6]


def test_risk_tier_members_and_values_match_the_wire_form() -> None:
    assert {m.name for m in RiskTier} == {m.name for m in WireRiskTier}
    assert all(member.value == member.name for member in RiskTier)


@pytest.mark.parametrize("member", list(RiskTier))
def test_risk_tier_round_trips_through_wire(member: RiskTier) -> None:
    assert RiskTier.from_wire(member.to_wire()) is member


def test_tier_spec_is_frozen_and_forbids_extras() -> None:
    spec = TierSpec(
        checks=(CheckKind.SCHEMA,), floor=(), snapshot_before=False, max_diff_bytes=None
    )

    with pytest.raises(ValidationError, match="frozen"):
        spec.snapshot_before = True  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError):
        TierSpec.model_validate({**spec.model_dump(), "extra": "nope"})


def test_tier_spec_judge_and_audit_rate_default_off() -> None:
    # A TierSpec built with no roadmap-4.10 fields (every pre-existing test and builder call in
    # this codebase) gets the conservative, backward-compatible defaults: no real-time judge, no
    # sampling.
    spec = TierSpec(checks=(), floor=(), snapshot_before=False)

    assert spec.judge is False
    assert spec.audit_rate == 0.0


@pytest.mark.parametrize("audit_rate", [-0.1, 1.1])
def test_tier_spec_rejects_an_audit_rate_outside_zero_to_one(audit_rate: float) -> None:
    with pytest.raises(ValidationError):
        TierSpec(checks=(), floor=(), snapshot_before=False, audit_rate=audit_rate)


def test_load_tiers_reads_the_shipped_v0_table() -> None:
    table = load_tiers()

    assert set(table.tiers) == set(RiskTier)


_EXPECTED_CHECKS = {
    RiskTier.READ_ONLY: frozenset(),
    RiskTier.SCRATCH_WRITE: frozenset({CheckKind.SCHEMA, CheckKind.SIZE_CAP}),
    RiskTier.OUTSIDE_SCRATCH_WRITE: frozenset(
        {CheckKind.SCHEMA, CheckKind.ALLOWLIST, CheckKind.SIZE_CAP}
    ),
    RiskTier.NETWORK_EGRESS: frozenset({CheckKind.SCHEMA, CheckKind.ALLOWLIST}),
    RiskTier.SPEND: frozenset({CheckKind.SCHEMA, CheckKind.ALLOWLIST}),
    RiskTier.DEVICE_COMMAND: frozenset({CheckKind.SCHEMA, CheckKind.ALLOWLIST}),
    RiskTier.IRREVERSIBLE: frozenset({CheckKind.SCHEMA, CheckKind.ALLOWLIST}),
}
_EXPECTED_SNAPSHOT_BEFORE = {
    RiskTier.READ_ONLY: False,
    RiskTier.SCRATCH_WRITE: False,
    RiskTier.OUTSIDE_SCRATCH_WRITE: False,
    RiskTier.NETWORK_EGRESS: False,
    RiskTier.SPEND: True,
    RiskTier.DEVICE_COMMAND: True,
    RiskTier.IRREVERSIBLE: True,
}
# Roadmap step 4.10: v0's shipped table turns no tier's real-time judge on yet (no Warden has a
# JudgeReviewer wired into its Mapping[CheckKind, Check]), but every tier above READ_ONLY samples
# completed work at some rate (codingrules section 8.12: "What cannot be gated is sampled").
_EXPECTED_AUDIT_RATES = {
    RiskTier.READ_ONLY: 0.0,
    RiskTier.SCRATCH_WRITE: 0.02,
    RiskTier.OUTSIDE_SCRATCH_WRITE: 0.1,
    RiskTier.NETWORK_EGRESS: 0.1,
    RiskTier.SPEND: 0.2,
    RiskTier.DEVICE_COMMAND: 0.2,
    RiskTier.IRREVERSIBLE: 0.5,
}


@pytest.mark.parametrize("tier", list(RiskTier))
def test_each_tier_has_the_expected_checks(tier: RiskTier) -> None:
    table = load_tiers()

    assert set(table.tiers[tier].checks) == _EXPECTED_CHECKS[tier]
    assert table.tiers[tier].snapshot_before is _EXPECTED_SNAPSHOT_BEFORE[tier]


@pytest.mark.parametrize("tier", list(RiskTier))
def test_no_shipped_v0_tier_turns_judge_on_yet(tier: RiskTier) -> None:
    # v0 ships JudgeCheck ready to wire in (hivemind.supervision.capping.checks.judge) but no
    # composition root's Mapping[CheckKind, Check] carries one yet; judge=true here would fail
    # every proposal at that tier closed with "check unavailable" (docs/adr/0018).
    table = load_tiers()

    assert table.tiers[tier].judge is False


@pytest.mark.parametrize("tier", list(RiskTier))
def test_each_tier_has_the_expected_audit_rate(tier: RiskTier) -> None:
    table = load_tiers()

    assert table.tiers[tier].audit_rate == _EXPECTED_AUDIT_RATES[tier]


@pytest.mark.parametrize("tier", list(RiskTier))
def test_every_tier_floors_schema(tier: RiskTier) -> None:
    table = load_tiers()

    assert CheckKind.SCHEMA in table.tiers[tier].floor


def test_read_only_has_no_checks_of_its_own_but_floors_schema() -> None:
    table = load_tiers()

    assert table.tiers[RiskTier.READ_ONLY].checks == ()
    assert table.tiers[RiskTier.READ_ONLY].floor == (CheckKind.SCHEMA,)


def test_load_tiers_raises_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CappingError, match="Could not read"):
        load_tiers(tmp_path / "does-not-exist.toml")


def test_load_tiers_raises_for_invalid_toml(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.toml"
    bad_file.write_text("this is not [ valid toml", encoding="utf-8")

    with pytest.raises(CappingError, match="Could not read"):
        load_tiers(bad_file)


def test_load_tiers_raises_for_an_unknown_tier_name(tmp_path: Path) -> None:
    bad_file = tmp_path / "unknown-tier.toml"
    bad_file.write_text('[tiers.not_a_real_tier]\nchecks = ["SCHEMA"]\n', encoding="utf-8")

    with pytest.raises(CappingError, match="invalid"):
        load_tiers(bad_file)


def test_tier_table_lowercases_round_trip_through_the_toml_key_convention(tmp_path: Path) -> None:
    toml_file = tmp_path / "one-tier.toml"
    toml_file.write_text(
        '[tiers.outside_scratch_write]\nchecks = ["SCHEMA", "ALLOWLIST"]\nfloor = ["SCHEMA"]\n'
        "snapshot_before = false\nmax_diff_bytes = 2048\n",
        encoding="utf-8",
    )

    table = load_tiers(toml_file)

    spec = table.tiers[RiskTier.OUTSIDE_SCRATCH_WRITE]
    assert spec.checks == (CheckKind.SCHEMA, CheckKind.ALLOWLIST)
    assert spec.max_diff_bytes == 2048


def test_tier_table_is_frozen_and_forbids_extras() -> None:
    table = TierTable(tiers={})

    with pytest.raises(ValidationError, match="frozen"):
        table.tiers = {}  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError):
        TierTable.model_validate({"tiers": {}, "extra": "nope"})


# ──────────────────────────────────────────────────────────────────────────────
# checks_for: tempo-driven ladder shortening/lengthening (roadmap step 4.10)
# ──────────────────────────────────────────────────────────────────────────────


def _judge_tier(*, floor_has_judge: bool = False) -> TierSpec:
    """Build a TierSpec whose real-time ladder includes JUDGE, floored or not."""
    floor = (CheckKind.SCHEMA, CheckKind.JUDGE) if floor_has_judge else (CheckKind.SCHEMA,)
    return TierSpec(
        checks=(CheckKind.SCHEMA, CheckKind.ALLOWLIST),
        floor=floor,
        snapshot_before=False,
        judge=True,
    )


def test_checks_for_matches_the_plain_union_when_tempo_is_normal_and_judge_is_off() -> None:
    tier = TierSpec(checks=(CheckKind.SCHEMA, CheckKind.SIZE_CAP), floor=(CheckKind.SCHEMA,))

    ladder = checks_for(tier, _NORMAL_TEMPO)

    assert ladder == (CheckKind.SCHEMA, CheckKind.SIZE_CAP)


def test_checks_for_adds_judge_when_the_tier_flag_is_set() -> None:
    tier = _judge_tier()

    ladder = checks_for(tier, _NORMAL_TEMPO)

    assert CheckKind.JUDGE in ladder
    assert ladder.count(CheckKind.JUDGE) == 1


def test_checks_for_drops_judge_for_a_low_accuracy_bar_when_not_floored() -> None:
    tier = _judge_tier(floor_has_judge=False)
    urgent = Tempo(latency_budget_s=None, accuracy=AccuracyBar.LOW)

    ladder = checks_for(tier, urgent)

    assert CheckKind.JUDGE not in ladder


def test_checks_for_drops_judge_under_a_tight_latency_budget_when_not_floored() -> None:
    tier = _judge_tier(floor_has_judge=False)
    tight = Tempo(latency_budget_s=SHORTEN_LATENCY_BUDGET_S - 1, accuracy=AccuracyBar.NORMAL)

    ladder = checks_for(tier, tight)

    assert CheckKind.JUDGE not in ladder


def test_checks_for_never_drops_judge_when_it_is_a_floor_check() -> None:
    # This is the "irreversible never skips anything" case (codingrules 8.14): a floored JUDGE
    # survives even an urgent, low-bar, tight-budget tempo -- the harshest shortening request.
    tier = _judge_tier(floor_has_judge=True)
    harshest = Tempo(latency_budget_s=1.0, accuracy=AccuracyBar.LOW)

    ladder = checks_for(tier, harshest)

    assert CheckKind.JUDGE in ladder


@pytest.mark.parametrize(
    "tempo",
    [
        _NORMAL_TEMPO,
        Tempo(latency_budget_s=None, accuracy=AccuracyBar.LOW),
        Tempo(latency_budget_s=None, accuracy=AccuracyBar.HIGH),
        Tempo(latency_budget_s=None, accuracy=AccuracyBar.CRITICAL),
        Tempo(latency_budget_s=1.0, accuracy=AccuracyBar.LOW),
        Tempo(latency_budget_s=1.0, accuracy=AccuracyBar.CRITICAL),
    ],
)
def test_irreversible_style_floor_never_loses_a_check_under_any_tempo(tempo: Tempo) -> None:
    # A tier configured the way IRREVERSIBLE is meant to be (every check it ever uses also in its
    # floor) must keep its whole ladder whatever tempo says.
    tier = TierSpec(
        checks=(CheckKind.SCHEMA, CheckKind.ALLOWLIST),
        floor=(CheckKind.SCHEMA, CheckKind.ALLOWLIST, CheckKind.JUDGE),
        snapshot_before=True,
        judge=True,
    )

    ladder = checks_for(tier, tempo)

    assert set(tier.floor) <= set(ladder)


def test_checks_for_adds_a_second_judge_pass_for_a_critical_bar() -> None:
    tier = _judge_tier()
    critical = Tempo(latency_budget_s=None, accuracy=AccuracyBar.CRITICAL)

    ladder = checks_for(tier, critical)

    assert ladder.count(CheckKind.JUDGE) == 2


def test_checks_for_never_introduces_judge_for_a_tier_that_never_uses_it() -> None:
    # A CRITICAL bar lengthens an existing JUDGE pass; it never invents one for a tier whose own
    # configuration (checks/floor/judge) never names JUDGE at all.
    tier = TierSpec(checks=(CheckKind.SCHEMA,), floor=(CheckKind.SCHEMA,))
    critical = Tempo(latency_budget_s=None, accuracy=AccuracyBar.CRITICAL)

    ladder = checks_for(tier, critical)

    assert CheckKind.JUDGE not in ladder


def test_checks_for_preserves_cheapest_first_order() -> None:
    tier = TierSpec(
        checks=(CheckKind.SIZE_CAP, CheckKind.SCHEMA, CheckKind.ALLOWLIST),
        floor=(CheckKind.SCHEMA,),
    )

    ladder = checks_for(tier, _NORMAL_TEMPO)

    assert ladder == (CheckKind.SCHEMA, CheckKind.ALLOWLIST, CheckKind.SIZE_CAP)
