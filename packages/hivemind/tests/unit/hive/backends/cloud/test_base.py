"""Unit tests for hivemind.hive.backends.cloud.base: PricingTag, CloudCredentials, and config.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/cloud/base.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.cloud.base for the value types under test.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from hivemind.forage import ModelCost
from hivemind.hive.backends.cloud.base import (
    CloudBackendConfig,
    CloudCredentials,
    CloudRegion,
    PricingTag,
)


def test_pricing_tag_as_model_cost_maps_onto_cost_per_seat_hour() -> None:
    tag = PricingTag(cost_per_hour_usd=0.42)

    cost = tag.as_model_cost()

    assert cost == ModelCost(cost_per_seat_hour_usd=0.42)


def test_pricing_tag_rejects_a_negative_rate() -> None:
    with pytest.raises(ValidationError):
        PricingTag(cost_per_hour_usd=-1.0)


def test_cloud_credentials_never_shows_its_secret_in_repr() -> None:
    credentials = CloudCredentials(key_id=SecretStr("AKIA_FAKE"), secret=SecretStr("super-secret"))

    rendered = repr(credentials)

    assert "super-secret" not in rendered
    assert "AKIA_FAKE" not in rendered


def test_cloud_backend_config_round_trips_every_field() -> None:
    config = CloudBackendConfig(
        region=CloudRegion("us-east-1"),
        credentials=CloudCredentials(key_id=SecretStr("k"), secret=SecretStr("s")),
        pricing=PricingTag(cost_per_hour_usd=0.1),
        instance_type="t3.medium",
    )

    assert config.region == "us-east-1"
    assert config.instance_type == "t3.medium"
    assert config.pricing.cost_per_hour_usd == 0.1
