"""Tests for the swarm family: EXAMPLES of all six classes, and waggle.messages.swarm.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    swarm class across the family's two modules, which the registry step later loads by file
    path to seed its per-family checks; pins for every class construction, the JSON round trip
    (bytes included), the rejection of an extra field and the shared reason bound; then the
    three enums, NodeKey and every rule spec section 8.10 names for EnrolRequest, EnrolAccept
    and DeviceHeartbeat. The other three classes' own rules are pinned in
    test_swarm_colonized.py.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the six swarm classes.

See Also:
    - waggle.messages.swarm and waggle.messages.swarm_colonized for the modules under test.
    - docs/waggle/spec.md section 8.10 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_REASON_CHARS, MAX_SUB_BEES_ON_WIRE, WaggleMessage
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.reports import CellCapabilitiesReport, HostCapacityReport, PlatformReport
from waggle.messages.swarm import (
    MAX_CAPABILITY_CHARS,
    MAX_GRANTED_CAPABILITIES,
    MAX_HEARTBEAT_LEASES,
    MAX_HOSTNAME_CHARS,
    MAX_INVITE_TOKEN_CHARS,
    MAX_RUNTIME_VERSION_CHARS,
    MAX_TRUSTED_NODES,
    MIN_INVITE_TOKEN_CHARS,
    DeviceHeartbeat,
    EnrolAccept,
    EnrolRequest,
    NodeKey,
    NucAction,
    NucOutcome,
    RuntimeLevel,
)
from waggle.messages.swarm_colonized import NucPromote, NucPromoted, TrailSegmentSync

CLOCK = FakeClock()
NOW = CLOCK.now()
LATER = NOW + timedelta(hours=1)
KEY_HEX = "ab" * 32  # 32 bytes of public key as 64 lowercase hex characters.
DIGEST = "0123456789abcdef" * 4  # 32 bytes of digest as 64 lowercase hex characters.
INVITE_TOKEN = "invite-0123456789abcdef0123456789"  # noqa: S105  # A fixture, not a credential.
DEVICE_ID = new_id(IdKind.DEVICE, CLOCK)
NODE_ID = new_id(IdKind.NODE, CLOCK)
CELL_ID = new_id(IdKind.CELL, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
GIB = 1_073_741_824  # One gibibyte, so the capacity figures read as a real host.
# Built with model_validate: a shell= keyword would trip the subprocess security lint (S604).
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
    can_start_display=False,
    can_host_model=True,
    network_scopes=("net:internet",),
)
CAPACITY = HostCapacityReport(
    cores=8,
    memory_bytes=32 * GIB,
    memory_free_bytes=16 * GIB,
    disk_bytes=512 * GIB,
    disk_free_bytes=100 * GIB,
    cpu_load=0.25,
    gpus=(),
)
SWARM_CLASSES: tuple[type[WaggleMessage], ...] = (
    EnrolRequest,
    EnrolAccept,
    DeviceHeartbeat,
    NucPromote,
    NucPromoted,
    TrailSegmentSync,
)


def _node_keys(count: int) -> tuple[dict[str, str], ...]:
    """``count`` NodeKey dicts with distinct node ids and the same key."""
    return tuple(
        {"node_id": new_id(IdKind.NODE, CLOCK), "public_key_hex": KEY_HEX} for _ in range(count)
    )


def _lease_ids(count: int) -> tuple[str, ...]:
    """``count`` distinct lease ids."""
    return tuple(new_id(IdKind.LEASE, CLOCK) for _ in range(count))


# One valid instance of every swarm class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    EnrolRequest(
        invite_token=INVITE_TOKEN,
        device_id=DEVICE_ID,
        node_id=NODE_ID,
        public_key_hex=KEY_HEX,
        hostname="laptop.local",
        runtime_version="0.1.0",
        platform=PLATFORM,
        capabilities=CAPABILITIES,
        capacity=CAPACITY,
        max_sub_bees=4,
    ),
    EnrolAccept(
        device_id=DEVICE_ID,
        cell_id=CELL_ID,
        hive_id=new_id(IdKind.HIVE, CLOCK),
        hive_key_hex=KEY_HEX,
        trusted_nodes=(NodeKey(node_id=new_id(IdKind.NODE, CLOCK), public_key_hex=KEY_HEX),),
        warden_id=WARDEN_ID,
        access_level=AccessLevel.SCRATCH,
        comb_shield=CombShieldLevel.MEADOW,
        granted_capabilities=("session:exec", "net:internet"),
        offline_limit_s=600.0,
        heartbeat_interval_s=30.0,
        reason="The operator approved the device on loopback at SCRATCH.",
    ),
    DeviceHeartbeat(
        device_id=DEVICE_ID,
        level=RuntimeLevel.GATEWAY,
        lease_ids=_lease_ids(1),
        hive_started_processes=1,
        outbox_pending=0,
        uptime_s=3600.0,
    ),
    NucPromote(
        cell_id=CELL_ID,
        device_id=DEVICE_ID,
        action=NucAction.PROMOTE,
        server_id="laptop_local_server",
        initial_models=("coder-7b",),
        deadline_s=120.0,
        reason="The Cell has a free GPU and the hosting plan allows a local server.",
    ),
    NucPromoted(
        cell_id=CELL_ID,
        device_id=DEVICE_ID,
        action=NucAction.PROMOTE,
        outcome=NucOutcome.PROMOTED,
        level=RuntimeLevel.NUC,
        server_id="laptop_local_server",
        loaded_models=("coder-7b",),
        reason="The server answered its health probe after 40 seconds.",
    ),
    TrailSegmentSync(
        node_id=NODE_ID,
        cell_id=CELL_ID,
        warden_id=WARDEN_ID,
        from_at=NOW,
        to_at=LATER,
        first_event_id=new_id(IdKind.EVENT, CLOCK),
        last_event_id=new_id(IdKind.EVENT, CLOCK),
        event_count=12,
        segment_format_version=1,
        chunk=b"{}\n",
        offset=0,
        total_bytes=3,
        final=True,
        sha256=DIGEST,
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


def test_examples_hold_exactly_one_instance_of_every_swarm_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == SWARM_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_swarm_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.reason = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_swarm_message_rejects_an_extra_field_and_a_foreign_id(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)
    # Every swarm class names the device or its Cell; a Warden id in that slot is refused.
    field = "device_id" if "device_id" in type(example).model_fields else "cell_id"
    with pytest.raises(ValidationError, match=field.removesuffix("id")):
        _rebuild(example, **{field: WARDEN_ID})


@pytest.mark.parametrize(
    "example", [_example(EnrolAccept), _example(NucPromote), _example(NucPromoted)]
)
def test_reason_is_bounded_by_the_shared_limit(example: WaggleMessage) -> None:
    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


# ──────────────────────────────────────────────────────────────────────────────
# Enums and NodeKey
# ──────────────────────────────────────────────────────────────────────────────


def test_swarm_enums_list_exactly_the_spec_members() -> None:
    assert [member.value for member in RuntimeLevel] == ["GATEWAY", "COLONIZED", "NUC"]
    assert [member.value for member in NucAction] == ["PROMOTE", "DEMOTE"]
    assert [member.value for member in NucOutcome] == ["PROMOTED", "DEMOTED", "FAILED"]
    assert all(m.name == m.value for enum in (RuntimeLevel, NucAction, NucOutcome) for m in enum)


def test_node_key_round_trips_and_rejects_a_malformed_key_or_node() -> None:
    key = NodeKey(node_id=NODE_ID, public_key_hex=KEY_HEX)

    assert NodeKey.model_validate(key.model_dump(mode="json")) == key
    for bad_hex in (KEY_HEX.upper(), KEY_HEX[:-2], KEY_HEX + "ab", "zz" * 32):
        with pytest.raises(ValidationError, match="pattern"):
            NodeKey(node_id=NODE_ID, public_key_hex=bad_hex)
    with pytest.raises(ValidationError, match="node_"):
        NodeKey(node_id=DEVICE_ID, public_key_hex=KEY_HEX)
    with pytest.raises(ValidationError, match="extra"):
        NodeKey.model_validate({**_node_keys(1)[0], "algorithm": "ed25519"})


# ──────────────────────────────────────────────────────────────────────────────
# EnrolRequest
# ──────────────────────────────────────────────────────────────────────────────


def test_enrol_request_hides_the_token_from_the_repr_and_defaults_to_full_access() -> None:
    request = _example(EnrolRequest)

    assert isinstance(request, EnrolRequest)
    assert INVITE_TOKEN not in repr(request)
    assert "hostname" in repr(request)
    assert request.requested_access is AccessLevel.FULL
    assert request.model_dump(mode="json")["requested_access"] == "FULL"


def test_enrol_request_accepts_every_bound_and_no_sub_bee_cap() -> None:
    at_bounds = _rebuild(
        _example(EnrolRequest),
        invite_token="t" * MAX_INVITE_TOKEN_CHARS,
        hostname="h" * MAX_HOSTNAME_CHARS,
        runtime_version="v" * MAX_RUNTIME_VERSION_CHARS,
        max_sub_bees=MAX_SUB_BEES_ON_WIRE,
        requested_access="READ_ONLY",
    )

    assert isinstance(at_bounds, EnrolRequest)
    assert at_bounds.requested_access is AccessLevel.READ_ONLY
    assert _rebuild(_example(EnrolRequest), max_sub_bees=None).model_dump()["max_sub_bees"] is None
    assert _rebuild(_example(EnrolRequest), invite_token="t" * MIN_INVITE_TOKEN_CHARS)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"invite_token": "short-token"}, f"at least {MIN_INVITE_TOKEN_CHARS}"),
        ({"invite_token": "t" * (MAX_INVITE_TOKEN_CHARS + 1)}, f"at most {MAX_INVITE_TOKEN_CHARS}"),
        ({"public_key_hex": KEY_HEX.upper()}, "pattern"),
        ({"public_key_hex": KEY_HEX[:-1]}, "pattern"),
        ({"hostname": "h" * (MAX_HOSTNAME_CHARS + 1)}, f"at most {MAX_HOSTNAME_CHARS}"),
        (
            {"runtime_version": "v" * (MAX_RUNTIME_VERSION_CHARS + 1)},
            f"at most {MAX_RUNTIME_VERSION_CHARS}",
        ),
        ({"max_sub_bees": -1}, "greater than or equal to 0"),
        ({"max_sub_bees": MAX_SUB_BEES_ON_WIRE + 1}, "less than or equal to"),
        ({"requested_access": "ALL"}, "requested_access"),
        ({"node_id": DEVICE_ID}, "node_"),
        ({"platform": {"os": "LINUX"}}, "architecture"),
    ],
)
def test_enrol_request_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(EnrolRequest), **changes)


# ──────────────────────────────────────────────────────────────────────────────
# EnrolAccept
# ──────────────────────────────────────────────────────────────────────────────


def test_enrol_accept_never_grants_night_veil() -> None:
    for tier in (CombShieldLevel.MEADOW, CombShieldLevel.PROPOLIS):
        assert _rebuild(_example(EnrolAccept), comb_shield=tier).model_dump()["comb_shield"] is tier
    with pytest.raises(ValidationError, match="never NIGHT_VEIL"):
        _rebuild(_example(EnrolAccept), comb_shield="NIGHT_VEIL")


def test_enrol_accept_accepts_every_bound_and_every_access_level() -> None:
    at_bounds = _rebuild(
        _example(EnrolAccept),
        trusted_nodes=_node_keys(MAX_TRUSTED_NODES),
        granted_capabilities=("c" * MAX_CAPABILITY_CHARS,) * MAX_GRANTED_CAPABILITIES,
    )

    assert isinstance(at_bounds, EnrolAccept)
    assert len(at_bounds.trusted_nodes) == MAX_TRUSTED_NODES
    accepts = [_rebuild(_example(EnrolAccept), access_level=level) for level in AccessLevel]
    assert [accept.model_dump()["access_level"] for accept in accepts] == list(AccessLevel)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"trusted_nodes": ()}, "at least 1"),
        ({"trusted_nodes": _node_keys(1) * 2}, "node ids must be unique"),
        ({"trusted_nodes": _node_keys(MAX_TRUSTED_NODES + 1)}, f"at most {MAX_TRUSTED_NODES}"),
        ({"hive_key_hex": KEY_HEX.upper()}, "pattern"),
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
        ({"hive_id": WARDEN_ID}, "hive_"),
        ({"warden_id": DEVICE_ID}, "warden_"),
        ({"access_level": "ALL"}, "access_level"),
    ],
)
def test_enrol_accept_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(EnrolAccept), **changes)


# ──────────────────────────────────────────────────────────────────────────────
# DeviceHeartbeat
# ──────────────────────────────────────────────────────────────────────────────


def test_device_heartbeat_reports_every_level_and_any_number_of_leases_up_to_the_cap() -> None:
    for level in RuntimeLevel:
        assert _rebuild(_example(DeviceHeartbeat), level=level).model_dump()["level"] is level
    full = _rebuild(_example(DeviceHeartbeat), lease_ids=_lease_ids(MAX_HEARTBEAT_LEASES))
    idle = _rebuild(_example(DeviceHeartbeat), lease_ids=(), uptime_s=0)

    assert isinstance(full, DeviceHeartbeat)
    assert len(full.lease_ids) == MAX_HEARTBEAT_LEASES
    assert idle.model_dump(mode="json")["lease_ids"] == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"lease_ids": _lease_ids(MAX_HEARTBEAT_LEASES + 1)}, f"at most {MAX_HEARTBEAT_LEASES}"),
        ({"lease_ids": (CELL_ID,)}, "lease_"),
        ({"level": "QUEEN"}, "level"),
        ({"hive_started_processes": -1}, "greater than or equal to 0"),
        ({"outbox_pending": -1}, "greater than or equal to 0"),
        ({"uptime_s": -0.5}, "greater than or equal to 0"),
    ],
)
def test_device_heartbeat_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(DeviceHeartbeat), **changes)
