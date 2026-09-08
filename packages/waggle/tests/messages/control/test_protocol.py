"""Tests for the control family (waggle.messages.control.protocol and hive): all nine classes.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    control class, which the registry step later loads by file path to seed its per-family
    checks; and for every class pins construction, the JSON round trip, the rejection of an
    extra field, at least one bound, and every validator spec section 8.11 names.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the nine control classes.

See Also:
    - waggle.messages.control.protocol and waggle.messages.control.hive for the modules under test.
    - docs/waggle/spec.md section 8.11 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_REASON_CHARS, WaggleMessage
from waggle.messages.control.hive import (
    MAX_ADDRESS_CHARS,
    MAX_HUMAN_TEXT_CHARS,
    HumanMessage,
    MaskOverride,
    MaskOverrideAction,
    MaskTactic,
    QueenMoved,
)
from waggle.messages.control.protocol import (
    MAX_ERROR_CODE_CHARS,
    MAX_ERROR_MESSAGE_CHARS,
    MAX_FAILED_KIND_CHARS,
    MAX_PROVIDER_CHARS,
    Cluster,
    ClusterCause,
    ErrorMessage,
    Ping,
    Pong,
    Shutdown,
    Wake,
)
from waggle.messages.labels import HoneyClearance, Urgency

CLOCK = FakeClock()
NOW = CLOCK.now()
LATER = NOW + timedelta(hours=1)
KEY_HEX = "ab" * 32  # 32 bytes of public key as 64 lowercase hex characters.
CONTROL_CLASSES: tuple[type[WaggleMessage], ...] = (
    Ping,
    Pong,
    ErrorMessage,
    Shutdown,
    Cluster,
    Wake,
    HumanMessage,
    MaskOverride,
    QueenMoved,
)

# One valid instance of every control class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    Ping(),
    Pong(received_at=NOW),
    ErrorMessage(
        code="waggle.codec.invalid_payload",
        message="The frame with id msg_x failed validation.",
        failed_kind="task.assign",
        is_retryable=False,
    ),
    Shutdown(urgency=Urgency.GRACEFUL, deadline_s=30.0, reason="Operator requested a stop."),
    Cluster(provider="anthropic", cause=ClusterCause.PROVIDER_DOWN, reason="API returned 503."),
    Wake(provider=None, reason="Every provider answered a probe."),
    HumanMessage(
        text="Please pause the release.", task_id=None, device_id=new_id(IdKind.DEVICE, CLOCK)
    ),
    MaskOverride(
        cell_id=new_id(IdKind.CELL, CLOCK),
        action=MaskOverrideAction.FORCE,
        tactics=(MaskTactic.WRITE_LIKE_HUMAN,),
        expires_at=LATER,
        reason="The site rate-limits obvious bots.",
    ),
    QueenMoved(
        sequence=1,
        new_address="wss://queen.example.org:8443/waggle",
        new_node_id=new_id(IdKind.NODE, CLOCK),
        new_node_public_key_hex=KEY_HEX,
        effective_at=NOW,
        grace_until=LATER,
        reason="Supersedure to the new Hive Stand.",
    ),
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


def _example(message_type: type[WaggleMessage]) -> WaggleMessage:
    """The EXAMPLES entry of ``message_type``."""
    return next(example for example in EXAMPLES if type(example) is message_type)


# ──────────────────────────────────────────────────────────────────────────────
# Every class
# ──────────────────────────────────────────────────────────────────────────────


def test_examples_hold_exactly_one_instance_of_every_control_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == CONTROL_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_control_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.reason = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_control_message_rejects_an_extra_field(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)


@pytest.mark.parametrize("example", [_example(Shutdown), _example(Cluster), _example(Wake)])
def test_reason_is_bounded_by_the_shared_limit(example: WaggleMessage) -> None:
    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


# ──────────────────────────────────────────────────────────────────────────────
# Ping, Pong, ErrorMessage
# ──────────────────────────────────────────────────────────────────────────────


def test_ping_has_no_fields() -> None:
    assert Ping.model_fields == {}
    assert Ping().model_dump(mode="json") == {}


def test_pong_normalises_received_at_and_rejects_a_naive_time() -> None:
    assert Pong(received_at=NOW).received_at.tzinfo is UTC
    with pytest.raises(ValidationError, match="timezone-aware"):
        Pong(received_at=datetime(2020, 1, 1))  # naive on purpose


@pytest.mark.parametrize("code", ["Waggle.Error", "nodots", "waggle.", ".waggle", "1.two", "a b.c"])
def test_error_message_rejects_a_code_outside_the_pattern(code: str) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        _rebuild(_example(ErrorMessage), code=code)


def test_error_message_bounds() -> None:
    example = _example(ErrorMessage)
    assert _rebuild(example, code="a." + "b" * (MAX_ERROR_CODE_CHARS - 2))
    with pytest.raises(ValidationError, match=f"at most {MAX_ERROR_CODE_CHARS}"):
        _rebuild(example, code="a." + "b" * (MAX_ERROR_CODE_CHARS - 1))
    with pytest.raises(ValidationError, match="at least 1"):
        _rebuild(example, message="")
    with pytest.raises(ValidationError, match=f"at most {MAX_ERROR_MESSAGE_CHARS}"):
        _rebuild(example, message="x" * (MAX_ERROR_MESSAGE_CHARS + 1))


def test_error_message_failed_kind_is_optional_and_shaped_like_a_kind() -> None:
    example = _example(ErrorMessage)
    assert _rebuild(example, failed_kind=None).model_dump()["failed_kind"] is None
    with pytest.raises(ValidationError, match="pattern"):
        _rebuild(example, failed_kind="nodots")
    with pytest.raises(ValidationError, match=f"at most {MAX_FAILED_KIND_CHARS}"):
        _rebuild(example, failed_kind="a." + "b" * (MAX_FAILED_KIND_CHARS - 1))


# ──────────────────────────────────────────────────────────────────────────────
# Shutdown, Cluster, Wake
# ──────────────────────────────────────────────────────────────────────────────


def test_shutdown_immediate_requires_a_zero_deadline() -> None:
    assert Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="Sting Cut.").deadline_s == 0
    with pytest.raises(
        ValidationError, match=re.escape("IMMEDIATE shutdown must carry deadline_s 0.0")
    ):
        Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=5.0, reason="Sting Cut.")


def test_shutdown_rejects_a_negative_deadline() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Shutdown(urgency=Urgency.GRACEFUL, deadline_s=-1.0, reason="x")


@pytest.mark.parametrize("message_type", [Cluster, Wake])
@pytest.mark.parametrize(
    "provider", ["Anthropic", "1abc", "a-b", "", "x" * (MAX_PROVIDER_CHARS + 1)]
)
def test_cluster_and_wake_reject_a_provider_outside_the_manifest_key_shape(
    message_type: type[WaggleMessage], provider: str
) -> None:
    with pytest.raises(ValidationError, match="provider"):
        _rebuild(_example(message_type), provider=provider)


@pytest.mark.parametrize("message_type", [Cluster, Wake])
def test_cluster_and_wake_accept_none_as_every_provider(message_type: type[WaggleMessage]) -> None:
    assert _rebuild(_example(message_type), provider=None).model_dump()["provider"] is None


# ──────────────────────────────────────────────────────────────────────────────
# HumanMessage
# ──────────────────────────────────────────────────────────────────────────────


def test_human_message_clearance_is_always_c2() -> None:
    example = _example(HumanMessage)
    assert isinstance(example, HumanMessage)
    assert example.clearance is HoneyClearance.C2
    assert example.model_dump(mode="json")["clearance"] == "C2"
    assert _rebuild(example, clearance="C2") == example
    for lower in ("C1", "C0", HoneyClearance.C1, "public"):
        with pytest.raises(ValidationError, match="clearance"):
            _rebuild(example, clearance=lower)


def test_human_message_bounds_and_ids() -> None:
    example = _example(HumanMessage)
    with pytest.raises(ValidationError, match="at least 1"):
        _rebuild(example, text="")
    with pytest.raises(ValidationError, match=f"at most {MAX_HUMAN_TEXT_CHARS}"):
        _rebuild(example, text="x" * (MAX_HUMAN_TEXT_CHARS + 1))
    assert _rebuild(example, task_id=new_id(IdKind.TASK, CLOCK))
    with pytest.raises(ValidationError, match="task_"):
        _rebuild(example, task_id=new_id(IdKind.CELL, CLOCK))
    with pytest.raises(ValidationError, match="device_"):
        _rebuild(example, device_id=new_id(IdKind.WORKER, CLOCK))


# ──────────────────────────────────────────────────────────────────────────────
# MaskOverride
# ──────────────────────────────────────────────────────────────────────────────


def test_mask_override_clear_carries_neither_tactics_nor_expiry() -> None:
    cleared = _rebuild(_example(MaskOverride), action="CLEAR", tactics=(), expires_at=None)

    assert cleared.model_dump(mode="json")["tactics"] == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"tactics": ()}, "non-empty exactly when action is FORCE"),
        ({"expires_at": None}, "required exactly when action is FORCE"),
        ({"action": "CLEAR", "expires_at": None}, "non-empty exactly when action is FORCE"),
        ({"action": "CLEAR", "tactics": ()}, "required exactly when action is FORCE"),
        ({"tactics": ("WRITE_LIKE_HUMAN", "WRITE_LIKE_HUMAN")}, "unique"),
        ({"tactics": ("WRITE_LIKE_HUMAN", "MOUSE_LIKE_HUMAN", "WRITE_LIKE_HUMAN")}, "at most 2"),
        ({"cell_id": "task_01ARZ3NDEKTSV4RRFFQ69G5FAV"}, "cell_"),
    ],
)
def test_mask_override_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(MaskOverride), **changes)


def test_mask_override_accepts_both_tactics_once() -> None:
    both = _rebuild(_example(MaskOverride), tactics=("WRITE_LIKE_HUMAN", "MOUSE_LIKE_HUMAN"))

    assert isinstance(both, MaskOverride)
    assert both.tactics == (MaskTactic.WRITE_LIKE_HUMAN, MaskTactic.MOUSE_LIKE_HUMAN)


# ──────────────────────────────────────────────────────────────────────────────
# QueenMoved
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "address", ["ws://127.0.0.1:9000", "ws://localhost", "ws://[::1]:1", "wss://10.0.0.1"]
)
def test_queen_moved_accepts_tls_anywhere_and_plaintext_on_loopback(address: str) -> None:
    moved = _rebuild(_example(QueenMoved), new_address=address)

    assert isinstance(moved, QueenMoved)
    assert moved.new_address == address
    assert moved.is_rollback is False


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"new_address": "ws://queen.example.org"}, "loopback"),
        ({"new_address": "http://127.0.0.1"}, "pattern"),
        ({"new_address": "wss://x" + "y" * MAX_ADDRESS_CHARS}, f"at most {MAX_ADDRESS_CHARS}"),
        ({"sequence": 0}, "greater than or equal to 1"),
        ({"new_node_public_key_hex": KEY_HEX.upper()}, "pattern"),
        ({"new_node_public_key_hex": KEY_HEX[:-1]}, "pattern"),
        ({"new_node_id": "hive_01ARZ3NDEKTSV4RRFFQ69G5FAV"}, "node_"),
        ({"grace_until": NOW - timedelta(seconds=1)}, "precedes effective_at"),
    ],
)
def test_queen_moved_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(QueenMoved), **changes)


def test_queen_moved_grace_may_equal_effective_and_rollback_is_explicit() -> None:
    rollback = _rebuild(_example(QueenMoved), grace_until=NOW, is_rollback=True)

    assert isinstance(rollback, QueenMoved)
    assert rollback.grace_until == rollback.effective_at
    assert rollback.is_rollback is True
