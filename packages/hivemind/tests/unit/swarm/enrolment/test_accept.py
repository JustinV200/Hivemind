"""Tests for hivemind.swarm.enrolment.accept: whether an invite admits a device, and on what terms.

Fits into the Hive:
    Mirrors src/hivemind/swarm/enrolment/accept.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one). Everything under test is pure, so the tests build values with the swarm
    builders and read the decision straight off the result.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.enrolment.accept for the module under test.
    - docs/waggle/enrolment.md section 7 for the terms a device is admitted on.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.swarm import (
    make_device_party,
    make_endpoint,
    make_enrol_envelope,
    make_enrol_request,
    make_hive_party,
    make_rules,
    make_swarm_invite,
    make_swarm_node,
)

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.cell.local import DEFAULT_MAX_SUB_BEES
from hivemind.manifest import SwarmSection
from hivemind.swarm.enrolment import (
    Admission,
    EnrolmentRules,
    MintedIds,
    check_request,
    decide_enrolment,
    granted_capabilities,
    node_update_for,
)
from hivemind.swarm.errors import EnrolRefusal
from hivemind.swarm.models import InviteState
from hivemind.swarm.node_state import NodeStatus
from waggle.clock import FakeClock
from waggle.envelope import Hop, wrap
from waggle.ids import new_cell_id, new_hive_id, new_warden_id
from waggle.messages.swarm import RuntimeLevel

_CLOCK = FakeClock()
_HIVE = make_hive_party(_CLOCK)
_RULES = make_rules(make_endpoint(_HIVE))
_MINTED = MintedIds(cell_id=new_cell_id(_CLOCK), warden_id=new_warden_id(_CLOCK))
_CODE = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ"
_LEVELS = list(AccessLevel)


def _admit(access: AccessLevel = AccessLevel.FULL, **request_changes: object) -> Admission:
    """Decide an enrolment of a fresh device against a fresh invite capped at `access`."""
    party = make_device_party(_CLOCK)
    request = make_enrol_request(party, _CODE, **request_changes)
    invite = make_swarm_invite(_CODE, _CLOCK, access_level=access)
    decision = decide_enrolment(request, invite, _RULES, _MINTED, _CLOCK.now())
    assert isinstance(decision, Admission)
    return decision


def test_a_request_addressed_to_this_hive_and_naming_its_own_ids_passes() -> None:
    party = make_device_party(_CLOCK)
    request = make_enrol_request(party, _CODE)
    envelope = make_enrol_envelope(party, _HIVE, request, _CLOCK)

    assert check_request(envelope, request, _RULES) is None


def test_a_request_addressed_to_another_hives_queen_is_refused() -> None:
    party = make_device_party(_CLOCK)
    request = make_enrol_request(party, _CODE)
    hop = Hop(sender=party.device_id, recipient=new_hive_id(_CLOCK), node_id=party.node_id)

    refusal = check_request(wrap(request, hop, clock=_CLOCK), request, _RULES)

    assert refusal is EnrolRefusal.WRONG_HIVE


@pytest.mark.parametrize("claim", ["device_id", "node_id"])
def test_a_request_naming_ids_its_envelope_does_not_prove_is_refused(claim: str) -> None:
    party, other = make_device_party(_CLOCK), make_device_party(_CLOCK)
    request = make_enrol_request(party, _CODE, **{claim: getattr(other, claim)})
    envelope = make_enrol_envelope(party, _HIVE, request, _CLOCK)

    assert check_request(envelope, request, _RULES) is EnrolRefusal.IDENTITY_MISMATCH


@pytest.mark.parametrize("asked", _LEVELS)
@pytest.mark.parametrize("cap", _LEVELS)
def test_a_device_is_granted_the_lower_of_what_it_asked_and_the_invites_cap(
    asked: AccessLevel, cap: AccessLevel
) -> None:
    admission = _admit(cap, requested_access=asked.to_wire())

    expected = asked if asked.rank <= cap.rank else cap
    assert admission.node.access_level is expected
    assert admission.node.requested_access is asked
    assert admission.accept.access_level == expected.to_wire()
    assert admission.node.granted_capabilities == granted_capabilities(expected, _MINTED.cell_id)


def test_an_admitted_node_is_online_on_the_gateway_rung_with_the_minted_ids() -> None:
    admission = _admit()
    node = admission.node

    assert (node.status, node.level) == (NodeStatus.ONLINE, RuntimeLevel.GATEWAY)
    assert (node.cell_id, node.warden_id) == (_MINTED.cell_id, _MINTED.warden_id)
    assert node.enrolled_at == node.last_seen_at == _CLOCK.now()
    assert node.desired_level is None and node.revoked_at is None


def test_the_tier_and_label_come_from_the_invite_never_from_the_device() -> None:
    party = make_device_party(_CLOCK)
    request = make_enrol_request(party, _CODE)
    invite = make_swarm_invite(
        _CODE, _CLOCK, comb_shield=CombShieldLevel.PROPOLIS, label="kitchen-pi"
    )

    decision = decide_enrolment(request, invite, _RULES, _MINTED, _CLOCK.now())

    assert isinstance(decision, Admission)
    assert decision.node.comb_shield is CombShieldLevel.PROPOLIS
    assert decision.node.label == "kitchen-pi"
    assert "kitchen-pi" in decision.node.reason and "PROPOLIS" in decision.node.reason


def test_an_unlabelled_invite_is_named_as_such_in_the_reason() -> None:
    assert "unlabelled" in _admit().node.reason


def test_the_accept_carries_the_hive_key_the_trusted_node_and_the_timings() -> None:
    accept = _admit().accept
    endpoint = _RULES.endpoint

    assert accept.hive_id == endpoint.hive_id and accept.hive_key_hex == endpoint.hive_key_hex
    assert [(k.node_id, k.public_key_hex) for k in accept.trusted_nodes] == [
        (endpoint.hive_node_id, endpoint.hive_key_hex)
    ]
    assert (accept.offline_limit_s, accept.heartbeat_interval_s) == (120.0, 15.0)
    assert (accept.cell_id, accept.warden_id) == (_MINTED.cell_id, _MINTED.warden_id)


def test_a_device_that_names_no_sub_bee_cap_gets_the_default_capacity_and_no_cap() -> None:
    node = _admit().node

    assert node.max_sub_bees is None
    assert node.capacity.max_sub_bees == DEFAULT_MAX_SUB_BEES
    assert node.capacity.local_seats == ()


def test_a_device_that_names_a_sub_bee_cap_has_it_recorded_both_ways() -> None:
    node = _admit(max_sub_bees=3).node

    assert node.max_sub_bees == 3 and node.capacity.max_sub_bees == 3


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (InviteState.REDEEMED, EnrolRefusal.USED),
        (InviteState.EXPIRED, EnrolRefusal.EXPIRED),
        (InviteState.REVOKED, EnrolRefusal.REVOKED),
    ],
)
def test_an_invite_that_is_no_longer_issued_admits_no_one(
    state: InviteState, reason: EnrolRefusal
) -> None:
    party = make_device_party(_CLOCK)
    changes: dict[str, object] = {"state": state}
    if state is InviteState.REDEEMED:
        changes["device_id"] = make_device_party(_CLOCK).device_id
    invite = make_swarm_invite(_CODE, _CLOCK, **changes)

    decision = decide_enrolment(
        make_enrol_request(party, _CODE), invite, _RULES, _MINTED, _CLOCK.now()
    )

    assert decision is reason


def test_an_issued_invite_past_its_time_to_live_admits_no_one() -> None:
    party = make_device_party(_CLOCK)
    invite = make_swarm_invite(_CODE, _CLOCK)
    later = _CLOCK.now() + timedelta(minutes=16)

    decision = decide_enrolment(make_enrol_request(party, _CODE), invite, _RULES, _MINTED, later)

    assert decision is EnrolRefusal.EXPIRED


def test_the_rules_take_their_timings_from_the_manifest_section() -> None:
    section = SwarmSection(dead_man_s=300.0, heartbeat_interval_s=5.0, invite_ttl_minutes=2.0)

    rules = EnrolmentRules.from_section(section, _RULES.endpoint)

    assert rules.offline_limit_s == 300.0 and rules.heartbeat_interval_s == 5.0
    assert rules.invite_ttl == timedelta(minutes=2)
    assert rules.endpoint == _RULES.endpoint


def test_the_node_update_restates_the_nodes_standing_terms() -> None:
    node = make_swarm_node(make_device_party(_CLOCK), _CLOCK, access_level=AccessLevel.FULL)

    update = node_update_for(node, "connected")

    assert (update.device_id, update.cell_id, update.warden_id) == (
        node.device_id,
        node.cell_id,
        node.warden_id,
    )
    assert update.level is node.level and update.reason == "connected"
    assert update.access_level == node.access_level.to_wire()
    assert update.comb_shield == node.comb_shield.to_wire()
    assert update.granted_capabilities == node.granted_capabilities
    assert (update.offline_limit_s, update.heartbeat_interval_s) == (120.0, 15.0)
    assert update.watch_roots == ()  # None were named for it.


def test_the_node_update_carries_the_directories_the_operator_named_for_its_warden() -> None:
    roots = ("/var/log/app", "/srv/data")
    node = make_swarm_node(make_device_party(_CLOCK), _CLOCK, watch_roots=roots)

    assert node_update_for(node, "connected").watch_roots == roots


def test_the_node_update_carries_only_the_directories_the_nodes_capabilities_cover() -> None:
    # A Warden on the device cannot ask the Hive what it may read, so it is told only what the
    # packet would let it; a directory the strings do not cover is never sent.
    node = make_swarm_node(
        make_device_party(_CLOCK),
        _CLOCK,
        granted_capabilities=("fs:read:/srv/**", f"watch:{new_cell_id(_CLOCK)}"),
        watch_roots=("/srv/data", "/var/log"),
    )

    assert node_update_for(node, "connected").watch_roots == ("/srv/data",)
