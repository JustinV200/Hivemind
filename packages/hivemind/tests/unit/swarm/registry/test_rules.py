"""Tests for hivemind.swarm.registry.rules: the pure rules both registries apply.

Fits into the Hive:
    Mirrors src/hivemind/swarm/registry/rules.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Every function takes a value and returns a new one, so each test builds the
    value, applies the rule and reads the result. The same rules are exercised through both
    registries by the contract suite; here each branch is stated on its own.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.registry.rules for the module under test.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity
from builders.swarm import make_device_party, make_swarm_invite, make_swarm_node
from pydantic import ValidationError

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm.errors import (
    EnrolRefusal,
    InvalidNodeTransitionError,
    InviteConflictError,
    LadderError,
    NodeRevokedError,
    NodeStatusConflictError,
)
from hivemind.swarm.models import InviteState, SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry import rules
from waggle.clock import FakeClock
from waggle.messages.swarm import RuntimeLevel

_CLOCK = FakeClock()


def _node(**changes: object) -> SwarmNode:
    """An enrolled ONLINE node on the GATEWAY rung, with `changes` applied."""
    return make_swarm_node(make_device_party(_CLOCK), _CLOCK, **changes)


def test_no_invite_means_an_unknown_token() -> None:
    assert rules.judge_redemption(None, _CLOCK.now()) is EnrolRefusal.UNKNOWN_INVITE


def test_an_issued_invite_inside_its_lifetime_admits() -> None:
    assert rules.judge_redemption(make_swarm_invite(clock=_CLOCK), _CLOCK.now()) is None


def test_an_issued_invite_lapses_at_the_instant_it_expires() -> None:
    invite = make_swarm_invite(clock=_CLOCK)

    before = invite.expires_at - timedelta(microseconds=1)

    assert rules.judge_redemption(invite, before) is None
    assert rules.judge_redemption(invite, invite.expires_at) is EnrolRefusal.EXPIRED


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (InviteState.REDEEMED, EnrolRefusal.USED),
        (InviteState.EXPIRED, EnrolRefusal.EXPIRED),
        (InviteState.REVOKED, EnrolRefusal.REVOKED),
    ],
)
def test_a_final_invite_answers_with_its_own_state_whatever_the_time(
    state: InviteState, reason: EnrolRefusal
) -> None:
    device = make_device_party(_CLOCK).device_id if state is InviteState.REDEEMED else None
    invite = make_swarm_invite(clock=_CLOCK, state=state, device_id=device)

    assert rules.judge_redemption(invite, _CLOCK.now()) is reason


def test_a_device_whose_identities_are_all_new_is_not_taken() -> None:
    assert not rules.identity_taken([_node()], _node())
    assert not rules.identity_taken([], _node())


@pytest.mark.parametrize(
    "identity", ["device_id", "node_id", "cell_id", "warden_id", "public_key_hex"]
)
def test_any_one_identity_already_enrolled_makes_the_device_taken(identity: str) -> None:
    enrolled = _node()
    # Everything else is fresh, so only the shared identity can be the reason.
    newcomer = _node(**{identity: getattr(enrolled, identity)})

    assert rules.identity_taken([enrolled], newcomer)


def test_the_invite_moves_make_the_state_they_name_and_redemption_remembers_the_device() -> None:
    invite = make_swarm_invite(clock=_CLOCK)
    device = make_device_party(_CLOCK).device_id

    assert rules.expired_invite(invite).state is InviteState.EXPIRED
    assert rules.revoked_invite(invite).state is InviteState.REVOKED
    redeemed = rules.redeemed_invite(invite, device)
    assert (redeemed.state, redeemed.device_id) == (InviteState.REDEEMED, device)


@pytest.mark.parametrize("move", ["expired_invite", "revoked_invite"])
def test_an_invite_that_is_already_final_cannot_move_again(move: str) -> None:
    revoked = rules.revoked_invite(make_swarm_invite(clock=_CLOCK))

    with pytest.raises(InviteConflictError):
        getattr(rules, move)(revoked)


def test_a_redeemed_invite_cannot_be_redeemed_a_second_time() -> None:
    device = make_device_party(_CLOCK).device_id
    redeemed = rules.redeemed_invite(make_swarm_invite(clock=_CLOCK), device)

    with pytest.raises(InviteConflictError):
        rules.redeemed_invite(redeemed, device)


def test_replacing_a_field_revalidates_the_whole_node() -> None:
    node = _node()

    assert rules.replaced(node, hostname="other").hostname == "other"
    with pytest.raises(ValidationError):
        rules.replaced(node, status=NodeStatus.REVOKED)  # A revoked node needs revoked_at.


def test_a_status_move_changes_only_the_status() -> None:
    node = _node()

    moved = rules.with_status(node, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, _CLOCK.now())

    assert moved.status is NodeStatus.UNREACHABLE
    assert moved.model_copy(update={"status": NodeStatus.ONLINE}) == node


def test_revoking_stamps_the_time_it_happened() -> None:
    node, at = _node(), _CLOCK.now() + timedelta(minutes=1)

    revoked = rules.with_status(node, NodeStatus.ONLINE, NodeStatus.REVOKED, at)

    assert revoked.revoked_at == at


def test_a_status_move_from_a_status_the_node_no_longer_has_is_a_conflict() -> None:
    with pytest.raises(NodeStatusConflictError):
        rules.with_status(_node(), NodeStatus.UNREACHABLE, NodeStatus.ONLINE, _CLOCK.now())


def test_a_status_move_along_no_edge_is_refused() -> None:
    with pytest.raises(InvalidNodeTransitionError):
        rules.with_status(_node(), NodeStatus.ONLINE, NodeStatus.ONLINE, _CLOCK.now())


def test_a_rung_move_goes_one_rung_and_clears_a_request_it_fulfils() -> None:
    node = _node(desired_level=RuntimeLevel.COLONIZED)

    moved = rules.with_level(node, RuntimeLevel.COLONIZED)

    assert moved.level is RuntimeLevel.COLONIZED and moved.desired_level is None


def test_a_rung_move_short_of_the_request_keeps_it() -> None:
    node = _node(desired_level=RuntimeLevel.NUC)

    assert rules.with_level(node, RuntimeLevel.COLONIZED).desired_level is RuntimeLevel.NUC


def test_a_rung_may_not_be_skipped() -> None:
    with pytest.raises(LadderError):
        rules.with_level(_node(), RuntimeLevel.NUC)


@pytest.mark.parametrize(
    ("asked", "stored"),
    [
        (RuntimeLevel.NUC, RuntimeLevel.NUC),
        (RuntimeLevel.GATEWAY, None),  # It is on GATEWAY already: nothing is asked.
        (None, None),
    ],
)
def test_the_pending_rung_is_set_cleared_or_ignored_when_it_is_the_current_one(
    asked: RuntimeLevel | None, stored: RuntimeLevel | None
) -> None:
    assert rules.with_desired_level(_node(), asked).desired_level is stored


def test_access_and_its_capability_strings_change_together() -> None:
    changed = rules.with_access(_node(), AccessLevel.READ_ONLY, ("fs:read:**",))

    assert changed.access_level is AccessLevel.READ_ONLY
    assert changed.granted_capabilities == ("fs:read:**",)


def test_the_tier_changes_but_never_to_night_veil() -> None:
    assert rules.with_shield(_node(), CombShieldLevel.PROPOLIS).comb_shield.name == "PROPOLIS"
    with pytest.raises(ValidationError):
        rules.with_shield(_node(), CombShieldLevel.NIGHT_VEIL)


def test_watch_roots_are_replaced_and_may_be_emptied() -> None:
    watching = rules.with_watch_roots(_node(), ("/srv/a", "/srv/b"))

    assert watching.watch_roots == ("/srv/a", "/srv/b")
    assert rules.with_watch_roots(watching, ()).watch_roots == ()


def test_watch_roots_that_break_the_nodes_bounds_are_refused() -> None:
    with pytest.raises(ValidationError):
        rules.with_watch_roots(_node(), ("/srv/a", "/srv/a"))


def test_touching_moves_the_last_heard_time_forward_and_never_back() -> None:
    node = _node(last_seen_at=_CLOCK.now())
    later = _CLOCK.now() + timedelta(seconds=10)

    forward = rules.touched(node, later, None)
    backward = rules.touched(forward, _CLOCK.now(), None)

    assert forward.last_seen_at == later and backward.last_seen_at == later


def test_touching_a_node_never_heard_from_sets_the_first_time_and_may_set_the_version() -> None:
    node = _node(last_seen_at=None)

    touched = rules.touched(node, _CLOCK.now(), "2.0.0")

    assert touched.last_seen_at == _CLOCK.now() and touched.runtime_version == "2.0.0"
    assert rules.touched(node, _CLOCK.now(), None).runtime_version == node.runtime_version


def test_a_report_replaces_the_stored_capabilities_and_capacity() -> None:
    node = _node()
    capacity = make_capacity().model_copy(update={"max_sub_bees": 7})

    updated = rules.with_facts(node, make_capabilities(), capacity)

    assert updated.capacity.max_sub_bees == 7


@pytest.mark.parametrize(
    "change",
    [
        lambda n: rules.with_level(n, RuntimeLevel.COLONIZED),
        lambda n: rules.with_desired_level(n, RuntimeLevel.COLONIZED),
        lambda n: rules.with_access(n, AccessLevel.READ_ONLY, ()),
        lambda n: rules.with_shield(n, CombShieldLevel.PROPOLIS),
        lambda n: rules.with_watch_roots(n, ("/srv/a",)),
        lambda n: rules.touched(n, _CLOCK.now(), None),
        lambda n: rules.with_facts(n, make_capabilities(), make_capacity()),
    ],
    ids=["level", "desired", "access", "shield", "watch-roots", "touched", "facts"],
)
def test_nothing_changes_on_a_revoked_node(change: Callable[[SwarmNode], object]) -> None:
    revoked = _node(status=NodeStatus.REVOKED, revoked_at=_CLOCK.now())

    with pytest.raises(NodeRevokedError):
        change(revoked)
