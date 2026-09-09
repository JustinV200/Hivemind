"""Unit tests for hivemind.supervision.capping.tiers: RiskTier sync, TierTable, load_tiers."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.tiers import RiskTier, TierSpec, TierTable, load_tiers
from waggle.messages.capping import CheckKind
from waggle.messages.capping import RiskTier as WireRiskTier

# The repository root, six parents up from this test file
# (packages/hivemind/tests/unit/supervision/capping/test_tiers.py).
_REPO_ROOT = Path(__file__).resolve().parents[6]
_TIERS_PATH = _REPO_ROOT / "docs" / "supervision" / "capping-tiers.toml"


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


def test_load_tiers_reads_the_shipped_v0_table() -> None:
    table = load_tiers(_TIERS_PATH)

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


@pytest.mark.parametrize("tier", list(RiskTier))
def test_each_tier_has_the_expected_checks(tier: RiskTier) -> None:
    table = load_tiers(_TIERS_PATH)

    assert set(table.tiers[tier].checks) == _EXPECTED_CHECKS[tier]
    assert table.tiers[tier].snapshot_before is _EXPECTED_SNAPSHOT_BEFORE[tier]


@pytest.mark.parametrize("tier", list(RiskTier))
def test_every_tier_floors_schema(tier: RiskTier) -> None:
    table = load_tiers(_TIERS_PATH)

    assert CheckKind.SCHEMA in table.tiers[tier].floor


def test_read_only_has_no_checks_of_its_own_but_floors_schema() -> None:
    table = load_tiers(_TIERS_PATH)

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
