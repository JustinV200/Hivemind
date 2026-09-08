"""Tests for waggle.messages.base: the payload root, shapes, shared bounds and field aliases.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins the shared constants to the spec's
    numbers, the WaggleMessage configuration every family inherits, check_id and id_validator,
    the UtcDatetime alias, and every <Kind>IdField alias against its own kind and the others.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.base for the module under test.
    - docs/waggle/spec.md sections 2 (Time), 3 (duplicates and freshness) and 8 (conventions).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages import base
from waggle.messages.base import (
    VALUE_MODEL_CONFIG,
    AlarmIdField,
    CellIdField,
    DeviceIdField,
    EventIdField,
    GrantIdField,
    HiveIdField,
    LeaseIdField,
    MessageIdField,
    MessageShape,
    NodeIdField,
    TaskIdField,
    ToolIdField,
    UtcDatetime,
    WaggleMessage,
    WardenIdField,
    WorkerIdField,
    check_id,
    id_validator,
)

CLOCK = FakeClock()

# Every alias with the kind it must accept; the "exactly one per IdKind" test below keeps this
# table and the module's exports in step.
_ALIASES: list[tuple[object, IdKind]] = [
    (HiveIdField, IdKind.HIVE),
    (CellIdField, IdKind.CELL),
    (LeaseIdField, IdKind.LEASE),
    (TaskIdField, IdKind.TASK),
    (WorkerIdField, IdKind.WORKER),
    (WardenIdField, IdKind.WARDEN),
    (AlarmIdField, IdKind.ALARM),
    (GrantIdField, IdKind.GRANT),
    (ToolIdField, IdKind.TOOL),
    (NodeIdField, IdKind.NODE),
    (EventIdField, IdKind.EVENT),
    (DeviceIdField, IdKind.DEVICE),
    (MessageIdField, IdKind.MESSAGE),
]

# The spec's shared numbers (sections 3, 5 and 8); the number is normative, so it is pinned.
_CONSTANTS: list[tuple[str, object]] = [
    ("MAX_REASON_CHARS", 1_000),
    ("MAX_CHUNK_BYTES", 262_144),
    ("MAX_PATH_CHARS", 4_096),
    ("MAX_SLOT_CHARS", 32),
    ("SLOT_PATTERN", r"^[A-Z][A-Z_]*$"),
    ("SHA256_PATTERN", r"^[0-9a-f]{64}$"),
    ("KIND_PATTERN", r"^[a-z_]+\.[a-z_]+$"),
    ("MAX_SUB_BEES_ON_WIRE", 64),
    ("MAX_MESSAGE_AGE_S", 604_800),
    ("MAX_CLOCK_SKEW_S", 300),
    ("MAX_OPEN_CHUNK_GROUPS", 8),
    ("CHUNK_GROUP_TIMEOUT_S", 60.0),
    ("DEFAULT_MAX_OUTPUT_BYTES", 16_777_216),
]


class _Stamped(BaseModel):
    """A one-field model to exercise UtcDatetime through pydantic."""

    model_config = VALUE_MODEL_CONFIG

    at: UtcDatetime


# ──────────────────────────────────────────────────────────────────────────────
# Constants, shapes and the root configuration
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "value"), _CONSTANTS)
def test_shared_constant_has_the_spec_value(name: str, value: object) -> None:
    assert getattr(base, name) == value
    assert name in base.__all__


def test_message_shapes_are_request_reply_event() -> None:
    assert [shape.value for shape in MessageShape] == ["request", "reply", "event"]


def test_waggle_message_is_frozen_forbids_extras_and_carries_bytes_as_base64() -> None:
    config = WaggleMessage.model_config

    assert config["frozen"] is True
    assert config["extra"] == "forbid"
    assert config["ser_json_bytes"] == "base64"
    assert config["val_json_bytes"] == "base64"
    assert VALUE_MODEL_CONFIG is WaggleMessage.model_config


def test_waggle_message_has_no_fields_of_its_own() -> None:
    assert WaggleMessage.model_fields == {}
    assert hash(WaggleMessage()) == hash(WaggleMessage())


# ──────────────────────────────────────────────────────────────────────────────
# check_id and id_validator
# ──────────────────────────────────────────────────────────────────────────────


def test_check_id_returns_a_well_formed_id_unchanged() -> None:
    task_id = new_id(IdKind.TASK, CLOCK)

    assert check_id(task_id, IdKind.TASK) == task_id


def test_check_id_raises_value_error_not_invalid_id_error() -> None:
    # pydantic only reports ValueError; a WaggleError would escape a validator untouched.
    with pytest.raises(ValueError, match="prefix") as caught:
        check_id(new_id(IdKind.TASK, CLOCK), IdKind.CELL)

    assert type(caught.value) is ValueError
    assert caught.value.__cause__ is not None


def test_id_validator_requires_at_least_one_kind() -> None:
    with pytest.raises(ValueError, match="at least one IdKind"):
        id_validator()


def test_id_validator_over_several_kinds_accepts_each_and_names_the_set_otherwise() -> None:
    adapter: TypeAdapter[str] = TypeAdapter(
        Annotated[str, id_validator(IdKind.WORKER, IdKind.WARDEN)]
    )

    for kind in (IdKind.WORKER, IdKind.WARDEN):
        accepted = new_id(kind, CLOCK)
        assert adapter.validate_python(accepted) == accepted
    with pytest.raises(ValidationError, match="worker_, warden_"):
        adapter.validate_python(new_id(IdKind.HIVE, CLOCK))
    with pytest.raises(ValidationError, match="ulid"):
        adapter.validate_python("worker_" + "U" * 26)


# ──────────────────────────────────────────────────────────────────────────────
# UtcDatetime
# ──────────────────────────────────────────────────────────────────────────────


def test_utc_datetime_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _Stamped(at=datetime(2020, 1, 1))  # naive on purpose


def test_utc_datetime_normalises_an_aware_datetime_to_utc() -> None:
    east = timezone(timedelta(hours=5))

    stamped = _Stamped(at=datetime(2020, 1, 1, 5, tzinfo=east))

    assert stamped.at == datetime(2020, 1, 1, 0, tzinfo=UTC)
    assert stamped.at.utcoffset() == timedelta(0)


def test_utc_datetime_keeps_a_utc_datetime_and_parses_a_wire_string() -> None:
    assert _Stamped(at=datetime(2020, 1, 1, tzinfo=UTC)).at == datetime(2020, 1, 1, tzinfo=UTC)
    parsed = _Stamped.model_validate({"at": "2020-01-01T02:00:00+02:00"})
    assert parsed.at == datetime(2020, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError, match="timezone-aware"):
        _Stamped.model_validate({"at": "2020-01-01T02:00:00"})


# ──────────────────────────────────────────────────────────────────────────────
# One alias per IdKind
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("alias", "kind"), _ALIASES)
def test_id_alias_accepts_its_own_kind_and_rejects_every_other(alias: object, kind: IdKind) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(alias)

    own = new_id(kind, CLOCK)
    assert adapter.validate_python(own) == own
    for other in IdKind:
        if other is not kind:
            with pytest.raises(ValidationError, match="prefix"):
                adapter.validate_python(new_id(other, CLOCK))


@pytest.mark.parametrize(("alias", "kind"), _ALIASES)
def test_id_alias_rejects_garbage_and_non_strings(alias: object, kind: IdKind) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(alias)

    with pytest.raises(ValidationError):
        adapter.validate_python(f"{kind.value}_short")
    with pytest.raises(ValidationError):
        adapter.validate_python(12345)


def test_exactly_one_alias_exists_per_id_kind() -> None:
    exported = {name for name in base.__all__ if name.endswith("IdField")}
    expected = {f"{kind.name.capitalize()}IdField" for kind in IdKind}

    assert exported == expected
    assert {kind for _, kind in _ALIASES} == set(IdKind)
    assert len(_ALIASES) == len(IdKind)
