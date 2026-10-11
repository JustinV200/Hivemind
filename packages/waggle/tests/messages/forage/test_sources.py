"""Tests for the forage family's source messages (waggle.messages.forage.sources), minor 11.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins SourceAdded, SourceRemoved and SourceEvict
    to spec section 8.4: construction, the round trip through the codec inside an envelope,
    every bound the spec names, the closed SourceRemovalCause set, the catalogue shape of each
    kind and the protocol minor they arrived in. The family's other messages, and EXAMPLES,
    live in test_grants.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.forage.sources for the module under test.
    - docs/waggle/spec.md section 8.4 and the 1.11 history entry for the fields pinned here.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import PROTOCOL_MINOR, PROTOCOL_VERSION, Envelope
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_REASON_CHARS, MessageShape, WaggleMessage
from waggle.messages.forage.capacity import MAX_SERVER_ID_CHARS
from waggle.messages.forage.sources import (
    MAX_CAPABILITY_CHARS,
    MAX_SOURCE_CAPABILITIES,
    SourceAdded,
    SourceEvict,
    SourceRemovalCause,
    SourceRemoved,
)
from waggle.messages.forage.values import SourceRef
from waggle.messages.registry import kind_for, spec_for

CLOCK = FakeClock()
CELL_ID = new_id(IdKind.CELL, CLOCK)
GIB = 1_073_741_824
SOURCE = SourceRef(
    source_id="nuc/small", provider="local_server", model="small-8b", host_cell_id=CELL_ID
)
ADDED = SourceAdded(
    cell_id=CELL_ID,
    server_id="nuc_server",
    source=SOURCE,
    grade=2,
    context_window=8_192,
    seats_total=4,
    vram_bytes=5 * GIB,
    capabilities=("tools", "json"),
    reason="Within the loadable allowlist and the VRAM ceiling.",
)
REMOVED = SourceRemoved(
    cell_id=CELL_ID,
    server_id="nuc_server",
    source=SOURCE,
    cause=SourceRemovalCause.SERVER_DIED,
    reason="The health check failed three times.",
)
EVICT = SourceEvict(
    cell_id=CELL_ID, source=SOURCE, seats_to_reclaim=2, reason="A CRITICAL task needs the seats."
)
MESSAGES: tuple[WaggleMessage, ...] = (ADDED, REMOVED, EVICT)


def _rebuild(message: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``message`` with some fields replaced."""
    return type(message).model_validate({**message.model_dump(), **changes})


# ──────────────────────────────────────────────────────────────────────────────
# Every message
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("message", MESSAGES, ids=lambda message: type(message).__name__)
def test_message_survives_the_codec_inside_an_envelope(
    message: WaggleMessage,
    make_envelope: Callable[..., Envelope],
    plain_codec: Codec,
) -> None:
    envelope = make_envelope(message)

    decoded = plain_codec.decode(plain_codec.encode(envelope))

    assert decoded == envelope
    assert decoded.payload == message
    assert type(decoded.payload) is type(message)


@pytest.mark.parametrize("message", MESSAGES, ids=lambda message: type(message).__name__)
def test_message_is_frozen_and_rejects_an_extra_field(message: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        message.cell_id = "changed"  # type: ignore[attr-defined]  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(message, hop_count=1)


@pytest.mark.parametrize("message", MESSAGES, ids=lambda message: type(message).__name__)
def test_reason_is_bounded_by_the_shared_limit(message: WaggleMessage) -> None:
    assert _rebuild(message, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(message, reason="x" * (MAX_REASON_CHARS + 1))


@pytest.mark.parametrize("message", MESSAGES, ids=lambda message: type(message).__name__)
def test_cell_id_rejects_an_id_of_another_kind(message: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="cell_"):
        _rebuild(message, cell_id=new_id(IdKind.NODE, CLOCK))


@pytest.mark.parametrize("message", [ADDED, REMOVED], ids=lambda message: type(message).__name__)
def test_server_id_is_bounded_like_the_capacity_report_s(message: WaggleMessage) -> None:
    assert _rebuild(message, server_id="s" * MAX_SERVER_ID_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_SERVER_ID_CHARS}"):
        _rebuild(message, server_id="s" * (MAX_SERVER_ID_CHARS + 1))


def test_the_kinds_are_events_registered_at_minor_eleven() -> None:
    assert (PROTOCOL_VERSION, PROTOCOL_MINOR) == ("1.11", 11)
    expected: dict[str, type[WaggleMessage]] = {
        "forage.source_added": SourceAdded,
        "forage.source_removed": SourceRemoved,
        "forage.source_evict": SourceEvict,
    }
    for kind, model in expected.items():
        assert kind_for(model) == kind
        spec = spec_for(kind)
        # The catalogue has no order shape; the Queen's eviction is an event like cell.taint_order.
        assert (spec.shape, spec.replies_to) == (MessageShape.EVENT, None)


# ──────────────────────────────────────────────────────────────────────────────
# SourceAdded
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("grade", [1, 5])
def test_source_added_accepts_the_grade_range_ends(grade: int) -> None:
    assert _rebuild(ADDED, grade=grade)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"grade": 0}, "greater than or equal to 1"),
        ({"grade": 6}, "less than or equal to 5"),
        ({"context_window": 0}, "greater than 0"),
        ({"seats_total": -1}, "greater than or equal to 0"),
        ({"vram_bytes": -1}, "greater than or equal to 0"),
        (
            {"capabilities": ("c",) * (MAX_SOURCE_CAPABILITIES + 1)},
            f"at most {MAX_SOURCE_CAPABILITIES}",
        ),
        ({"capabilities": ("c" * (MAX_CAPABILITY_CHARS + 1),)}, f"at most {MAX_CAPABILITY_CHARS}"),
    ],
)
def test_source_added_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(ADDED, **changes)


def test_source_added_allows_zero_seats_and_no_capabilities_at_the_bounds() -> None:
    edge = _rebuild(
        ADDED,
        seats_total=0,
        vram_bytes=0,
        capabilities=("c" * MAX_CAPABILITY_CHARS,) * MAX_SOURCE_CAPABILITIES,
    )

    assert isinstance(edge, SourceAdded)
    assert len(edge.capabilities) == MAX_SOURCE_CAPABILITIES
    assert _rebuild(ADDED, capabilities=()).model_dump(mode="json")["capabilities"] == []


# ──────────────────────────────────────────────────────────────────────────────
# SourceRemoved and SourceRemovalCause
# ──────────────────────────────────────────────────────────────────────────────


def test_removal_cause_has_exactly_the_spec_members_with_values_equal_to_names() -> None:
    assert [member.name for member in SourceRemovalCause] == [
        "UNLOADED",
        "SERVER_STOPPED",
        "SERVER_DIED",
        "EVICTED",
        "LEASE_RELEASED",
    ]
    assert all(member.value == member.name for member in SourceRemovalCause)


def test_source_removed_carries_every_cause_and_rejects_an_unknown_one() -> None:
    for cause in SourceRemovalCause:
        removed = _rebuild(REMOVED, cause=cause.value)
        assert isinstance(removed, SourceRemoved)
        assert removed.cause is cause
    with pytest.raises(ValidationError, match="cause"):
        _rebuild(REMOVED, cause="VANISHED")


# ──────────────────────────────────────────────────────────────────────────────
# SourceEvict
# ──────────────────────────────────────────────────────────────────────────────


def test_source_evict_needs_at_least_one_seat_to_reclaim() -> None:
    assert _rebuild(EVICT, seats_to_reclaim=1)
    for seats in (0, -1):
        with pytest.raises(ValidationError, match="greater than 0"):
            _rebuild(EVICT, seats_to_reclaim=seats)
