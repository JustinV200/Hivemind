"""Tests for hivemind.entrance.enrol.grants: the device ceiling rule and the steward rule.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/grants.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.grants for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.entrance import make_device

from hivemind.entrance.enrol import (
    DeviceStatus,
    EnrolledDevice,
    approval_grant,
    device_ceiling,
    steward_grant,
    steward_terms,
)
from hivemind.entrance.errors import CapabilityCeilingError, StewardGrantError
from hivemind.guard import CapabilitySet, InvalidCapabilityError, load_guard_policy, proposed_set

_POLICY = load_guard_policy()  # The shipped policy: its device role is the ceiling under test.
_CEILING = device_ceiling(_POLICY)
_STEWARD_SET = ("entrance:steward", "entrance:submit", "entrance:answer", "observe", "spend:*")


def _steward(
    status: DeviceStatus = DeviceStatus.APPROVED, capabilities: tuple[str, ...] = _STEWARD_SET
) -> EnrolledDevice:
    """A steward device (by default approved, holding stewardship and unlimited spend)."""
    return make_device(status=status, capabilities=capabilities)


# ──────────────────────────────────────────────────────────────────────────────
# The operator's approval: inside the device ceiling
# ──────────────────────────────────────────────────────────────────────────────


def test_naming_nothing_grants_the_proposed_set_which_leaves_out_stewardship() -> None:
    granted = approval_grant(_POLICY, None)

    assert granted == proposed_set(_POLICY)
    assert "entrance:steward" not in granted.as_strings()
    assert granted.issubset(_CEILING)


def test_named_capabilities_inside_the_ceiling_are_granted_as_named() -> None:
    named = ("entrance:submit", "net:api.example.com", "fs:read:/data/**", "entrance:steward")

    granted = approval_grant(_POLICY, named)

    assert granted == CapabilitySet.parse(*named)


@pytest.mark.parametrize("beyond", ["spend:10", "supersede", "sting_cut", "warden:spawn"])
def test_a_capability_beyond_the_ceiling_is_refused_by_name(beyond: str) -> None:
    with pytest.raises(CapabilityCeilingError) as excinfo:
        approval_grant(_POLICY, ("observe", beyond))

    assert excinfo.value.capability == beyond
    assert beyond in str(excinfo.value)


def test_the_first_offender_in_sorted_order_is_the_one_named() -> None:
    with pytest.raises(CapabilityCeilingError) as excinfo:
        approval_grant(_POLICY, ("supersede", "spend:1"))

    assert excinfo.value.capability == "spend:1"


def test_a_string_that_is_not_a_capability_is_refused_by_the_grammar() -> None:
    with pytest.raises(InvalidCapabilityError, match="observe:everything"):
        approval_grant(_POLICY, ("observe:everything",))


def test_an_empty_list_grants_nothing() -> None:
    assert approval_grant(_POLICY, ()) == CapabilitySet.empty()


# ──────────────────────────────────────────────────────────────────────────────
# The steward rule: its own set, within the ceiling, never stewardship
# ──────────────────────────────────────────────────────────────────────────────


def test_a_steward_may_grant_what_it_holds_inside_the_ceiling() -> None:
    requested = CapabilitySet.parse("entrance:submit", "observe")

    assert steward_grant(_steward(), requested, _CEILING) is requested


@pytest.mark.parametrize(
    ("requested", "reason"),
    [
        ("entrance:steward", "granted only at the Hive Stand"),
        ("entrance:push", "does not hold it"),
        ("spend:5", "beyond the device ceiling"),
    ],
)
def test_a_steward_may_not_grant_stewardship_what_it_lacks_or_beyond_the_ceiling(
    requested: str, reason: str
) -> None:
    steward = _steward()

    with pytest.raises(StewardGrantError, match=reason) as excinfo:
        steward_grant(steward, CapabilitySet.parse("observe", requested), _CEILING)

    assert excinfo.value.capability == requested


@pytest.mark.parametrize(
    "steward",
    [
        _steward(capabilities=("entrance:submit", "observe")),  # Not flagged as a steward.
        _steward(status=DeviceStatus.LOCKED),  # A locked steward approves nothing.
    ],
)
def test_only_an_approved_device_holding_stewardship_may_approve(steward: EnrolledDevice) -> None:
    with pytest.raises(StewardGrantError, match="not an approved device") as excinfo:
        steward_grant(steward, CapabilitySet.parse("observe"), _CEILING)

    assert excinfo.value.capability is None


# ──────────────────────────────────────────────────────────────────────────────
# A steward's approval: no more spend per day, and no longer life, than its own
# ──────────────────────────────────────────────────────────────────────────────


def test_a_steward_grants_up_to_its_own_cap_and_expiry() -> None:
    steward = make_device(status=DeviceStatus.APPROVED, spend_cap_usd_per_day=5.0)
    assert steward.expires_at is not None

    steward_terms(steward, 5.0, steward.expires_at)
    steward_terms(steward, 0.0, steward.expires_at - timedelta(hours=1))


def test_a_steward_may_not_grant_a_cap_above_its_own() -> None:
    steward = make_device(status=DeviceStatus.APPROVED, spend_cap_usd_per_day=5.0)

    with pytest.raises(StewardGrantError, match="daily cap above its own 5 USD"):
        steward_terms(steward, 5.01, steward.expires_at)


@pytest.mark.parametrize("longer", [timedelta(seconds=1), None])
def test_a_steward_may_not_grant_an_approval_outlasting_its_own(longer: timedelta | None) -> None:
    steward = make_device(status=DeviceStatus.APPROVED, spend_cap_usd_per_day=5.0)
    assert steward.expires_at is not None
    # None asks for an approval that never lapses, which outlasts any steward that does.
    asked = steward.expires_at + longer if longer is not None else None

    with pytest.raises(StewardGrantError, match="outlasting its own"):
        steward_terms(steward, 1.0, asked)


def test_an_uncapped_never_lapsing_steward_bounds_neither_term() -> None:
    steward = make_device(status=DeviceStatus.APPROVED, spend_cap_usd_per_day=None, expires_at=None)

    steward_terms(steward, 1_000.0, None)
