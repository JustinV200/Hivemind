"""Tests for hivemind.swarm.errors: the failures the Swarm raises on purpose, and what each says.

Fits into the Hive:
    Mirrors src/hivemind/swarm/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Every error has a stable `code` a caller or a device sees, a place in the shared
    taxonomy (so code that already handles a failed Cell operation handles a lost device too) and
    a message that names ids and never a secret.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.errors for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.cell.errors import CellError, LeaseRefusedError
from hivemind.common.errors import (
    ConflictError,
    DeadlineExceededError,
    NotFoundError,
    PermissionDeniedError,
)
from hivemind.swarm import errors
from hivemind.swarm.errors import (
    CommandDeniedError,
    DeviceRequestError,
    DeviceTimeoutError,
    EnrolmentDeniedError,
    EnrolRefusal,
    GatewayUnavailableError,
    InvalidNodeTransitionError,
    InviteConflictError,
    InviteFormatError,
    InviteNotFoundError,
    InviteRefusedError,
    LadderError,
    NodeNotFoundError,
    NodeRevokedError,
    NodeStatusConflictError,
    ShieldUnverifiedError,
    SwarmError,
    TransferIntegrityError,
)
from hivemind.swarm.level import RuntimeLevel
from hivemind.swarm.node_state import NodeStatus

_ALL = [
    getattr(errors, name)
    for name in errors.__all__
    if isinstance(getattr(errors, name), type) and issubclass(getattr(errors, name), SwarmError)
]


def test_every_swarm_error_has_a_code_of_its_own_under_the_swarm_namespace() -> None:
    codes = [error.code for error in _ALL]

    assert len(codes) == len(set(codes)) and len(_ALL) == 18
    assert all(code.startswith("hive.swarm.") for code in codes)


@pytest.mark.parametrize(
    ("error", "parents"),
    [
        (EnrolmentDeniedError, (PermissionDeniedError,)),
        (InviteRefusedError, (PermissionDeniedError,)),
        (InviteFormatError, (ValueError,)),
        (InviteNotFoundError, (NotFoundError,)),
        (InviteConflictError, (ConflictError,)),
        (NodeNotFoundError, (NotFoundError,)),
        (NodeRevokedError, (PermissionDeniedError,)),
        (NodeStatusConflictError, (ConflictError,)),
        (InvalidNodeTransitionError, (ConflictError,)),
        (LadderError, (ConflictError,)),
        (GatewayUnavailableError, (CellError,)),
        (DeviceTimeoutError, (CellError, DeadlineExceededError)),
        (DeviceRequestError, (CellError,)),
        (TransferIntegrityError, (CellError,)),
        (CommandDeniedError, (PermissionDeniedError,)),
        (ShieldUnverifiedError, (LeaseRefusedError,)),
    ],
)
def test_each_error_sits_in_the_taxonomy_code_that_handles_its_kind_already_expects(
    error: type[SwarmError], parents: tuple[type[Exception], ...]
) -> None:
    assert issubclass(error, SwarmError)
    assert all(issubclass(error, parent) for parent in parents)


def test_a_refusal_has_a_closed_set_of_reason_codes_a_trail_can_hold() -> None:
    assert {reason.value for reason in EnrolRefusal} == {
        "unknown_invite",
        "expired",
        "revoked",
        "used",
        "identity_mismatch",
        "identity_in_use",
        "wrong_hive",
        "not_an_enrolment",
    }


def test_a_denied_enrolment_names_the_ids_and_the_reason_and_says_nothing_was_created() -> None:
    error = EnrolmentDeniedError(EnrolRefusal.USED, "device_x", "node_y")

    assert (error.reason, error.device_id, error.node_id) == (
        EnrolRefusal.USED,
        "device_x",
        "node_y",
    )
    assert "device_x" in str(error) and "node_y" in str(error) and "used" in str(error)
    assert "Nothing was created" in str(error)


def test_unknown_things_are_named_by_the_id_they_were_asked_by() -> None:
    assert "abcdef123456" in str(InviteNotFoundError("abcdef1234567890"))
    assert "abcdef1234567890" not in str(InviteNotFoundError("abcdef1234567890"))  # Truncated.
    assert NodeNotFoundError("device_x").device_id == "device_x"
    assert "device_x" in str(NodeRevokedError("device_x"))


def test_a_status_conflict_says_what_was_expected_and_what_was_found() -> None:
    error = NodeStatusConflictError("device_x", NodeStatus.ONLINE, NodeStatus.REVOKED)

    assert (error.expected, error.found) == (NodeStatus.ONLINE, NodeStatus.REVOKED)
    assert "REVOKED" in str(error) and "ONLINE" in str(error)


@pytest.mark.parametrize("device_id", [None, "device_x"])
def test_a_forbidden_edge_names_both_ends_and_the_device_when_known(device_id: str | None) -> None:
    status = InvalidNodeTransitionError(NodeStatus.REVOKED, NodeStatus.ONLINE, device_id)
    rung = LadderError(RuntimeLevel.GATEWAY, RuntimeLevel.NUC, device_id)

    assert "REVOKED" in str(status) and "ONLINE" in str(status)
    assert "GATEWAY" in str(rung) and "NUC" in str(rung)
    assert ("device_x" in str(status)) is (device_id is not None)
    assert (status.device_id, rung.device_id) == (device_id, device_id)


def test_a_device_refusal_keeps_the_devices_code_verbatim_and_whether_to_retry() -> None:
    error = DeviceRequestError("pollen.not_found", "no such lease", is_retryable=True)

    assert error.device_code == "pollen.not_found" and error.device_message == "no such lease"
    assert error.is_retryable is True and "pollen.not_found" in str(error)
    assert DeviceRequestError("pollen.x", "m").is_retryable is False


def test_a_denied_command_names_the_lease_and_what_it_needed_but_not_what_it_ran() -> None:
    error = CommandDeniedError("lease_x", "exec:ls")

    assert (error.lease_id, error.needed) == ("lease_x", "exec:ls")
    assert "lease_x" in str(error) and "exec:ls" in str(error)
    assert "Nothing was sent" in str(error)


@pytest.mark.parametrize(
    ("failed", "named"),
    [(["default_route", "dns"], "default_route, dns"), ([], "none ran")],
)
def test_an_unverified_shield_lists_the_checks_that_failed(failed: list[str], named: str) -> None:
    error = ShieldUnverifiedError("cell_x", failed)

    assert error.failed_checks == tuple(failed) and named in str(error)
    assert error.cell_id == "cell_x" and "released" in str(error)
