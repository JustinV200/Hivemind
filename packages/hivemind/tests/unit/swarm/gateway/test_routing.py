"""Tests for hivemind.swarm.gateway.routing: what a connection is, decided by its first frame.

Fits into the Hive:
    Mirrors src/hivemind/swarm/gateway/routing.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The router is served over in-memory connections whose Hive end verifies with the
    real key ring, so a frame that fails verification never reaches it, exactly as on the
    listener. The same frames over real sockets are tested against the listener itself.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.routing for the module under test.
    - docs/waggle/enrolment.md sections 5 to 8 for the first-frame rules.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator

import pytest
import structlog.testing
from builders.swarm import (
    Dialled,
    HubLog,
    RouterRig,
    WardenHandlerLog,
    make_capacity_report,
    make_cell_ready,
    make_device_party,
    make_heartbeat,
    make_router_rig,
)

from hivemind.swarm.enrolment import new_invite_token
from hivemind.swarm.gateway import routing as routing_module
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from waggle.ids import new_lease_id
from waggle.messages.control import ErrorMessage
from waggle.messages.session.commands import SessionClose
from waggle.messages.swarm import EnrolAccept, NodeUpdate


@pytest.fixture
async def rig() -> AsyncIterator[RouterRig]:
    """A router over a fresh in-memory Hive Stand; every connection dialled is ended at the end."""
    router_rig = make_router_rig()
    yield router_rig
    await router_rig.close()


async def _connect(rig: RouterRig, party_status: NodeStatus = NodeStatus.ONLINE) -> Dialled:
    """Enrol a device, connect it with its heartbeat and wait until its terms arrive."""
    party, _node = await rig.enrolled(status=party_status)
    device = rig.dial(party)
    await device.send_heartbeat()
    terms = await device.next_sent()
    assert isinstance(terms.payload, NodeUpdate)
    return device


async def _enrol(rig: RouterRig) -> tuple[Dialled, str]:
    """Dial a new device with a freshly minted invite; return it and the invite's token."""
    _invite, code = await rig.stand.mint()
    return rig.dial(make_device_party(rig.stand.clock)), code


async def test_a_new_device_with_a_valid_invite_is_admitted_and_answered(rig: RouterRig) -> None:
    device, code = await _enrol(rig)

    request = await device.send_enrol(code)
    reply = await device.next_sent()

    assert isinstance(reply.payload, EnrolAccept)
    assert reply.correlation_id == request.id
    assert (reply.sender, reply.recipient) == (rig.stand.hive.hive_id, device.party.device_id)
    assert reply.payload.hive_key_hex == rig.stand.hive.public_hex
    node = await rig.stand.registry.node(device.party.device_id)
    assert node is not None and node.status is NodeStatus.ONLINE
    assert device.party.node_id in rig.ring  # Its next frame verifies against the key it enrolled.


async def test_the_connection_ends_when_a_new_device_hangs_up_after_its_accept(
    rig: RouterRig,
) -> None:
    device, code = await _enrol(rig)
    await device.send_enrol(code)
    await device.next_sent()

    await device.device_end.close()
    await device.finished()

    assert rig.stand.hub.link(device.party.device_id) is None


async def test_a_new_device_that_carries_on_with_a_heartbeat_is_served_as_a_link(
    rig: RouterRig,
) -> None:
    device, code = await _enrol(rig)
    await device.send_enrol(code)
    await device.next_sent()

    await device.send_heartbeat()
    terms = await device.next_sent()

    assert isinstance(terms.payload, NodeUpdate) and terms.payload.reason == "connected"
    assert rig.stand.hub.link(device.party.device_id) is not None


async def test_a_new_device_that_follows_its_accept_with_anything_else_is_disconnected(
    rig: RouterRig,
) -> None:
    device, code = await _enrol(rig)
    await device.send_enrol(code)
    await device.next_sent()

    await device.send(SessionClose(lease_id=new_lease_id(rig.stand.clock), reason="x"))

    assert await device.ended()
    assert rig.stand.hub.link(device.party.device_id) is None


async def test_a_device_that_hangs_up_before_its_accept_arrives_is_still_enrolled(
    rig: RouterRig,
) -> None:
    device, code = await _enrol(rig)
    await device.send_enrol(code)

    await device.device_end.close()
    await device.finished()

    assert await rig.stand.registry.node(device.party.device_id) is not None


async def test_an_unknown_invite_is_answered_with_the_reason_and_the_connection_ends(
    rig: RouterRig,
) -> None:
    device = rig.dial(make_device_party(rig.stand.clock))
    code = new_invite_token()  # Well formed, never minted.

    request = await device.send_enrol(code)
    reply = await device.next_sent()

    assert isinstance(reply.payload, ErrorMessage)
    assert reply.payload.code == "hive.swarm.enrol_denied"
    assert (reply.payload.failed_kind, reply.payload.is_retryable) == ("swarm.enrol_request", False)
    assert reply.correlation_id == request.id
    assert code not in reply.payload.message
    assert await device.ended()
    assert await rig.stand.registry.node(device.party.device_id) is None
    (refused,) = await rig.stand.events("swarm.enrol_refused")
    assert code not in str(refused.payload)


async def test_an_invite_admits_one_device_only(rig: RouterRig) -> None:
    first, code = await _enrol(rig)
    second = rig.dial(make_device_party(rig.stand.clock))
    await first.send_enrol(code)
    await first.next_sent()

    await second.send_enrol(code)
    reply = await second.next_sent()

    assert isinstance(reply.payload, ErrorMessage)
    assert await rig.stand.registry.node(second.party.device_id) is None


async def test_a_lapsed_invite_is_refused(rig: RouterRig) -> None:
    device, code = await _enrol(rig)
    rig.stand.clock.advance(16 * 60.0)  # The invite lives fifteen minutes.

    await device.send_enrol(code)

    assert isinstance((await device.next_sent()).payload, ErrorMessage)


async def test_a_known_device_connecting_with_a_heartbeat_is_attached_and_told_its_terms(
    rig: RouterRig,
) -> None:
    party, node = await rig.enrolled()
    heard = HubLog()
    rig.stand.hub.subscribe(heard)
    device = rig.dial(party)

    first = await device.send_heartbeat()
    terms = await device.next_sent()
    await heard.wait_until(lambda: len(heard.frames) == 1)

    assert isinstance(terms.payload, NodeUpdate)
    assert terms.payload.granted_capabilities == node.granted_capabilities
    link = rig.stand.hub.link(party.device_id)
    assert link is not None and link.is_open
    assert [online.device_id for online in heard.online] == [party.device_id]
    assert [frame.id for frame in heard.frames] == [first.id]  # The identifying heartbeat is kept.


async def test_a_device_that_was_unreachable_is_brought_online_by_its_connection(
    rig: RouterRig,
) -> None:
    await _connect(rig, NodeStatus.UNREACHABLE)

    (node,) = await rig.stand.registry.nodes()
    assert node.status is NodeStatus.ONLINE
    (online,) = await rig.stand.events("swarm.online")
    assert online.payload["reason"] == "link_connected"


async def test_a_link_that_ends_marks_the_device_unreachable_and_tells_the_listeners(
    rig: RouterRig,
) -> None:
    device = await _connect(rig)
    heard = HubLog()
    rig.stand.hub.subscribe(heard)

    await device.device_end.close()
    await device.finished()

    stored = await rig.stand.registry.node(device.party.device_id)
    assert stored is not None and stored.status is NodeStatus.UNREACHABLE
    assert rig.stand.hub.link(device.party.device_id) is None
    assert [reason for _node, reason in heard.offline] == ["closed"]


async def test_a_device_that_closes_straight_after_its_heartbeat_does_not_crash_the_router(
    rig: RouterRig,
) -> None:
    party, _node = await rig.enrolled()
    device = rig.dial(party)

    await device.send_heartbeat()
    await device.device_end.close()  # The terms the router sends next have nowhere to go.
    await device.finished()

    stored = await rig.stand.registry.node(party.device_id)
    assert stored is not None and stored.status is NodeStatus.UNREACHABLE


async def test_a_reconnect_replaces_the_old_link_and_the_old_links_end_takes_nothing_offline(
    rig: RouterRig,
) -> None:
    first = await _connect(rig)
    link = rig.stand.hub.link(first.party.device_id)

    second = rig.dial(first.party)
    await second.send_heartbeat()
    await second.next_sent()
    await first.finished()

    assert rig.stand.hub.link(first.party.device_id) not in (None, link)
    stored = await rig.stand.registry.node(first.party.device_id)
    assert stored is not None and stored.status is NodeStatus.ONLINE


async def test_frames_after_the_first_are_applied_and_passed_to_the_listeners(
    rig: RouterRig,
) -> None:
    device = await _connect(rig)
    heard = HubLog()
    rig.stand.hub.subscribe(heard)

    sent = await device.send(make_capacity_report(device.party, cores=48))
    await heard.wait_until(lambda: any(frame.id == sent.id for frame in heard.frames))

    stored = await rig.stand.registry.node(device.party.device_id)
    assert stored is not None and stored.capacity.host.cores == 48


async def test_a_revoked_device_is_not_served_even_with_a_key_the_ring_still_trusts(
    rig: RouterRig,
) -> None:
    party, _node = await rig.enrolled()
    await rig.stand.registry.set_status(
        party.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, rig.stand.clock.now()
    )
    device = rig.dial(party)

    await device.send_heartbeat()

    assert await device.ended()
    assert rig.stand.hub.link(party.device_id) is None


async def test_once_the_ring_forgets_a_revoked_key_its_frames_fail_like_a_forgery(
    rig: RouterRig,
) -> None:
    party, _node = await rig.enrolled()
    await rig.stand.registry.set_status(
        party.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, rig.stand.clock.now()
    )
    rig.ring.sync(await rig.stand.registry.nodes())
    device = rig.dial(party)

    await device.send_heartbeat()

    assert await device.ended()
    assert device.close_code == 1008  # The transport's policy-violation close.


@pytest.mark.parametrize("claim", ["warden_sender", "other_device_id"])
async def test_a_heartbeat_not_from_the_nodes_own_device_is_refused(
    rig: RouterRig, claim: str
) -> None:
    party, _node = await rig.enrolled()
    device = rig.dial(party)

    if claim == "warden_sender":
        await device.send(
            make_heartbeat(party), sender=party.warden_id
        )  # Right key, wrong address.
    else:
        await device.send_heartbeat(device_id=make_device_party(rig.stand.clock).device_id)

    assert await device.ended()
    assert rig.stand.hub.link(party.device_id) is None


async def test_a_first_frame_of_any_other_kind_closes_the_connection_unanswered(
    rig: RouterRig,
) -> None:
    party, _node = await rig.enrolled()
    device = rig.dial(party)

    await device.send(SessionClose(lease_id=new_lease_id(rig.stand.clock), reason="x"))

    assert await device.ended()
    assert device.close_code == 1000


async def test_a_peer_that_says_nothing_is_dropped_after_the_first_frame_timeout(
    rig: RouterRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routing_module, "FIRST_FRAME_TIMEOUT_S", 0.05)
    party, _node = await rig.enrolled()
    device = rig.dial(party)

    await device.finished()

    assert await device.ended()


async def test_a_second_nodes_frame_on_a_pinned_connection_ends_it(rig: RouterRig) -> None:
    device = await _connect(rig)
    intruder, _node = await rig.enrolled()

    device.send_as(intruder, make_capacity_report(intruder))  # Verifies, but is not this node.

    await device.finished()
    stored = await rig.stand.registry.node(device.party.device_id)
    assert stored is not None and stored.status is NodeStatus.UNREACHABLE


async def test_a_registry_failure_under_one_connection_is_logged_and_the_connection_closed(
    rig: RouterRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    party, _node = await rig.enrolled()

    async def failing(node_id: str) -> SwarmNode | None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(rig.stand.registry, "node_by_node_id", failing)
    device = rig.dial(party)

    with structlog.testing.capture_logs() as captured:
        await device.send_heartbeat()
        await device.finished()

    [failed] = [entry for entry in captured if entry["event"] == "swarm.connection_failed"]
    assert failed["error"] == "OperationalError"
    assert await device.ended()


async def test_a_warden_with_a_ready_frame_is_handed_to_the_swarm_gate(rig: RouterRig) -> None:
    handler = WardenHandlerLog()
    rig.router.bind_warden_handler(handler)
    party, node = await rig.enrolled()
    device = rig.dial(party)

    ready = await device.send(make_cell_ready(party), sender=party.warden_id)
    async with asyncio.timeout(5.0):
        await handler.handed.wait()
    beat = await device.send_heartbeat()  # A later frame reaches the handler on the same iterator.
    await device.finished()

    (connection,) = handler.seen
    assert connection.node == node and connection.first.id == ready.id
    assert [frame.id for frame in handler.frames] == [beat.id]
    assert await device.ended()  # The router closed the transport when the handler returned.


async def test_a_warden_is_closed_when_no_handler_is_bound(rig: RouterRig) -> None:
    party, _node = await rig.enrolled()
    device = rig.dial(party)

    await device.send(make_cell_ready(party), sender=party.warden_id)

    assert await device.ended()


async def test_a_warden_connection_is_closed_again_once_the_handler_is_unbound(
    rig: RouterRig,
) -> None:
    handler = WardenHandlerLog()
    rig.router.bind_warden_handler(handler)
    rig.router.bind_warden_handler(None)
    party, _node = await rig.enrolled()
    device = rig.dial(party)

    await device.send(make_cell_ready(party), sender=party.warden_id)

    assert await device.ended() and handler.seen == []


@pytest.mark.parametrize("claim", ["device_sender", "other_cell", "other_warden", "revoked"])
async def test_a_ready_frame_that_does_not_speak_for_the_nodes_own_warden_is_not_handed_over(
    rig: RouterRig, claim: str
) -> None:
    handler = WardenHandlerLog()
    rig.router.bind_warden_handler(handler)
    party, _node = await rig.enrolled()
    stranger = make_device_party(rig.stand.clock)
    if claim == "revoked":
        await rig.stand.registry.set_status(
            party.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, rig.stand.clock.now()
        )
    wrong_ids: dict[str, dict[str, object]] = {
        "other_cell": {"cell_id": stranger.cell_id},
        "other_warden": {"warden_id": stranger.warden_id},
    }
    overrides = wrong_ids.get(claim, {})
    sender = party.device_id if claim == "device_sender" else party.warden_id
    device = rig.dial(party)

    await device.send(make_cell_ready(party, **overrides), sender=sender)

    assert await device.ended() and handler.seen == []
