"""Tests for the cell family: EXAMPLES for all nine classes, plus the cell.status module itself.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    cell message class, which the registry step later loads by file path to seed its
    per-family checks; pins for every class the JSON round trip and the rejection of an extra
    field; and for waggle.messages.cell.status (CellReady, CellHeartbeat, AttestationCheck,
    TaskNeedsReport and the family's non-wax enums) every bound and validator spec section 8.5
    names. The lease messages are tested in test_leases.py and the wax messages in
    test_wax.py.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the nine cell message classes.

See Also:
    - waggle.messages.cell.status, leases and wax for the modules under test.
    - docs/waggle/spec.md section 8.5 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from datetime import timedelta
from enum import Enum

import pytest
from pydantic import BaseModel, ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_REASON_CHARS, WaggleMessage
from waggle.messages.cell.leases import CellRequest, CellTeardownRequest, LeaseOpened, LeaseReleased
from waggle.messages.cell.status import (
    MAX_ATTESTATION_CHECKS,
    MAX_CHECK_DETAIL_CHARS,
    MAX_CHECK_NAME_CHARS,
    MAX_LEASE_IDS,
    MAX_RUNTIME_VERSION_CHARS,
    AttestationCheck,
    CellHeartbeat,
    CellMode,
    CellReady,
    IsolationNeed,
    ReleaseCause,
    TaskNeedsReport,
)
from waggle.messages.cell.wax import (
    CellWaxCleared,
    CellWaxProposed,
    CellWaxWritten,
    WaxClearCause,
    WaxDecision,
    WaxOrigin,
    WaxSeverity,
)
from waggle.messages.labels import (
    AccessLevel,
    AccuracyBar,
    CombShieldLevel,
    HoneyClearance,
    OsFamily,
    Tempo,
    Urgency,
)
from waggle.messages.reports import (
    MAX_NETWORK_SCOPE_CHARS,
    MAX_NETWORK_SCOPES,
    CellCapabilitiesReport,
    PlatformReport,
)

CLOCK = FakeClock()
NOW = CLOCK.now()
LATER = NOW + timedelta(days=7)
CELL_ID = new_id(IdKind.CELL, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
WORKER_ID = new_id(IdKind.WORKER, CLOCK)
LEASE_ID = new_id(IdKind.LEASE, CLOCK)
TASK_ID = new_id(IdKind.TASK, CLOCK)
WAX_ID = "wax_01ARZ3NDEKTSV4RRFFQ69G5FAV"  # A wax_ prefix on a well-formed Crockford ULID.
CELL_CLASSES: tuple[type[WaggleMessage], ...] = (
    CellReady,
    CellHeartbeat,
    CellTeardownRequest,
    CellRequest,
    LeaseOpened,
    LeaseReleased,
    CellWaxProposed,
    CellWaxWritten,
    CellWaxCleared,
)

# Member names in declaration order, per spec section 8.5; every value equals its name.
_MEMBERS: list[tuple[type[Enum], list[str]]] = [
    (CellMode, ["ACTIVE", "IDLE", "WATCH"]),
    (IsolationNeed, ["REQUIRED", "PREFERRED", "NONE"]),
    (
        ReleaseCause,
        ["COMPLETED", "CANCELLED", "STING_CUT", "DEAD_MAN", "ORPHAN_SWEEP", "HOLDER_LOST"],
    ),
    (WaxSeverity, ["NOTE", "CAUTION", "BLOCK"]),
    (WaxOrigin, ["BEE", "PATROL", "HUMAN"]),
    (WaxDecision, ["AUTOPILOT", "AWAKE"]),
    (WaxClearCause, ["CLEARED", "EXPIRED", "CELL_RETIRED"]),
]

# The value models the examples are built from: a Linux Cell with a display, one passed check.
PLATFORM = PlatformReport.model_validate(
    {
        "os": "LINUX",
        "distribution": "Ubuntu 24.04",
        "architecture": "x86_64",
        "package_manager": "apt",
        "shell": "/bin/bash",
        "python_version": "3.12.14",
    }
)
CAPABILITIES = CellCapabilitiesReport(
    has_display=True,
    has_audio=False,
    has_browser=True,
    can_start_display=True,
    can_host_model=False,
    network_scopes=("net:internet",),
)
CHECK = AttestationCheck(name="vpn_up", has_passed=True, detail="Tunnel handshake completed.")
NEEDS = TaskNeedsReport(
    isolation=IsolationNeed.PREFERRED,
    needs_exoskeleton=False,
    os=OsFamily.LINUX,
    network_scopes=("net:internet",),
    is_disposable=True,
    comb_shield=CombShieldLevel.MEADOW,
    tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
)

# One valid instance of every cell class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    CellReady(
        cell_id=CELL_ID,
        warden_id=WARDEN_ID,
        platform=PLATFORM,
        capabilities=CAPABILITIES,
        access_level=AccessLevel.FULL,
        comb_shield=CombShieldLevel.PROPOLIS,
        attestation=(CHECK,),
        runtime_version="0.1.0.dev0",
    ),
    CellHeartbeat(
        cell_id=CELL_ID,
        mode=CellMode.ACTIVE,
        lease_ids=(LEASE_ID,),
        worker_count=2,
        is_shield_verified=True,
        interval_s=15.0,
    ),
    CellTeardownRequest(
        cell_id=CELL_ID,
        lease_id=LEASE_ID,
        urgency=Urgency.GRACEFUL,
        cause=ReleaseCause.COMPLETED,
        reason="The task's result was accepted.",
    ),
    CellRequest(
        cell_id=None,
        needs=NEEDS,
        holder=None,
        task_id=TASK_ID,
        access_level=AccessLevel.SCRATCH,
        lifetime_s=3_600.0,
        reason="A browser task needs a display.",
    ),
    LeaseOpened(
        lease_id=LEASE_ID,
        cell_id=CELL_ID,
        holder=WARDEN_ID,
        task_id=TASK_ID,
        access_level=AccessLevel.SCRATCH,
        comb_shield=CombShieldLevel.PROPOLIS,
        scratch_root="/home/operator/.hive/scratch/lease",
        allowed_paths=("/home/operator/projects/site",),
        dead_man_s=600.0,
        reason="Only this Cell has a display; no wax weighs on it.",
    ),
    LeaseReleased(
        lease_id=LEASE_ID,
        cell_id=CELL_ID,
        holder=WARDEN_ID,
        cause=ReleaseCause.COMPLETED,
        is_restored=True,
        killed_processes=0,
        residual_paths=(),
        reason="Scratch removed; every touched path restored.",
    ),
    CellWaxProposed(
        cell_id=CELL_ID,
        severity=WaxSeverity.CAUTION,
        text="The package manager needs a password prompt every run.",
        reason="Three installs stalled on it.",
        clearance=HoneyClearance.C2,
        expires_at=LATER,
        origin=WaxOrigin.BEE,
        proposer=WORKER_ID,
        task_id=TASK_ID,
    ),
    CellWaxWritten(
        wax_id=WAX_ID,
        cell_id=CELL_ID,
        severity=WaxSeverity.CAUTION,
        text="The package manager needs a password prompt every run.",
        reason="Confirmed by the Patrol.",
        clearance=HoneyClearance.C2,
        expires_at=LATER,
        origin=WaxOrigin.BEE,
        proposer=WORKER_ID,
        decided_by=WaxDecision.AUTOPILOT,
    ),
    CellWaxCleared(
        wax_id=WAX_ID,
        cell_id=CELL_ID,
        cause=WaxClearCause.EXPIRED,
        reason="Expired on the nightly sweep.",
    ),
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


def _example(message_type: type[WaggleMessage]) -> WaggleMessage:
    """The EXAMPLES entry of ``message_type``."""
    return next(example for example in EXAMPLES if type(example) is message_type)


def _round_trips(model: BaseModel) -> bool:
    """Whether ``model`` survives model_dump(mode="json") and model_validate unchanged."""
    return type(model).model_validate(model.model_dump(mode="json")) == model


def _check(**overrides: object) -> AttestationCheck:
    """The passed check, then ``overrides``."""
    return AttestationCheck.model_validate({**CHECK.model_dump(), **overrides})


def _needs(**overrides: object) -> TaskNeedsReport:
    """The example needs, then ``overrides``."""
    return TaskNeedsReport.model_validate({**NEEDS.model_dump(), **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# Every class
# ──────────────────────────────────────────────────────────────────────────────


def test_examples_hold_exactly_one_instance_of_every_cell_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == CELL_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_cell_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.cell_id = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_cell_message_rejects_an_extra_field(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)


@pytest.mark.parametrize(
    "example",
    [example for example in EXAMPLES if not isinstance(example, CellReady | CellHeartbeat)],
    ids=lambda example: type(example).__name__,
)
def test_reason_is_bounded_by_the_shared_limit(example: WaggleMessage) -> None:
    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_cell_id_rejects_an_id_of_another_kind(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="cell_"):
        _rebuild(example, cell_id=new_id(IdKind.NODE, CLOCK))


# ──────────────────────────────────────────────────────────────────────────────
# Enums, AttestationCheck, TaskNeedsReport
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("enum_type", "names"), _MEMBERS)
def test_enum_has_exactly_the_spec_members_with_values_equal_to_names(
    enum_type: type[Enum], names: list[str]
) -> None:
    assert [member.name for member in enum_type] == names
    assert [member.value for member in enum_type] == names


def test_attestation_check_constructs_round_trips_and_bounds() -> None:
    assert _round_trips(_check())
    assert _round_trips(_check(has_passed=False, detail=""))
    with pytest.raises(ValidationError, match=f"at most {MAX_CHECK_NAME_CHARS}"):
        _check(name="x" * (MAX_CHECK_NAME_CHARS + 1))
    with pytest.raises(ValidationError, match=f"at most {MAX_CHECK_DETAIL_CHARS}"):
        _check(detail="x" * (MAX_CHECK_DETAIL_CHARS + 1))
    with pytest.raises(ValidationError, match="extra"):
        _check(duration_s=1.0)


def test_task_needs_report_constructs_round_trips_and_bounds() -> None:
    assert _round_trips(_needs())
    assert _round_trips(_needs(os=None, network_scopes=()))
    with pytest.raises(ValidationError, match=f"at most {MAX_NETWORK_SCOPES}"):
        _needs(network_scopes=("net:internet",) * (MAX_NETWORK_SCOPES + 1))
    with pytest.raises(ValidationError, match=f"at most {MAX_NETWORK_SCOPE_CHARS}"):
        _needs(network_scopes=("x" * (MAX_NETWORK_SCOPE_CHARS + 1),))
    with pytest.raises(ValidationError, match="extra"):
        _needs(gpu=True)


# ──────────────────────────────────────────────────────────────────────────────
# CellReady
# ──────────────────────────────────────────────────────────────────────────────


def test_cell_ready_attestation_is_empty_exactly_for_meadow() -> None:
    example = _example(CellReady)
    meadow = _rebuild(example, comb_shield="MEADOW", attestation=())
    # A failed check on a NIGHT_VEIL Cell is accepted here: never scheduling it is the Queen's
    # rule, and the trail must still record the report.
    failed = _rebuild(example, comb_shield="NIGHT_VEIL", attestation=(_check(has_passed=False),))

    assert isinstance(meadow, CellReady)
    assert meadow.attestation == ()
    assert isinstance(failed, CellReady)
    assert failed.attestation[0].has_passed is False
    with pytest.raises(ValidationError, match="non-empty exactly when comb_shield is not MEADOW"):
        _rebuild(example, comb_shield="MEADOW", attestation=(CHECK,))
    with pytest.raises(ValidationError, match="non-empty exactly when comb_shield is not MEADOW"):
        _rebuild(example, comb_shield="PROPOLIS", attestation=())


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"attestation": (CHECK,) * (MAX_ATTESTATION_CHECKS + 1)},
            f"at most {MAX_ATTESTATION_CHECKS}",
        ),
        (
            {"runtime_version": "x" * (MAX_RUNTIME_VERSION_CHARS + 1)},
            f"at most {MAX_RUNTIME_VERSION_CHARS}",
        ),
        ({"warden_id": "worker_01ARZ3NDEKTSV4RRFFQ69G5FAV"}, "warden_"),
        ({"access_level": "ROOT"}, "access_level"),
    ],
)
def test_cell_ready_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(CellReady), **changes)


# ──────────────────────────────────────────────────────────────────────────────
# CellHeartbeat
# ──────────────────────────────────────────────────────────────────────────────


def test_cell_heartbeat_carries_every_mode_and_no_leases_when_idle() -> None:
    for mode in CellMode:
        beat = _rebuild(_example(CellHeartbeat), mode=mode.value, lease_ids=(), worker_count=0)
        assert isinstance(beat, CellHeartbeat)
        assert beat.mode is mode
        assert beat.model_dump(mode="json")["lease_ids"] == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"lease_ids": (LEASE_ID,) * (MAX_LEASE_IDS + 1)}, f"at most {MAX_LEASE_IDS}"),
        ({"lease_ids": (CELL_ID,)}, "lease_"),
        ({"worker_count": -1}, "greater than or equal to 0"),
        ({"interval_s": 0.0}, "greater than 0"),
    ],
)
def test_cell_heartbeat_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(CellHeartbeat), **changes)
