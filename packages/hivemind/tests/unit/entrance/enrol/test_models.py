"""Tests for hivemind.entrance.enrol.models: the Entrance's records, round trips and refusals.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/models.py (codingrules section 3). Every model gets a JSON
    round trip and the rejections its validators promise (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.models for the module under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from builders.entrance import ed25519_public_key, make_description, make_device, make_invite
from pydantic import BaseModel, ValidationError

from hivemind.entrance.auth import KeyKind, b64url_decode, key_fingerprint
from hivemind.entrance.enrol import (
    DeviceDescription,
    DeviceInvite,
    DeviceStatus,
    EnrolledDevice,
    OperatorCredential,
)
from waggle.clock import FakeClock

_PHC = (
    "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaA"
)
# Any, not object: spread into make_device, whose `clock` parameter a dict of objects could hit.
_PASSKEY_FIELDS: dict[str, Any] = {
    "key_kind": KeyKind.PASSKEY,
    "public_key": "pQECAyYgASFYIA",
    "credential_id": "AQIDBA",
    "rp_id": "localhost",
    "interactive": True,
}


def _round_trip[ModelT: BaseModel](model: ModelT) -> ModelT:
    """Serialise ``model`` to JSON and read it back as the same class."""
    return type(model).model_validate_json(model.model_dump_json())


# ──────────────────────────────────────────────────────────────────────────────
# DeviceDescription
# ──────────────────────────────────────────────────────────────────────────────


def test_device_description_round_trips() -> None:
    description = make_description()

    assert _round_trip(description) == description


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "x" * 65},
        {"name": "phone\x1b[31m"},  # A terminal escape.
        {"platform": "Android\n16"},
        {"user_agent": "evil\u202etxt.exe"},  # A right-to-left override.
        {"colour": "blue"},
    ],
)
def test_device_description_refuses_unsafe_or_unknown_text(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_description(**overrides)


# ──────────────────────────────────────────────────────────────────────────────
# EnrolledDevice
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", list(DeviceStatus))
def test_enrolled_device_round_trips_in_every_status(status: DeviceStatus) -> None:
    device = make_device(status=status)

    assert _round_trip(device) == device


def test_a_passkey_device_round_trips_with_its_credential_and_backup_flags() -> None:
    device = make_device(
        status=DeviceStatus.PENDING,
        **_PASSKEY_FIELDS,
        sign_count=7,
        backup_eligible=True,
        backup_state=True,
    )

    assert _round_trip(device) == device


def test_capabilities_are_kept_sorted_and_unique() -> None:
    device = make_device(capabilities=("observe", "entrance:submit", "observe"))

    assert device.capabilities == ("entrance:submit", "observe")


def test_fingerprint_is_the_stored_keys_fingerprint_and_none_before_redemption() -> None:
    public_key = ed25519_public_key()
    pending = make_device(status=DeviceStatus.PENDING, public_key=public_key)

    assert make_device().fingerprint is None
    assert pending.fingerprint == key_fingerprint(b64url_decode(public_key))


def test_last_network_accepts_an_ipv4_24_and_an_ipv6_64() -> None:
    assert make_device(last_network="100.64.3.0/24").last_network == "100.64.3.0/24"
    assert make_device(last_network="fd7a:115c:a1e0::/64").last_network == "fd7a:115c:a1e0::/64"


@pytest.mark.parametrize(
    ("status", "overrides"),
    [
        (DeviceStatus.PENDING, {"public_key": None}),  # A kind without a key.
        (DeviceStatus.PENDING, {"key_kind": KeyKind.PASSKEY}),  # A passkey with no credential.
        (DeviceStatus.PENDING, {**_PASSKEY_FIELDS, "interactive": False}),  # Passkeys have humans.
        (DeviceStatus.PENDING, {"credential_id": "AQIDBA"}),  # A credential on an Ed25519 key.
        (DeviceStatus.PENDING, {"sign_count": 1}),  # A counter on an Ed25519 key.
        (DeviceStatus.PENDING, {"backup_eligible": True}),  # Only passkeys sync.
        (DeviceStatus.PENDING, {"public_key": "AQID"}),  # Not 32 bytes.
        (DeviceStatus.PENDING, {"public_key": "AB"}),  # Not canonical base64url.
        (DeviceStatus.PENDING, {**_PASSKEY_FIELDS, "backup_state": True}),  # BS without BE.
        (DeviceStatus.PENDING, {"description": None}),  # Redeemed without describing itself.
        (DeviceStatus.INVITED, {"key_kind": KeyKind.ED25519, "public_key": "A" * 43}),
        (DeviceStatus.APPROVED, {"approved_at": None}),
        (DeviceStatus.LOCKED, {"approved_at": datetime(2019, 1, 1, tzinfo=UTC)}),  # Too early.
        (DeviceStatus.APPROVED, {"loopback_bound": True, "interactive": False}),
        (DeviceStatus.INVITED, {"last_network": "100.64.3.0/16"}),  # Not the device's /24.
        (DeviceStatus.INVITED, {"last_network": "100.64.3.7/24"}),  # Host bits set.
        (DeviceStatus.INVITED, {"capabilities": ("Observe",)}),  # Not a family name.
        (DeviceStatus.INVITED, {"capabilities": ("observe\n",)}),
        (DeviceStatus.INVITED, {"created_at": datetime(2026, 1, 1)}),  # A naive timestamp.
        (DeviceStatus.INVITED, {"id": "cell_01M221E4C10R4XDPNQNRX85AAA"}),  # Wrong id kind.
        (DeviceStatus.INVITED, {"trusted": True}),
    ],
)
def test_enrolled_device_refuses_an_inconsistent_record(
    status: DeviceStatus, overrides: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        make_device(status=status, **overrides)


# ──────────────────────────────────────────────────────────────────────────────
# DeviceInvite and OperatorCredential
# ──────────────────────────────────────────────────────────────────────────────


def test_device_invite_round_trips_unused_and_used() -> None:
    clock = FakeClock()
    invite = make_invite(make_device(clock), clock)
    used = make_invite(make_device(clock), clock, used_at=clock.now() + timedelta(minutes=1))

    assert _round_trip(invite) == invite
    assert _round_trip(used) == used


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_hash": "A" * 64},  # Upper-case hex is a second spelling.
        {"code_hash": "a" * 63},
        {"expires_at": datetime(2020, 1, 1, tzinfo=UTC)},  # Not after created_at.
        {"used_at": datetime(2020, 1, 1, 1, tzinfo=UTC)},  # After expiry.
        {"used_at": datetime(2019, 12, 31, tzinfo=UTC)},  # Before creation.
        {"label": "phone\x07"},
    ],
)
def test_device_invite_refuses_a_malformed_invite(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_invite(make_device(), FakeClock(), **overrides)


def test_operator_credential_round_trips_and_keeps_its_hash_out_of_repr() -> None:
    at = datetime(2026, 9, 24, tzinfo=UTC)
    credential = OperatorCredential(password_hash=_PHC, created_at=at, changed_at=at)

    assert _round_trip(credential) == credential
    assert _PHC not in repr(credential)


@pytest.mark.parametrize(
    ("password_hash", "changed_at"),
    [
        ("correct horse battery staple", datetime(2026, 9, 24, tzinfo=UTC)),  # A plaintext.
        (_PHC.replace("argon2id", "argon2i"), datetime(2026, 9, 24, tzinfo=UTC)),
        (_PHC, datetime(2026, 9, 23, tzinfo=UTC)),  # Changed before it was created.
    ],
)
def test_operator_credential_refuses_anything_but_an_argon2id_hash_in_order(
    password_hash: str, changed_at: datetime
) -> None:
    with pytest.raises(ValidationError):
        OperatorCredential(
            password_hash=password_hash,
            created_at=datetime(2026, 9, 24, tzinfo=UTC),
            changed_at=changed_at,
        )


@pytest.mark.parametrize("model", [DeviceDescription, EnrolledDevice, DeviceInvite])
def test_every_record_is_frozen_and_forbids_unknown_fields(model: type[BaseModel]) -> None:
    assert model.model_config.get("frozen") is True
    assert model.model_config.get("extra") == "forbid"


def test_a_record_cannot_be_changed_in_place() -> None:
    device = make_device()

    with pytest.raises(ValidationError, match="frozen"):
        device.name = "renamed"  # The assignment is the test.
