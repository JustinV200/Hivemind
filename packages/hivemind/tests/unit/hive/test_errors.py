"""Unit tests for hivemind.hive.errors: message shape and code stability for each error class.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/errors.py (codingrules
    section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.errors for every class under test.
"""

from __future__ import annotations

from hivemind.common.errors import ConfigurationError, ConflictError, HiveMindError
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import (
    BackendCapabilityError,
    CellDestroyError,
    CellProvisionError,
    HiveError,
    InvalidCellTransitionError,
    UnknownBackendError,
)


def test_hive_error_is_rooted_at_hive_mind_error() -> None:
    assert issubclass(HiveError, HiveMindError)


def test_cell_provision_error_message_names_backend_image_and_reason() -> None:
    error = CellProvisionError("docker", "base-ubuntu", "no such image")

    assert issubclass(CellProvisionError, HiveError)
    assert error.backend_name == "docker"
    assert error.image == "base-ubuntu"
    assert "docker" in str(error)
    assert "base-ubuntu" in str(error)
    assert "no such image" in str(error)


def test_cell_destroy_error_message_names_backend_cell_and_reason() -> None:
    error = CellDestroyError("docker", "cell_01", "container still running")

    assert issubclass(CellDestroyError, HiveError)
    assert error.cell_id == "cell_01"
    assert "cell_01" in str(error)
    assert "container still running" in str(error)


def test_unknown_backend_error_is_a_configuration_error() -> None:
    error = UnknownBackendError("qemu", ("docker", "fake"))

    assert issubclass(UnknownBackendError, ConfigurationError)
    assert error.name == "qemu"
    assert error.known == ("docker", "fake")
    assert "qemu" in str(error)


def test_invalid_cell_transition_error_default_message() -> None:
    error = InvalidCellTransitionError(VirtualCellStatus.DESTROYED, VirtualCellStatus.READY)

    assert issubclass(InvalidCellTransitionError, ConflictError)
    assert "DESTROYED" in str(error)
    assert "READY" in str(error)


def test_invalid_cell_transition_error_custom_reason_overrides_default() -> None:
    error = InvalidCellTransitionError(
        VirtualCellStatus.RELEASED,
        VirtualCellStatus.DORMANT,
        cell_id="cell_01",
        reason="Night Veil Cells are teardown-only",
    )

    assert "cell_01" in str(error)
    assert "Night Veil Cells are teardown-only" in str(error)
    assert "no such edge" not in str(error)


def test_backend_capability_error_message_names_backend_and_capability() -> None:
    error = BackendCapabilityError("fake", "pause", cell_id="cell_01")

    assert issubclass(BackendCapabilityError, HiveError)
    assert error.capability == "pause"
    assert "fake" in str(error)
    assert "pause" in str(error)
    assert "cell_01" in str(error)


def test_every_error_class_has_its_own_stable_code() -> None:
    codes = {
        HiveError.code,
        CellProvisionError.code,
        CellDestroyError.code,
        UnknownBackendError.code,
        InvalidCellTransitionError.code,
        BackendCapabilityError.code,
    }

    assert len(codes) == 6  # No two error classes share a code (codingrules section 10).
