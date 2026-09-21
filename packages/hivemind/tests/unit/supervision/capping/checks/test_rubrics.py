"""Unit tests for hivemind.supervision.capping.checks.rubrics: JudgeRubric, load_judge_rubrics."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.supervision.capping.checks.rubrics import JudgeRubric, load_judge_rubrics
from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.tiers import RiskTier


def test_load_judge_rubrics_reads_the_shipped_v0_table() -> None:
    rubrics = load_judge_rubrics()

    assert set(rubrics) == set(RiskTier)


def test_outside_scratch_write_rubric_checks_the_tasks_stated_objective() -> None:
    """Roadmap step 5.0c: the judge rubric gains a criterion an over-declaring plan cannot dodge."""
    rubric = load_judge_rubrics()[RiskTier.OUTSIDE_SCRATCH_WRITE]

    assert "stated objective" in rubric.text
    assert rubric.rubric_id == "outside_scratch_write-v2"


@pytest.mark.parametrize("tier", list(RiskTier))
def test_every_shipped_rubric_has_a_rubric_id_and_nonempty_text(tier: RiskTier) -> None:
    rubrics = load_judge_rubrics()

    rubric = rubrics[tier]
    assert rubric.risk_tier is tier
    assert rubric.rubric_id
    assert rubric.text.strip()


def test_judge_rubric_is_frozen_and_forbids_extras() -> None:
    rubric = JudgeRubric(rubric_id="test-v1", risk_tier=RiskTier.SCRATCH_WRITE, text="Approve.")

    with pytest.raises(ValidationError, match="frozen"):
        rubric.text = "nope"  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError):
        rubric.model_validate({**rubric.model_dump(), "extra": "nope"})


def test_load_judge_rubrics_raises_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CappingError, match="Could not read"):
        load_judge_rubrics(tmp_path / "does-not-exist.toml")


def test_load_judge_rubrics_raises_for_invalid_toml(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.toml"
    bad_file.write_text("this is not [ valid toml", encoding="utf-8")

    with pytest.raises(CappingError, match="Could not read"):
        load_judge_rubrics(bad_file)


def test_load_judge_rubrics_raises_for_an_unknown_tier_name(tmp_path: Path) -> None:
    bad_file = tmp_path / "unknown-tier.toml"
    bad_file.write_text(
        '[rubrics.not_a_real_tier]\nrubric_id = "x"\ntext = "y"\n', encoding="utf-8"
    )

    with pytest.raises(CappingError, match="invalid"):
        load_judge_rubrics(bad_file)


def test_load_judge_rubrics_lowercases_round_trip_through_the_toml_key_convention(
    tmp_path: Path,
) -> None:
    toml_file = tmp_path / "one-rubric.toml"
    toml_file.write_text(
        '[rubrics.scratch_write]\nrubric_id = "custom-v1"\ntext = "Approve only in scope."\n',
        encoding="utf-8",
    )

    rubrics = load_judge_rubrics(toml_file)

    rubric = rubrics[RiskTier.SCRATCH_WRITE]
    assert rubric.rubric_id == "custom-v1"
    assert rubric.text == "Approve only in scope."
