"""Tests for waggle.messages.cell.snapshot: the Warden -> Queen snapshot relay (PROTOCOL_MINOR 5).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). For CellSnapshotRequest, CellSnapshotReply,
    CellRollbackRequest and CellRollbackReply it pins construction, the JSON round trip, the
    rejection of an extra field, every bound, and the exactly-one-outcome validators on both
    reply classes. The family's EXAMPLES live in test_status.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.cell.snapshot for the module under test.
    - docs/waggle/spec.md section 8.5 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.cell.snapshot import (
    MAX_ERROR_CHARS,
    MAX_PURPOSE_CHARS,
    MAX_SNAPSHOT_ID_CHARS,
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
)

CLOCK = FakeClock()
CELL_ID = new_id(IdKind.CELL, CLOCK)
SNAPSHOT_ID = "snap_docker_cell_01-20260101t000000000000-0"


def _rebuild(example: object, **changes: object) -> object:
    """Re-validate a WaggleMessage instance with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────────────
# CellSnapshotRequest
# ──────────────────────────────────────────────────────────────────────────────


def test_snapshot_request_round_trips_and_is_frozen() -> None:
    request = CellSnapshotRequest(cell_id=CELL_ID, purpose="A capping.irreversible proposal.")

    assert CellSnapshotRequest.model_validate(request.model_dump(mode="json")) == request
    with pytest.raises(ValidationError, match="frozen"):
        request.cell_id = "changed"  # type: ignore[misc,assignment]  # The assignment is the test.


def test_snapshot_request_rejects_an_extra_field() -> None:
    request = CellSnapshotRequest(cell_id=CELL_ID, purpose="x")
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(request, snapshot_id="snap_1")


def test_snapshot_request_purpose_is_bounded() -> None:
    request = CellSnapshotRequest(cell_id=CELL_ID, purpose="x" * MAX_PURPOSE_CHARS)
    assert request.purpose

    with pytest.raises(ValidationError, match=f"at most {MAX_PURPOSE_CHARS}"):
        CellSnapshotRequest(cell_id=CELL_ID, purpose="x" * (MAX_PURPOSE_CHARS + 1))
    with pytest.raises(ValidationError, match="at least 1"):
        CellSnapshotRequest(cell_id=CELL_ID, purpose="")


# ──────────────────────────────────────────────────────────────────────────────
# CellSnapshotReply
# ──────────────────────────────────────────────────────────────────────────────


def test_snapshot_reply_accepts_success_or_error_exclusively() -> None:
    success = CellSnapshotReply(cell_id=CELL_ID, snapshot_id=SNAPSHOT_ID, error=None)
    failure = CellSnapshotReply(cell_id=CELL_ID, snapshot_id=None, error="unknown Cell")

    assert success.snapshot_id == SNAPSHOT_ID
    assert failure.error == "unknown Cell"
    with pytest.raises(ValidationError, match="exactly one of snapshot_id and error"):
        CellSnapshotReply(cell_id=CELL_ID, snapshot_id=None, error=None)
    with pytest.raises(ValidationError, match="exactly one of snapshot_id and error"):
        CellSnapshotReply(cell_id=CELL_ID, snapshot_id=SNAPSHOT_ID, error="both set")


def test_snapshot_reply_bounds_and_extra_field() -> None:
    reply = CellSnapshotReply(cell_id=CELL_ID, snapshot_id=SNAPSHOT_ID, error=None)
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(reply, reason="not a field here")
    with pytest.raises(ValidationError, match=f"at most {MAX_SNAPSHOT_ID_CHARS}"):
        CellSnapshotReply(
            cell_id=CELL_ID, snapshot_id="x" * (MAX_SNAPSHOT_ID_CHARS + 1), error=None
        )
    with pytest.raises(ValidationError, match=f"at most {MAX_ERROR_CHARS}"):
        CellSnapshotReply(cell_id=CELL_ID, snapshot_id=None, error="x" * (MAX_ERROR_CHARS + 1))


# ──────────────────────────────────────────────────────────────────────────────
# CellRollbackRequest / CellRollbackReply
# ──────────────────────────────────────────────────────────────────────────────


def test_rollback_request_round_trips_and_bounds() -> None:
    request = CellRollbackRequest(cell_id=CELL_ID, snapshot_id=SNAPSHOT_ID)

    assert CellRollbackRequest.model_validate(request.model_dump(mode="json")) == request
    with pytest.raises(ValidationError, match=f"at most {MAX_SNAPSHOT_ID_CHARS}"):
        CellRollbackRequest(cell_id=CELL_ID, snapshot_id="x" * (MAX_SNAPSHOT_ID_CHARS + 1))
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(request, purpose="x")


def test_rollback_reply_requires_error_exactly_when_not_ok() -> None:
    ok = CellRollbackReply(cell_id=CELL_ID, ok=True, error=None)
    failed = CellRollbackReply(cell_id=CELL_ID, ok=False, error="daemon unreachable")

    assert ok.error is None
    assert failed.error == "daemon unreachable"
    with pytest.raises(ValidationError, match="error must be set exactly when ok is False"):
        CellRollbackReply(cell_id=CELL_ID, ok=False, error=None)
    with pytest.raises(ValidationError, match="error must be set exactly when ok is False"):
        CellRollbackReply(cell_id=CELL_ID, ok=True, error="should not be set")


def test_rollback_reply_rejects_an_extra_field() -> None:
    reply = CellRollbackReply(cell_id=CELL_ID, ok=True, error=None)
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(reply, snapshot_id=SNAPSHOT_ID)
