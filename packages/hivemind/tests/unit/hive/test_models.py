"""Unit tests for hivemind.hive.models: VirtualCellSpec's defaults, round trip and validators.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/models.py (codingrules
    section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.models for VirtualCellSpec and NetworkPolicy, the models under test.
    - packages/hivemind/tests/builders/forage.py for make_capacity, reused here for
      VirtualCellSpec.capacity.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity
from pydantic import ValidationError

from hivemind.cell import CombShieldLevel
from hivemind.hive.models import (
    MAX_ALLOWLIST_ENTRIES,
    MAX_LABELS,
    NetworkPolicy,
    VirtualCellSpec,
)
from waggle.clock import FakeClock
from waggle.ids import new_hive_id


def _make_spec(**overrides: object) -> VirtualCellSpec:
    """Build a valid VirtualCellSpec, with sensible defaults for every field a test ignores."""
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 2.0,
        "memory_bytes": 2 * 1024**3,
        "disk_bytes": 10 * 1024**3,
        "capacity": make_capacity(),
        "hive_id": new_hive_id(FakeClock()),
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def test_defaults_are_the_safe_plain_case() -> None:
    spec = _make_spec()

    assert spec.network_policy is NetworkPolicy.NONE
    assert spec.network_allowlist == ()
    assert spec.exoskeleton is False
    assert spec.comb_shield is CombShieldLevel.MEADOW
    assert spec.lifetime_s is None
    assert spec.labels == {}
    assert spec.ready_timeout_s > 0


def test_round_trip_serialise_deserialise_is_equal() -> None:
    spec = _make_spec(labels={"team": "hive"})

    restored = VirtualCellSpec.model_validate(spec.model_dump())

    assert restored == spec


def test_extra_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_spec(not_a_real_field="oops")


@pytest.mark.parametrize("field", ["cpu_cores", "memory_bytes", "disk_bytes"])
def test_non_positive_resource_is_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _make_spec(**{field: 0})


def test_allowlist_policy_without_entries_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_spec(network_policy=NetworkPolicy.ALLOWLIST, network_allowlist=())


def test_non_allowlist_policy_with_entries_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_spec(network_policy=NetworkPolicy.EGRESS_ONLY, network_allowlist=("example.org",))


def test_allowlist_policy_with_entries_is_accepted() -> None:
    spec = _make_spec(network_policy=NetworkPolicy.ALLOWLIST, network_allowlist=("example.org",))

    assert spec.network_allowlist == ("example.org",)


def test_allowlist_over_the_entry_cap_is_rejected() -> None:
    too_many = tuple(f"host{i}.example.org" for i in range(MAX_ALLOWLIST_ENTRIES + 1))

    with pytest.raises(ValidationError):
        _make_spec(network_policy=NetworkPolicy.ALLOWLIST, network_allowlist=too_many)


def test_night_veil_without_vpn_tor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_spec(comb_shield=CombShieldLevel.NIGHT_VEIL, network_policy=NetworkPolicy.NONE)


def test_vpn_tor_without_night_veil_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_spec(comb_shield=CombShieldLevel.MEADOW, network_policy=NetworkPolicy.VPN_TOR)


def test_night_veil_with_vpn_tor_is_accepted() -> None:
    spec = _make_spec(comb_shield=CombShieldLevel.NIGHT_VEIL, network_policy=NetworkPolicy.VPN_TOR)

    assert spec.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert spec.network_policy is NetworkPolicy.VPN_TOR


def test_labels_over_the_cap_is_rejected() -> None:
    too_many = {f"key{i}": "value" for i in range(MAX_LABELS + 1)}

    with pytest.raises(ValidationError):
        _make_spec(labels=too_many)


def test_empty_label_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_spec(labels={"": "value"})


def test_overlong_label_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_spec(labels={"key": "x" * 256})
