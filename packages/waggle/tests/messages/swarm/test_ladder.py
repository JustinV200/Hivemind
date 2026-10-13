"""Tests for waggle.messages.swarm.ladder: NodeUpdate, Colonize, Colonized and their enums.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins the two enums, and for the three classes
    construction, the JSON round trip, the bounds and the id kinds, and every validator spec
    section 8.10 names in both directions: NodeUpdate's tier never NIGHT_VEIL, Colonize's version
    and URL against its action, and Colonized's outcome-against-action matrix in full with its
    version and pid exactly where a step left something installed or running. The family's
    EXAMPLES live in test_enrolment.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.swarm.ladder for the module under test.
    - docs/waggle/spec.md section 8.10 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.swarm.enrolment import (
    MAX_CAPABILITY_CHARS,
    MAX_GRANTED_CAPABILITIES,
    MAX_RUNTIME_VERSION_CHARS,
    RuntimeLevel,
)
from waggle.messages.swarm.ladder import (
    MAX_WARDEN_URL_CHARS,
    MIN_WARDEN_PID,
    Colonize,
    ColonizeAction,
    Colonized,
    ColonizeOutcome,
    NodeUpdate,
)

CLOCK = FakeClock()
DEVICE_ID = new_id(IdKind.DEVICE, CLOCK)
CELL_ID = new_id(IdKind.CELL, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
# The one success outcome each step can report; FAILED answers any of them.
SUCCESS_FOR_ACTION = {
    ColonizeAction.PREPARE: ColonizeOutcome.PREPARED,
    ColonizeAction.START: ColonizeOutcome.STARTED,
    ColonizeAction.WITHDRAW: ColonizeOutcome.WITHDRAWN,
}


def _update(**overrides: object) -> NodeUpdate:
    """An update that raises a device to PROPOLIS on the colonized rung, then ``overrides``."""
    fields: dict[str, object] = {
        "device_id": DEVICE_ID,
        "cell_id": CELL_ID,
        "warden_id": WARDEN_ID,
        "level": RuntimeLevel.COLONIZED,
        "access_level": AccessLevel.SCRATCH,
        "comb_shield": CombShieldLevel.PROPOLIS,
        "granted_capabilities": ("exec:*", "fs:read:**"),
        "offline_limit_s": 600.0,
        "heartbeat_interval_s": 30.0,
        "reason": "The operator raised the tier to PROPOLIS.",
    }
    return NodeUpdate.model_validate({**fields, **overrides})


def _colonize(step: str = "START", **overrides: object) -> Colonize:
    """A START order naming the listener, or another step with its own fields, then overrides."""
    fields: dict[str, object] = {
        "cell_id": CELL_ID,
        "device_id": DEVICE_ID,
        "action": step,
        "warden_id": WARDEN_ID,
        "runtime_version": "0.4.2" if step == "PREPARE" else None,
        "warden_url": "wss://hive.example.org:8720/" if step == "START" else None,
        "deadline_s": 60.0,
        "reason": "The Hive Stand needs relief.",
    }
    return Colonize.model_validate({**fields, **overrides})


def _fitting_fields(outcome: ColonizeOutcome) -> dict[str, object]:
    """The runtime_version and warden_pid that fit ``outcome``."""
    has_version = outcome in (ColonizeOutcome.PREPARED, ColonizeOutcome.STARTED)
    return {
        "runtime_version": "0.4.2" if has_version else None,
        "warden_pid": 4242 if outcome is ColonizeOutcome.STARTED else None,
    }


def _colonized(**overrides: object) -> Colonized:
    """A successful START report, then ``overrides``."""
    fields: dict[str, object] = {
        "cell_id": CELL_ID,
        "device_id": DEVICE_ID,
        "action": ColonizeAction.START,
        "outcome": ColonizeOutcome.STARTED,
        "level": RuntimeLevel.COLONIZED,
        "runtime_version": "0.4.2",
        "warden_pid": 4242,
        "reason": "The Warden is running and dialled the swarm listener.",
    }
    return Colonized.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


def test_ladder_enums_list_exactly_the_spec_members_with_values_equal_to_names() -> None:
    assert [member.value for member in ColonizeAction] == ["PREPARE", "START", "WITHDRAW"]
    assert [member.value for member in ColonizeOutcome] == [
        "PREPARED",
        "STARTED",
        "WITHDRAWN",
        "FAILED",
    ]
    assert all(m.name == m.value for enum in (ColonizeAction, ColonizeOutcome) for m in enum)


# ──────────────────────────────────────────────────────────────────────────────
# NodeUpdate
# ──────────────────────────────────────────────────────────────────────────────


def test_node_update_round_trips_and_carries_every_level_and_access_level() -> None:
    update = _update()

    assert NodeUpdate.model_validate(update.model_dump(mode="json")) == update
    assert [_update(level=level).level for level in RuntimeLevel] == list(RuntimeLevel)
    assert [_update(access_level=level).access_level for level in AccessLevel] == list(AccessLevel)


def test_node_update_never_names_night_veil() -> None:
    for tier in (CombShieldLevel.MEADOW, CombShieldLevel.PROPOLIS):
        assert _update(comb_shield=tier).comb_shield is tier
    with pytest.raises(ValidationError, match="never NIGHT_VEIL"):
        _update(comb_shield="NIGHT_VEIL")


def test_node_update_accepts_the_capability_set_at_every_bound_and_none_at_all() -> None:
    full = _update(granted_capabilities=("c" * MAX_CAPABILITY_CHARS,) * MAX_GRANTED_CAPABILITIES)

    assert len(full.granted_capabilities) == MAX_GRANTED_CAPABILITIES
    # An empty set is a real update: a device that may now do nothing but be observed.
    assert _update(granted_capabilities=()).granted_capabilities == ()


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"granted_capabilities": ("c",) * (MAX_GRANTED_CAPABILITIES + 1)},
            f"at most {MAX_GRANTED_CAPABILITIES}",
        ),
        (
            {"granted_capabilities": ("c" * (MAX_CAPABILITY_CHARS + 1),)},
            f"at most {MAX_CAPABILITY_CHARS}",
        ),
        ({"offline_limit_s": 0}, "greater than 0"),
        ({"heartbeat_interval_s": 0}, "greater than 0"),
        ({"reason": "x" * (MAX_REASON_CHARS + 1)}, f"at most {MAX_REASON_CHARS}"),
        ({"level": "HIVE_STAND"}, "level"),
        ({"access_level": "ALL"}, "access_level"),
        ({"device_id": WARDEN_ID}, "device_"),
        ({"cell_id": DEVICE_ID}, "cell_"),
        ({"warden_id": DEVICE_ID}, "warden_"),
        ({"hive_key_hex": "ab" * 32}, "extra"),
    ],
)
def test_node_update_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _update(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# Colonize
# ──────────────────────────────────────────────────────────────────────────────


def test_colonize_round_trips_each_step_with_its_own_fields() -> None:
    steps = (
        _colonize("PREPARE"),
        _colonize("START"),
        _colonize("START", runtime_version="0.4.2"),
        _colonize("WITHDRAW"),
    )

    for step in steps:
        assert Colonize.model_validate(step.model_dump(mode="json")) == step
    assert [step.action for step in steps] == [
        ColonizeAction.PREPARE,
        ColonizeAction.START,
        ColonizeAction.START,
        ColonizeAction.WITHDRAW,
    ]


def test_colonize_accepts_every_bound() -> None:
    prepare = _colonize("PREPARE", runtime_version="v" * MAX_RUNTIME_VERSION_CHARS)
    start = _colonize("START", warden_url="wss://" + "h" * (MAX_WARDEN_URL_CHARS - len("wss://")))

    assert len(prepare.runtime_version or "") == MAX_RUNTIME_VERSION_CHARS
    assert len(start.warden_url or "") == MAX_WARDEN_URL_CHARS
    # A loopback listener is dialled over plain ws://, which the URL shape allows.
    assert _colonize("START", warden_url="ws://127.0.0.1:8720").warden_url == "ws://127.0.0.1:8720"


@pytest.mark.parametrize(
    ("action", "changes", "reason"),
    [
        # PREPARE installs a version; without one it installs nothing.
        (
            "PREPARE",
            {"runtime_version": None},
            "runtime_version is required when action is PREPARE",
        ),
        # START needs the URL the Warden dials, and no other step may carry one.
        ("START", {"warden_url": None}, "warden_url is required exactly when action is START"),
        ("PREPARE", {"warden_url": "wss://hive.example.org/"}, "warden_url is required exactly"),
        ("WITHDRAW", {"warden_url": "wss://hive.example.org/"}, "warden_url is required exactly"),
        # WITHDRAW names nothing to install.
        (
            "WITHDRAW",
            {"runtime_version": "0.4.2"},
            "runtime_version must be None when action is WITHDRAW",
        ),
        # Bounds and shapes.
        (
            "PREPARE",
            {"runtime_version": "v" * (MAX_RUNTIME_VERSION_CHARS + 1)},
            f"at most {MAX_RUNTIME_VERSION_CHARS}",
        ),
        ("PREPARE", {"runtime_version": ""}, "at least 1"),
        ("START", {"warden_url": "https://hive.example.org/"}, "pattern"),
        ("START", {"warden_url": "wss://hive example.org/"}, "pattern"),
        (
            "START",
            {"warden_url": "wss://" + "h" * MAX_WARDEN_URL_CHARS},
            f"at most {MAX_WARDEN_URL_CHARS}",
        ),
        ("START", {"deadline_s": 0}, "greater than 0"),
        ("START", {"reason": "x" * (MAX_REASON_CHARS + 1)}, f"at most {MAX_REASON_CHARS}"),
        ("START", {"action": "RESTART"}, "action"),
        ("START", {"cell_id": DEVICE_ID}, "cell_"),
        ("START", {"device_id": CELL_ID}, "device_"),
        ("START", {"warden_id": DEVICE_ID}, "warden_"),
        ("START", {"command": ["sh"]}, "extra"),
    ],
)
def test_colonize_validators(action: str, changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _colonize(action, **changes)


# ──────────────────────────────────────────────────────────────────────────────
# Colonized
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", list(ColonizeOutcome))
@pytest.mark.parametrize("action", list(ColonizeAction))
def test_colonized_outcome_is_the_step_s_success_or_failed(
    action: ColonizeAction, outcome: ColonizeOutcome
) -> None:
    fits = outcome is ColonizeOutcome.FAILED or outcome is SUCCESS_FOR_ACTION[action]
    fields = {"action": action, "outcome": outcome, **_fitting_fields(outcome)}

    if fits:
        assert _colonized(**fields).outcome is outcome
    else:
        with pytest.raises(ValidationError, match="cannot answer action"):
            _colonized(**fields)


def test_colonized_round_trips_every_level_and_each_consistent_shape() -> None:
    for level in RuntimeLevel:
        assert _colonized(level=level).level is level
    shapes = (
        _colonized(),
        _colonized(action="PREPARE", outcome="PREPARED", level="GATEWAY", warden_pid=None),
        _colonized(
            action="WITHDRAW",
            outcome="WITHDRAWN",
            level="GATEWAY",
            runtime_version=None,
            warden_pid=None,
        ),
        _colonized(outcome="FAILED", level="GATEWAY", runtime_version=None, warden_pid=None),
    )

    for shape in shapes:
        assert Colonized.model_validate(shape.model_dump(mode="json")) == shape


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        # Something installed or running must say which version.
        ({"runtime_version": None}, "runtime_version is set exactly when outcome is PREPARED"),
        (
            {"outcome": "FAILED", "warden_pid": None},
            "runtime_version is set exactly when outcome is PREPARED or STARTED",
        ),
        (
            {"action": "WITHDRAW", "outcome": "WITHDRAWN", "warden_pid": None},
            "runtime_version is set exactly when outcome is PREPARED or STARTED",
        ),
        # A pid exists exactly when a Warden was started.
        ({"warden_pid": None}, "warden_pid is set exactly when outcome is STARTED"),
        (
            {"action": "PREPARE", "outcome": "PREPARED"},
            "warden_pid is set exactly when outcome is STARTED",
        ),
        ({"warden_pid": 0}, f"greater than or equal to {MIN_WARDEN_PID}"),
        ({"runtime_version": "v" * (MAX_RUNTIME_VERSION_CHARS + 1)}, "at most"),
        ({"reason": "x" * (MAX_REASON_CHARS + 1)}, f"at most {MAX_REASON_CHARS}"),
        ({"level": "HIVE_STAND"}, "level"),
        ({"cell_id": DEVICE_ID}, "cell_"),
        ({"device_id": CELL_ID}, "device_"),
        ({"stdout": "ok"}, "extra"),
    ],
)
def test_colonized_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _colonized(**changes)
