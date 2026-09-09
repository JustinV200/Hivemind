"""Tests for hivemind.forage.models.sources: ModelCost, ModelSourceSpec, Distance and ModelSource.

Fits into the Hive:
    Mirrors src/hivemind/forage/models/sources.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.models.sources for the module under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from builders.forage import make_source
from pydantic import ValidationError

from hivemind.forage.models.sources import (
    Abundance,
    Distance,
    ModelCost,
    ModelSource,
    ModelSourceSpec,
)
from waggle.messages.forage import SourceRef
from waggle.messages.forage.values import MAX_MODEL_GRADE, MIN_MODEL_GRADE


def test_model_cost_defaults_every_rate_to_zero() -> None:
    cost = ModelCost()

    assert cost.cost_per_million_input_usd == 0.0
    assert cost.cost_per_million_output_usd == 0.0
    assert cost.cost_per_seat_hour_usd == 0.0


@pytest.mark.parametrize(
    "field",
    ["cost_per_million_input_usd", "cost_per_million_output_usd", "cost_per_seat_hour_usd"],
)
def test_model_cost_rejects_a_negative_rate(field: str) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ModelCost(**{field: -1.0})


def test_model_cost_is_frozen_and_forbids_extras() -> None:
    cost = ModelCost()

    with pytest.raises(ValidationError, match="frozen"):
        cost.cost_per_seat_hour_usd = 1.0  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        ModelCost.model_validate({"nope": 1})


def _spec(**overrides: object) -> ModelSourceSpec:
    fields: dict[str, object] = {
        "provider": "test-provider",
        "model": "test-model",
        "grade": 3,
        "context_window": 8_192,
    }
    fields.update(overrides)
    return ModelSourceSpec(**fields)


def test_model_source_spec_defaults_cost_seats_capabilities_and_host_cell_id() -> None:
    spec = _spec()

    assert spec.cost == ModelCost()
    assert spec.seats == 1
    assert spec.capabilities == ()
    assert spec.host_cell_id is None


@pytest.mark.parametrize("grade", [MIN_MODEL_GRADE - 1, MAX_MODEL_GRADE + 1])
def test_model_source_spec_rejects_a_grade_outside_the_closed_range(grade: int) -> None:
    with pytest.raises(ValidationError):
        _spec(grade=grade)


@pytest.mark.parametrize("window", [0, -1])
def test_model_source_spec_rejects_a_non_positive_context_window(window: int) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        _spec(context_window=window)


def test_model_source_spec_rejects_a_negative_seat_count() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _spec(seats=-1)


def test_model_source_spec_json_round_trips() -> None:
    original = _spec(seats=4, capabilities=("vision", "tool_calls"))

    restored = ModelSourceSpec.model_validate_json(original.model_dump_json())

    assert restored == original


def test_model_source_spec_is_frozen_and_forbids_extras() -> None:
    spec = _spec()

    with pytest.raises(ValidationError, match="frozen"):
        spec.grade = 5  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        ModelSourceSpec.model_validate({**_spec().model_dump(), "nope": 1})


def test_distance_json_round_trips() -> None:
    original = Distance(latency_s=0.5, tokens_per_s=42.0, measured_at=datetime.now(UTC))

    restored = Distance.model_validate_json(original.model_dump_json())

    assert restored == original


def test_distance_rejects_negative_latency_or_speed() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Distance(latency_s=-1.0, tokens_per_s=1.0, measured_at=datetime.now(UTC))
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Distance(latency_s=1.0, tokens_per_s=-1.0, measured_at=datetime.now(UTC))


def test_abundance_defaults_rate_limits_to_none() -> None:
    abundance = Abundance(seats_free=3)

    assert abundance.requests_per_minute_left is None
    assert abundance.tokens_per_minute_left is None


def test_abundance_rejects_a_negative_seats_free() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Abundance(seats_free=-1)


def test_model_source_defaults_distance_to_none() -> None:
    source = make_source()

    assert source.distance is None


def test_model_source_json_round_trips() -> None:
    original = make_source(
        distance=Distance(latency_s=0.1, tokens_per_s=10.0, measured_at=datetime.now(UTC))
    )

    restored = ModelSource.model_validate_json(original.model_dump_json())

    assert restored == original


def test_model_source_is_frozen_and_forbids_extras() -> None:
    source = make_source()

    with pytest.raises(ValidationError, match="frozen"):
        source.source_id = "other"  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        ModelSource.model_validate({**source.model_dump(), "nope": 1})


def test_model_source_source_ref_carries_the_static_identity_only() -> None:
    source = make_source(source_id="src_1", provider="acme", model="acme-model", host_cell_id=None)

    ref = source.source_ref()

    assert ref == SourceRef(
        source_id="src_1", provider="acme", model="acme-model", host_cell_id=None
    )
