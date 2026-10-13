"""Tests for hivemind.swarm.models: the invite and the enrolled device as the registry stores them.

Fits into the Hive:
    Mirrors src/hivemind/swarm/models.py (codingrules section 3: tests/unit mirrors src/ one-to-one,
    and every boundary model gets a round-trip test and a rejection test). The registry keeps each
    model as JSON, so a model that does not survive its own JSON is data lost, and a model that
    accepts what it should refuse is a bad row the rest of the Hive then trusts.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.models for the module under test.
    - docs/waggle/enrolment.md section 3.2 and 6 for the rows.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest
from builders.swarm import make_device_party, make_swarm_invite, make_swarm_node
from pydantic import ValidationError

from hivemind.cell import AccessLevel, CellKind, CombShieldLevel
from hivemind.cell.models import MAX_CELL_NAME_CHARS
from hivemind.swarm.errors import InviteConflictError
from hivemind.swarm.models import (
    INVITE_TRANSITIONS,
    MAX_LABEL_CHARS,
    SWARM_SOURCE,
    InviteState,
    SwarmInvite,
    SwarmNode,
    assert_invite_transition,
)
from hivemind.swarm.node_state import NodeStatus
from waggle.clock import FakeClock
from waggle.messages.swarm import RuntimeLevel
from waggle.messages.swarm.enrolment import (
    MAX_CAPABILITY_CHARS,
    MAX_GRANTED_CAPABILITIES,
    MAX_HOSTNAME_CHARS,
)

_CLOCK = FakeClock()
_DEVICE = make_device_party(_CLOCK).device_id
_STATES = list(InviteState)
_INVITE_EDGES = {
    (InviteState.ISSUED, InviteState.REDEEMED),
    (InviteState.ISSUED, InviteState.EXPIRED),
    (InviteState.ISSUED, InviteState.REVOKED),
}


def _invite(**changes: object) -> SwarmInvite:
    """An ISSUED invite with `changes` applied (validated)."""
    return make_swarm_invite(None, _CLOCK, **changes)


def _node(**changes: object) -> SwarmNode:
    """An ONLINE node on the GATEWAY rung with `changes` applied (validated)."""
    return make_swarm_node(make_device_party(_CLOCK), _CLOCK, **changes)


# ──────────────────────────────────────────────────────────────────────────────
# The invite machine
# ──────────────────────────────────────────────────────────────────────────────


def test_the_invite_table_lists_exactly_the_three_ways_out_of_issued() -> None:
    listed = {(a, b) for a, targets in INVITE_TRANSITIONS.items() for b in targets}

    assert listed == _INVITE_EDGES and set(INVITE_TRANSITIONS) == set(_STATES)


@pytest.mark.parametrize(("start", "end"), list(itertools.product(_STATES, _STATES)), ids=str)
def test_every_pair_of_invite_states_is_walked_or_refused_as_the_table_says(
    start: InviteState, end: InviteState
) -> None:
    if (start, end) in _INVITE_EDGES:
        assert_invite_transition(start, end, "0123456789abcdef")
        return
    with pytest.raises(InviteConflictError) as caught:
        assert_invite_transition(start, end, "0123456789abcdef")
    assert "0123456789ab..." in str(caught.value) and "0123456789abcdef" not in str(caught.value)


def test_a_refused_invite_move_reads_without_a_hash_when_the_caller_has_none() -> None:
    with pytest.raises(InviteConflictError) as caught:
        assert_invite_transition(InviteState.REDEEMED, InviteState.ISSUED)

    assert "..." not in str(caught.value)


# ──────────────────────────────────────────────────────────────────────────────
# SwarmInvite
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "invite",
    [
        _invite(),
        _invite(label="kitchen-pi", comb_shield=CombShieldLevel.PROPOLIS),
        _invite(state=InviteState.REDEEMED, device_id=_DEVICE),
        _invite(state=InviteState.EXPIRED),
        _invite(state=InviteState.REVOKED, access_level=AccessLevel.READ_ONLY),
    ],
    ids=["issued", "labelled", "redeemed", "expired", "revoked"],
)
def test_an_invite_survives_its_own_json_and_its_python_dump(invite: SwarmInvite) -> None:
    assert SwarmInvite.model_validate_json(invite.model_dump_json()) == invite
    assert SwarmInvite.model_validate(invite.model_dump(mode="json")) == invite


@pytest.mark.parametrize(
    "changes",
    [
        {"comb_shield": CombShieldLevel.NIGHT_VEIL},
        {"expires_at": datetime(2020, 1, 1, tzinfo=UTC)},  # The same instant it was created.
        {"expires_at": datetime(2019, 12, 31, tzinfo=UTC)},
        {"device_id": _DEVICE},  # Set on an ISSUED invite.
        {"state": InviteState.REDEEMED},  # REDEEMED with no device.
        {"code_hash": "ABC"},
        {"code_hash": "A" * 64},  # Uppercase hex.
        {"label": "x" * (MAX_LABEL_CHARS + 1)},
        {"created_at": datetime(2020, 1, 1)},  # No time zone, on purpose.
        {"unknown_field": 1},
    ],
    ids=[
        "night-veil",
        "zero-lifetime",
        "negative-lifetime",
        "device-on-issued",
        "redeemed-no-device",
        "short-hash",
        "uppercase-hash",
        "long-label",
        "naive-time",
        "extra-field",
    ],
)
def test_an_invite_that_breaks_a_rule_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _invite(**changes)


def test_an_invite_is_frozen() -> None:
    invite = _invite()

    with pytest.raises(ValidationError):
        invite.label = "changed"


def test_an_invite_missing_a_required_field_is_rejected() -> None:
    fields = _invite().model_dump(mode="json")
    del fields["access_level"]

    with pytest.raises(ValidationError):
        SwarmInvite.model_validate(fields)


# ──────────────────────────────────────────────────────────────────────────────
# SwarmNode
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "node",
    [
        _node(),
        _node(label="kitchen-pi", max_sub_bees=3, last_seen_at=None),
        _node(status=NodeStatus.UNREACHABLE, level=RuntimeLevel.COLONIZED),
        _node(desired_level=RuntimeLevel.COLONIZED, access_level=AccessLevel.FULL),
        _node(status=NodeStatus.REVOKED, revoked_at=_CLOCK.now()),
    ],
    ids=["online", "labelled", "unreachable-colonized", "pending-rung", "revoked"],
)
def test_a_node_survives_its_own_json_and_its_python_dump(node: SwarmNode) -> None:
    assert SwarmNode.model_validate_json(node.model_dump_json()) == node
    assert SwarmNode.model_validate(node.model_dump(mode="json")) == node


@pytest.mark.parametrize(
    "changes",
    [
        {"comb_shield": CombShieldLevel.NIGHT_VEIL},
        {"revoked_at": datetime(2020, 1, 1, tzinfo=UTC)},  # Set while not REVOKED.
        {"status": NodeStatus.REVOKED},  # REVOKED without a time.
        {"desired_level": RuntimeLevel.GATEWAY},  # Pending, but it is already there.
        {"public_key_hex": "abc"},
        {"public_key_hex": "G" * 64},
        {"hostname": "h" * (MAX_HOSTNAME_CHARS + 1)},
        {"label": "x" * (MAX_LABEL_CHARS + 1)},
        {"offline_limit_s": 0.0},
        {"heartbeat_interval_s": -1.0},
        {"max_sub_bees": -1},
        {"granted_capabilities": ("exec:*",) * (MAX_GRANTED_CAPABILITIES + 1)},
        {"granted_capabilities": ("x" * (MAX_CAPABILITY_CHARS + 1),)},
        {"enrolled_at": datetime(2020, 1, 1)},  # No time zone, on purpose.
        {"device_id": "not-an-id"},
        {"unknown_field": 1},
    ],
    ids=[
        "night-veil",
        "revoked-at-while-online",
        "revoked-without-time",
        "pending-the-current-rung",
        "short-key",
        "non-hex-key",
        "long-hostname",
        "long-label",
        "zero-limit",
        "negative-interval",
        "negative-sub-bees",
        "too-many-capabilities",
        "long-capability",
        "naive-time",
        "bad-device-id",
        "extra-field",
    ],
)
def test_a_node_that_breaks_a_rule_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _node(**changes)


def test_a_node_is_frozen() -> None:
    node = _node()

    with pytest.raises(ValidationError):
        node.hostname = "changed"


def test_a_nodes_platform_is_derived_from_its_capabilities() -> None:
    node = _node()

    assert node.platform == node.capabilities.to_wire()[0]


def test_a_node_is_a_real_cell_named_for_its_label_else_its_hostname() -> None:
    labelled, plain = _node(label="kitchen-pi"), _node(label=None, hostname="raspberrypi")

    cell = labelled.as_cell()

    assert (cell.id, cell.kind, cell.source) == (labelled.cell_id, CellKind.REAL, SWARM_SOURCE)
    assert cell.name == "kitchen-pi" and plain.as_cell().name == "raspberrypi"
    assert (cell.access_level, cell.comb_shield) == (labelled.access_level, labelled.comb_shield)
    assert cell.capabilities == labelled.capabilities and cell.capacity == labelled.capacity


def test_a_hostname_longer_than_a_cell_name_is_cut_to_fit() -> None:
    node = _node(label=None, hostname="h" * MAX_HOSTNAME_CHARS)

    assert len(node.as_cell().name) <= MAX_CELL_NAME_CHARS
