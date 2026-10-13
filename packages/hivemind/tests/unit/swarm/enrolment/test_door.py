"""Tests for hivemind.swarm.enrolment.door: minting invites and admitting the device with one.

Fits into the Hive:
    Mirrors src/hivemind/swarm/enrolment/door.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The door is built over the real registry fake and trail fake, so each test reads
    the registry and the trail the way an operator's command would after the fact.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.enrolment.door for the module under test.
    - docs/waggle/enrolment.md sections 3 and 6 for the ceremony.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from builders.swarm import (
    DeviceParty,
    HiveRig,
    make_device_party,
    make_enrol_envelope,
    make_enrol_request,
    make_heartbeat,
    make_hive_rig,
)
from pydantic import ValidationError

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm.enrolment import new_invite_token, token_hash
from hivemind.swarm.errors import EnrolmentDeniedError, EnrolRefusal, InviteRefusedError
from hivemind.swarm.models import InviteState
from hivemind.swarm.node_state import NodeStatus
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import new_hive_id
from waggle.messages.swarm import EnrolAccept


@pytest.fixture
def rig() -> HiveRig:
    """A Hive Stand over in-memory storage, with a door minting 15-minute invites."""
    return make_hive_rig()


def _envelope(rig: HiveRig, party: DeviceParty, code: str, **changes: object) -> Envelope:
    """The envelope `party` would send presenting `code`, with `changes` made to its request."""
    request = make_enrol_request(party, code, **changes)
    return make_enrol_envelope(party, rig.hive, request, rig.clock)


async def _refused(rig: HiveRig, envelope: Envelope) -> EnrolmentDeniedError:
    """Present `envelope` and return the denial the door raised."""
    with pytest.raises(EnrolmentDeniedError) as caught:
        await rig.door.enrol(envelope)
    return caught.value


async def test_a_minted_invite_is_stored_as_a_hash_with_its_cap_tier_label_and_expiry(
    rig: HiveRig,
) -> None:
    minted, code = await rig.mint(AccessLevel.SCRATCH, CombShieldLevel.PROPOLIS, "kitchen-pi")

    stored = await rig.registry.get_invite(token_hash(code))

    assert stored is not None and minted.code_hash == stored.code_hash == token_hash(code)
    assert stored.state is InviteState.ISSUED
    assert (stored.access_level, stored.comb_shield, stored.label) == (
        AccessLevel.SCRATCH,
        CombShieldLevel.PROPOLIS,
        "kitchen-pi",
    )
    assert stored.expires_at == rig.clock.now() + timedelta(minutes=15) == minted.expires_at
    assert code not in stored.model_dump_json()


async def test_minting_records_the_invite_without_the_token_or_the_string(rig: HiveRig) -> None:
    minted, code = await rig.mint(label="desk")

    (event,) = await rig.events("swarm.invited")

    assert event.subject_id == rig.hive.hive_id and event.actor == "system"
    assert set(event.payload) == {"label", "access_level", "comb_shield", "expires_at"}
    assert (
        code not in event.model_dump_json() and minted.invite_string not in event.model_dump_json()
    )


async def test_the_minted_invites_repr_hides_the_string_that_is_the_secret(rig: HiveRig) -> None:
    minted, _code = await rig.mint()

    assert minted.invite_string not in repr(minted)
    assert minted.code_hash[:12] in repr(minted)


async def test_no_two_invites_share_a_token(rig: HiveRig) -> None:
    _first, one = await rig.mint()
    _second, two = await rig.mint()

    assert one != two and len(await rig.registry.invites()) == 2


async def test_a_night_veil_invite_cannot_be_minted_and_nothing_is_stored(rig: HiveRig) -> None:
    with pytest.raises(InviteRefusedError):
        await rig.mint(shield=CombShieldLevel.NIGHT_VEIL)

    assert await rig.registry.invites() == () and await rig.kinds() == []


async def test_a_label_too_long_fails_before_anything_is_stored(rig: HiveRig) -> None:
    with pytest.raises(ValidationError):
        await rig.mint(label="x" * 65)

    assert await rig.registry.invites() == () and await rig.kinds() == []


async def test_a_valid_request_creates_the_node_spends_the_invite_and_records_it_together(
    rig: HiveRig,
) -> None:
    _minted, code = await rig.mint(AccessLevel.FULL)
    party = make_device_party(rig.clock)

    accept = await rig.door.enrol(_envelope(rig, party, code))

    node = await rig.registry.node(party.device_id)
    invite = await rig.registry.get_invite(token_hash(code))
    assert isinstance(accept, EnrolAccept) and accept.device_id == party.device_id
    assert node is not None and node.status is NodeStatus.ONLINE
    assert (node.cell_id, node.warden_id) == (accept.cell_id, accept.warden_id)
    assert invite is not None and invite.state is InviteState.REDEEMED
    assert invite.device_id == party.device_id
    assert await rig.kinds() == ["swarm.invited", "swarm.enrolled"]


async def test_the_enrolled_event_names_ids_and_terms_and_nothing_secret(rig: HiveRig) -> None:
    _minted, code = await rig.mint(label="desk")
    party = make_device_party(rig.clock)
    await rig.door.enrol(_envelope(rig, party, code))

    (event,) = await rig.events("swarm.enrolled")

    assert event.subject_id == party.device_id
    assert event.payload["node_id"] == party.node_id
    assert event.payload["label"] == "desk"
    assert code not in event.model_dump_json() and party.public_hex not in event.model_dump_json()


async def test_the_invites_cap_lowers_what_the_device_asked_for(rig: HiveRig) -> None:
    _minted, code = await rig.mint(AccessLevel.SCRATCH)
    party = make_device_party(rig.clock)

    accept = await rig.door.enrol(_envelope(rig, party, code))

    assert accept.access_level == AccessLevel.SCRATCH.to_wire()


@pytest.mark.parametrize(
    "refusal",
    [
        EnrolRefusal.UNKNOWN_INVITE,
        EnrolRefusal.USED,
        EnrolRefusal.EXPIRED,
        EnrolRefusal.REVOKED,
        EnrolRefusal.WRONG_HIVE,
        EnrolRefusal.IDENTITY_MISMATCH,
        EnrolRefusal.IDENTITY_IN_USE,
        EnrolRefusal.NOT_AN_ENROLMENT,
    ],
)
async def test_every_way_of_being_refused_creates_nothing_and_records_a_reason_code(
    rig: HiveRig, refusal: EnrolRefusal
) -> None:
    _minted, code = await rig.mint()
    party = make_device_party(rig.clock)
    envelope = _envelope(rig, party, code)
    if refusal is EnrolRefusal.UNKNOWN_INVITE:
        envelope = _envelope(rig, party, new_invite_token())
    elif refusal is EnrolRefusal.USED:
        await rig.door.enrol(_envelope(rig, make_device_party(rig.clock), code))
    elif refusal is EnrolRefusal.EXPIRED:
        rig.clock.advance(16 * 60.0)
    elif refusal is EnrolRefusal.REVOKED:
        await rig.registry.revoke_invite(token_hash(code))
    elif refusal is EnrolRefusal.WRONG_HIVE:
        hop = Hop(sender=party.device_id, recipient=new_hive_id(rig.clock), node_id=party.node_id)
        envelope = wrap(make_enrol_request(party, code), hop, clock=rig.clock)
    elif refusal is EnrolRefusal.IDENTITY_MISMATCH:
        envelope = _envelope(rig, party, code, device_id=make_device_party(rig.clock).device_id)
    elif refusal is EnrolRefusal.IDENTITY_IN_USE:
        _other, other_code = await rig.mint()
        await rig.door.enrol(_envelope(rig, party, other_code))
    else:
        hop = Hop(sender=party.device_id, recipient=rig.hive.hive_id, node_id=party.node_id)
        envelope = wrap(make_heartbeat(party), hop, clock=rig.clock)
    nodes_before = len(await rig.registry.nodes())

    denied = await _refused(rig, envelope)

    assert denied.reason is refusal and denied.device_id and denied.node_id
    assert len(await rig.registry.nodes()) == nodes_before
    refused = await rig.events("swarm.enrol_refused")
    assert [event.payload["reason"] for event in refused] == [refusal.value]
    assert set(refused[0].payload) == {"device_id", "node_id", "reason"}
    for text in (str(denied), refused[0].model_dump_json()):
        assert code not in text


async def test_a_lapsed_invite_that_is_presented_is_moved_to_expired(rig: HiveRig) -> None:
    _minted, code = await rig.mint()
    rig.clock.advance(16 * 60.0)

    await _refused(rig, _envelope(rig, make_device_party(rig.clock), code))

    invite = await rig.registry.get_invite(token_hash(code))
    assert invite is not None and invite.state is InviteState.EXPIRED


async def test_two_devices_presenting_one_token_at_once_admit_exactly_one(rig: HiveRig) -> None:
    _minted, code = await rig.mint()
    first, second = make_device_party(rig.clock), make_device_party(rig.clock)

    results = await asyncio.gather(
        rig.door.enrol(_envelope(rig, first, code)),
        rig.door.enrol(_envelope(rig, second, code)),
        return_exceptions=True,
    )

    accepted = [r for r in results if isinstance(r, EnrolAccept)]
    denied = [r for r in results if isinstance(r, EnrolmentDeniedError)]
    assert len(accepted) == 1 and len(denied) == 1 and denied[0].reason is EnrolRefusal.USED
    assert len(await rig.registry.nodes()) == 1
