"""Unit tests for hivemind.hive.backends.base: BackendCapabilities and VirtualCellRecord.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/backends/base.py
    (codingrules section 3). CellBackend itself is a Protocol with no logic of its own; its
    contract is exercised end to end by
    packages/hivemind/tests/contracts/test_cell_backend_contract.py instead.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.base for BackendCapabilities and VirtualCellRecord, under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hivemind.hive.backends.base import BackendCapabilities, VirtualCellRecord
from hivemind.hive.cell_state import VirtualCellStatus
from waggle.clock import FakeClock
from waggle.ids import new_cell_id


def test_backend_capabilities_defaults_headroom_to_none() -> None:
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True)

    assert capabilities.headroom is None


def test_backend_capabilities_rejects_negative_headroom() -> None:
    with pytest.raises(ValidationError):
        BackendCapabilities(can_snapshot=False, can_pause=True, headroom=-1)


def test_backend_capabilities_rejects_unknown_field() -> None:
    payload = {"can_snapshot": False, "can_pause": True, "not_a_real_field": True}
    with pytest.raises(ValidationError):
        BackendCapabilities.model_validate(payload)


def test_backend_capabilities_is_frozen() -> None:
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True)

    with pytest.raises(ValidationError, match="frozen"):
        capabilities.can_pause = False  # type: ignore[misc]  # The assignment is the test.


def test_virtual_cell_record_carries_every_field() -> None:
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    created_at = datetime.now(UTC)

    record = VirtualCellRecord(
        cell_id=cell_id,
        status=VirtualCellStatus.READY,
        image="base-ubuntu",
        labels={"hive_id": "hive_test"},
        created_at=created_at,
    )

    assert record.cell_id == cell_id
    assert record.status is VirtualCellStatus.READY
    assert record.image == "base-ubuntu"
    assert record.labels == {"hive_id": "hive_test"}
    assert record.created_at == created_at
