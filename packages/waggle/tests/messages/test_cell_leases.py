"""Tests for waggle.messages.cell_leases: the tenancy lifecycle's four messages.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). For CellTeardownRequest, CellRequest,
    LeaseOpened and LeaseReleased it pins construction in every form the spec allows, the JSON
    round trip, the rejection of an extra field, every bound, and every validator spec section
    8.5 names in both directions. The family's EXAMPLES live in test_cell.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.cell_leases for the module under test.
    - docs/waggle/spec.md section 8.5 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_PATH_CHARS, WaggleMessage
from waggle.messages.cell import IsolationNeed, ReleaseCause, TaskNeedsReport
from waggle.messages.cell_leases import (
    MAX_ALLOWED_PATHS,
    MAX_RESIDUAL_PATHS,
    CellRequest,
    CellTeardownRequest,
    LeaseOpened,
    LeaseReleased,
)
from waggle.messages.labels import AccuracyBar, CombShieldLevel, Tempo, Urgency

CLOCK = FakeClock()
CELL_ID = new_id(IdKind.CELL, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
LEASE_ID = new_id(IdKind.LEASE, CLOCK)
TASK_ID = new_id(IdKind.TASK, CLOCK)
NEEDS = TaskNeedsReport(
    isolation=IsolationNeed.NONE,
    needs_exoskeleton=False,
    os=None,
    network_scopes=(),
    is_disposable=True,
    comb_shield=CombShieldLevel.MEADOW,
    tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.LOW),
)
REQUESTABLE = (ReleaseCause.COMPLETED, ReleaseCause.CANCELLED, ReleaseCause.STING_CUT)
DISCOVERED = (ReleaseCause.DEAD_MAN, ReleaseCause.ORPHAN_SWEEP, ReleaseCause.HOLDER_LOST)


def _round_trips(message: WaggleMessage) -> bool:
    """Whether ``message`` survives model_dump(mode="json") and model_validate unchanged."""
    return type(message).model_validate(message.model_dump(mode="json")) == message


def _teardown(**overrides: object) -> CellTeardownRequest:
    """A graceful release of one completed lease, then ``overrides``."""
    fields: dict[str, object] = {
        "cell_id": CELL_ID,
        "lease_id": LEASE_ID,
        "urgency": "GRACEFUL",
        "cause": "COMPLETED",
        "reason": "The task is done.",
    }
    return CellTeardownRequest.model_validate({**fields, **overrides})


def _placement(**overrides: object) -> CellRequest:
    """The placement form: any Cell that fits NEEDS, no holder yet, then ``overrides``."""
    fields: dict[str, object] = {
        "cell_id": None,
        "needs": NEEDS,
        "holder": None,
        "task_id": TASK_ID,
        "access_level": "SCRATCH",
        "lifetime_s": 60.0,
        "reason": "A sandbox for a check.",
    }
    return CellRequest.model_validate({**fields, **overrides})


def _named(**overrides: object) -> CellRequest:
    """The named form: a lease on CELL_ID for WARDEN_ID, then ``overrides``."""
    return _placement(**{"cell_id": CELL_ID, "needs": None, "holder": WARDEN_ID, **overrides})


def _opened(**overrides: object) -> LeaseOpened:
    """A scratch-level lease on a MEADOW Cell with a Warden on it, then ``overrides``."""
    fields: dict[str, object] = {
        "lease_id": LEASE_ID,
        "cell_id": CELL_ID,
        "holder": WARDEN_ID,
        "task_id": None,
        "access_level": "SCRATCH",
        "comb_shield": "MEADOW",
        "scratch_root": "/home/operator/.hive/scratch/lease",
        "allowed_paths": (),
        "dead_man_s": None,
        "reason": "The Hive Stand itself.",
    }
    return LeaseOpened.model_validate({**fields, **overrides})


def _released(**overrides: object) -> LeaseReleased:
    """A clean release: restored, nothing killed, no residue, then ``overrides``."""
    fields: dict[str, object] = {
        "lease_id": LEASE_ID,
        "cell_id": CELL_ID,
        "holder": WARDEN_ID,
        "cause": "COMPLETED",
        "is_restored": True,
        "killed_processes": 0,
        "residual_paths": (),
        "reason": "Left as found.",
    }
    return LeaseReleased.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# CellTeardownRequest
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cause", REQUESTABLE, ids=lambda cause: cause.name)
def test_teardown_request_accepts_a_decided_cause_for_a_lease_or_a_whole_cell(
    cause: ReleaseCause,
) -> None:
    lease = _teardown(cause=cause.value)
    whole_cell = _teardown(cause=cause.value, lease_id=None, urgency="IMMEDIATE")

    assert lease.cause is cause
    assert _round_trips(lease)
    assert whole_cell.lease_id is None
    assert whole_cell.urgency is Urgency.IMMEDIATE
    assert _round_trips(whole_cell)


@pytest.mark.parametrize("cause", DISCOVERED, ids=lambda cause: cause.name)
def test_teardown_request_rejects_a_cause_only_a_releaser_can_establish(
    cause: ReleaseCause,
) -> None:
    with pytest.raises(ValidationError, match="only COMPLETED, CANCELLED or STING_CUT"):
        _teardown(cause=cause.value)


def test_teardown_request_checks_its_ids_and_extras() -> None:
    with pytest.raises(ValidationError, match="lease_"):
        _teardown(lease_id=CELL_ID)
    with pytest.raises(ValidationError, match="extra"):
        _teardown(deadline_s=5.0)


# ──────────────────────────────────────────────────────────────────────────────
# CellRequest
# ──────────────────────────────────────────────────────────────────────────────


def test_cell_request_takes_the_placement_form_or_the_named_form() -> None:
    placement = _placement()
    named = _named()
    # A named ask may still carry needs: only the placement form requires them.
    named_with_needs = _named(needs=NEEDS, lifetime_s=None, task_id=None)

    assert placement.cell_id is None and placement.needs == NEEDS
    assert named.cell_id == CELL_ID and named.holder == WARDEN_ID
    assert named_with_needs.lifetime_s is None
    assert all(_round_trips(request) for request in (placement, named, named_with_needs))


@pytest.mark.parametrize(
    ("builder", "changes", "reason"),
    [
        (_placement, {"needs": None}, "must state needs"),
        (_placement, {"holder": WARDEN_ID}, "holder is required exactly when cell_id is set"),
        (_named, {"holder": None}, "holder is required exactly when cell_id is set"),
        (_placement, {"lifetime_s": 0.0}, "greater than 0"),
        (_placement, {"task_id": CELL_ID}, "task_"),
        (_named, {"holder": TASK_ID}, "warden_"),
        (_placement, {"priority": 1}, "extra"),
    ],
)
def test_cell_request_validators(
    builder: Callable[..., CellRequest], changes: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        builder(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# LeaseOpened
# ──────────────────────────────────────────────────────────────────────────────


def test_lease_opened_constructs_with_or_without_a_dead_man_limit() -> None:
    with_warden = _opened()
    gateway_only = _opened(dead_man_s=300.0, allowed_paths=("/etc/hosts",), task_id=TASK_ID)

    assert with_warden.dead_man_s is None
    assert gateway_only.allowed_paths == ("/etc/hosts",)
    assert _round_trips(with_warden)
    assert _round_trips(gateway_only)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"scratch_root": ""}, "at least 1"),
        ({"scratch_root": "/" * (MAX_PATH_CHARS + 1)}, f"at most {MAX_PATH_CHARS}"),
        ({"allowed_paths": ("/x",) * (MAX_ALLOWED_PATHS + 1)}, f"at most {MAX_ALLOWED_PATHS}"),
        ({"allowed_paths": ("/" * (MAX_PATH_CHARS + 1),)}, f"at most {MAX_PATH_CHARS}"),
        ({"dead_man_s": 0.0}, "greater than 0"),
        ({"lease_id": CELL_ID}, "lease_"),
        ({"holder": CELL_ID}, "warden_"),
        ({"is_exclusive": True}, "extra"),
    ],
)
def test_lease_opened_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _opened(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# LeaseReleased
# ──────────────────────────────────────────────────────────────────────────────


def test_lease_released_lists_residual_paths_exactly_when_not_restored() -> None:
    clean = _released()
    dirty = _released(
        is_restored=False, residual_paths=("/etc/hosts",), killed_processes=3, cause="DEAD_MAN"
    )

    assert clean.residual_paths == ()
    assert dirty.cause is ReleaseCause.DEAD_MAN
    assert _round_trips(clean)
    assert _round_trips(dirty)
    with pytest.raises(ValidationError, match="empty exactly when is_restored"):
        _released(is_restored=True, residual_paths=("/etc/hosts",))
    with pytest.raises(ValidationError, match="empty exactly when is_restored"):
        _released(is_restored=False, residual_paths=())


@pytest.mark.parametrize("cause", ReleaseCause, ids=lambda cause: cause.name)
def test_lease_released_carries_every_cause(cause: ReleaseCause) -> None:
    assert _released(cause=cause.value).cause is cause


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"killed_processes": -1}, "greater than or equal to 0"),
        (
            {"is_restored": False, "residual_paths": ("/x",) * (MAX_RESIDUAL_PATHS + 1)},
            f"at most {MAX_RESIDUAL_PATHS}",
        ),
        (
            {"is_restored": False, "residual_paths": ("/" * (MAX_PATH_CHARS + 1),)},
            f"at most {MAX_PATH_CHARS}",
        ),
        ({"holder": LEASE_ID}, "warden_"),
        ({"duration_s": 1.0}, "extra"),
    ],
)
def test_lease_released_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _released(**changes)
