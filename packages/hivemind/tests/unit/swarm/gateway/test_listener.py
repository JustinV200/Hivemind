"""Tests for hivemind.swarm.gateway.listener: the door a Pollen Packet dials, over real sockets.

Fits into the Hive:
    Mirrors src/hivemind/swarm/gateway/listener.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Every test dials a real loopback WebSocket with a device's real signing key, so
    the transport's signature policy, frame ceiling, replay window and close codes are the real
    ones. The router's decisions are tested in test_routing.py over in-memory connections; here
    the same decisions are shown to hold across a socket. TLS and the pin are in
    test_listener_tls.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.listener for the module under test.
    - docs/waggle/enrolment.md sections 5 to 9 for the ceremony these tests walk.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from builders.swarm import (
    DeviceClient,
    DeviceParty,
    HiveRig,
    HubLog,
    WardenHandlerLog,
    enrol_node,
    make_capacity_report,
    make_cell_ready,
    make_device_party,
    make_hive_rig,
    make_swarm_node,
)
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from hivemind.swarm.gateway import ListenerConfig, LivenessRules, SwarmListener
from hivemind.swarm.gateway.refusals import RECONNECT_GRACE_S
from hivemind.swarm.node_state import NodeStatus
from waggle.codec import Signer
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.messages.control import ErrorMessage
from waggle.messages.swarm import EnrolAccept, NodeUpdate
from waggle.transport.ceiling import BURST_FRAMES


@dataclass
class _Live:
    """A started listener over an in-memory stand, and the devices dialled into it."""

    stand: HiveRig
    listener: SwarmListener
    log: HubLog
    clients: list[DeviceClient] = field(default_factory=list)

    async def dial(
        self, party: DeviceParty | None = None, *, signer: Signer | None = None
    ) -> DeviceClient:
        """Dial the listener as `party` (a new device when omitted), signing with `signer`."""
        who = party if party is not None else make_device_party(self.stand.clock)
        client = DeviceClient(self.stand, who, self.listener.uri, signer=signer)
        self.clients.append(client)
        await client.connect()
        return client

    async def enrolled(self, **overrides: object) -> DeviceParty:
        """Enrol a device in the registry and make the listener trust its key."""
        party = make_device_party(self.stand.clock)
        node = make_swarm_node(party, self.stand.clock, **overrides)
        await enrol_node(self.stand.registry, node, self.stand.clock)
        await self.listener.sync_keys()
        return party

    async def connected(self, party: DeviceParty | None = None) -> DeviceClient:
        """Dial as an enrolled device, send its heartbeat and wait for its terms."""
        who = party if party is not None else await self.enrolled()
        client = await self.dial(who)
        await client.send_heartbeat()
        assert isinstance((await client.next()).payload, NodeUpdate)
        return client

    async def revoke(self, party: DeviceParty) -> None:
        """Revoke a device in the registry, without telling the listener."""
        await self.stand.registry.set_status(
            party.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, self.stand.clock.now()
        )

    async def marker(self, client: DeviceClient) -> None:
        """Send a frame after the one under test and wait until the listener has handled it.

        Frames on one connection are handled in order, so once this one is seen, every frame
        sent before it has been handled too.
        """
        sent = await client.send(make_capacity_report(client.party, cores=1))
        await self.log.wait_until(lambda: any(frame.id == sent.id for frame in self.log.frames))


@pytest.fixture
async def live() -> AsyncIterator[_Live]:
    """A started plaintext listener on an OS-chosen loopback port, stopped after the test."""
    stand = make_hive_rig()
    listener, log = stand.listener(), HubLog()
    stand.hub.subscribe(log)
    await listener.start()
    state = _Live(stand, listener, log)
    yield state
    for client in state.clients:
        await client.close()
    await listener.stop()


async def test_a_new_device_enrols_over_a_real_socket_and_carries_on_as_a_link(live: _Live) -> None:
    _invite, code = await live.stand.mint()
    device = await live.dial()

    request = await device.send_enrol(code)
    reply = await device.next()
    await device.send_heartbeat()
    terms = await device.next()

    assert isinstance(reply.payload, EnrolAccept) and reply.correlation_id == request.id
    assert isinstance(terms.payload, NodeUpdate) and terms.payload.reason == "connected"
    link = live.stand.hub.link(device.party.device_id)
    assert link is not None and link.is_open
    assert [node.device_id for node in live.log.online] == [device.party.device_id]


async def test_a_denied_enrolment_is_answered_over_the_socket_and_the_connection_ends(
    live: _Live,
) -> None:
    device = await live.dial()

    await device.send_enrol("AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AA")
    reply = await device.next()

    assert isinstance(reply.payload, ErrorMessage)
    assert reply.payload.code == "hive.swarm.enrol_denied"
    assert await device.closed()
    assert await live.stand.registry.node(device.party.device_id) is None


async def test_an_enrolled_device_reconnects_with_a_heartbeat_as_its_first_frame(
    live: _Live,
) -> None:
    party = await live.enrolled(status=NodeStatus.UNREACHABLE)

    await live.connected(party)

    node = await live.stand.registry.node(party.device_id)
    assert node is not None and node.status is NodeStatus.ONLINE
    assert live.stand.hub.link(party.device_id) is not None


async def test_a_second_connection_from_the_same_device_replaces_the_first(live: _Live) -> None:
    first = await live.connected()

    second = await live.connected(first.party)

    assert await first.closed()
    link = live.stand.hub.link(first.party.device_id)
    assert link is not None and link.is_open
    node = await live.stand.registry.node(first.party.device_id)
    assert node is not None and node.status is NodeStatus.ONLINE
    assert second.transport.is_connected


async def test_a_revoked_device_whose_key_the_listener_dropped_is_refused_like_a_forgery(
    live: _Live,
) -> None:
    party = await live.enrolled()
    await live.revoke(party)
    await live.listener.sync_keys()
    device = await live.dial(party)

    await device.send_heartbeat()

    assert await device.closed()
    assert live.stand.hub.link(party.device_id) is None


async def test_a_revoked_device_is_refused_even_before_the_listener_drops_its_key(
    live: _Live,
) -> None:
    party = await live.enrolled()
    await live.revoke(party)
    device = await live.dial(party)

    await device.send_heartbeat()

    assert await device.closed()
    assert live.stand.hub.link(party.device_id) is None


async def test_a_first_frame_signed_with_another_key_is_refused(live: _Live) -> None:
    victim = await live.enrolled()
    attacker = make_device_party(live.stand.clock)
    forger = await live.dial(victim, signer=attacker.signer)  # Claims the victim, signs as itself.

    await forger.send_heartbeat()

    assert await forger.closed()
    assert live.stand.hub.link(victim.device_id) is None


async def test_a_frame_from_a_node_nobody_enrolled_is_refused(live: _Live) -> None:
    stranger = await live.dial()

    await stranger.send_heartbeat()

    assert await stranger.closed()
    assert live.stand.hub.entries() == ()


async def test_a_frame_flood_is_cut_off_and_recorded_against_the_node(live: _Live) -> None:
    device = await live.connected()

    # Three frames past the burst. The listener stops reading at the ceiling, and websockets only
    # resumes a paused receive queue once it holds fewer than four frames, so a longer backlog
    # would hold the close handshake up until the transport's close timeout (ten seconds).
    with contextlib.suppress(ConnectionLostError, TransportClosedError):
        for _ in range(BURST_FRAMES + 3):
            await device.send_heartbeat()

    assert await device.closed()
    (event,) = await live.stand.events("guard.frame_ceiling")
    assert event.subject_id == device.party.node_id
    assert event.actor == "system"


async def test_a_replayed_frame_outside_the_reconnection_grace_is_refused_and_recorded(
    live: _Live,
) -> None:
    device = await live.connected()
    original = await device.send_heartbeat()
    await live.log.wait_until(lambda: any(f.id == original.id for f in live.log.frames))
    live.stand.clock.advance(RECONNECT_GRACE_S + 1.0)

    await device.transport.send(original)  # The very same envelope again.
    await live.marker(device)

    (event,) = await live.stand.events("guard.replay_refused")
    assert event.subject_id == device.party.node_id
    assert event.payload["envelope_id"] == original.id


async def test_a_repeated_frame_just_after_a_reconnection_is_dropped_without_a_record(
    live: _Live,
) -> None:
    device = await live.connected()
    original = await device.send_heartbeat()
    await live.log.wait_until(lambda: any(f.id == original.id for f in live.log.frames))

    await device.transport.send(original)  # An outbox replay, as a reconnecting device does.
    await live.marker(device)

    assert await live.stand.events("guard.replay_refused") == []


async def test_a_silent_link_is_marked_unreachable_by_the_liveness_sweep(live: _Live) -> None:
    device = await live.connected()

    live.stand.clock.advance(61.0)  # Past four missed beats; the sweep wakes on the fake clock.
    await live.log.wait_until(lambda: bool(live.log.offline))

    node = await live.stand.registry.node(device.party.device_id)
    assert node is not None and node.status is NodeStatus.UNREACHABLE
    assert [reason for _node, reason in live.log.offline] == ["missed_heartbeats"]
    await device.send_heartbeat()  # Proof of life brings it straight back.
    await live.log.wait_until(lambda: len(live.log.online) == 2)
    assert (await live.stand.events("swarm.online"))[-1].payload["reason"] == "heartbeat_resumed"


async def test_disconnect_closes_a_live_link_and_reports_whether_there_was_one(
    live: _Live,
) -> None:
    device = await live.connected()

    assert await live.listener.disconnect(device.party.device_id) is True
    assert await device.closed()
    assert await live.listener.disconnect(device.party.device_id) is False
    assert await live.listener.disconnect(make_device_party(live.stand.clock).device_id) is False


async def test_stopping_ends_every_connection_without_marking_devices_unreachable(
    live: _Live,
) -> None:
    device = await live.connected()

    await live.listener.stop()

    assert await device.closed()
    node = await live.stand.registry.node(device.party.device_id)
    assert node is not None and node.status is NodeStatus.ONLINE
    assert live.log.offline == []


async def test_a_device_wardens_connection_is_handed_to_the_bound_handler(live: _Live) -> None:
    handler = WardenHandlerLog()
    live.listener.bind_warden_handler(handler)
    party = await live.enrolled()
    device = await live.dial(party)

    ready = await device.send(make_cell_ready(party), sender=party.warden_id)
    async with asyncio.timeout(5.0):
        await handler.handed.wait()

    assert (
        handler.seen[0].first.id == ready.id and handler.seen[0].node.device_id == party.device_id
    )
    assert live.listener.hub is live.stand.hub


async def test_the_listener_exposes_the_voice_and_the_recorder_it_was_built_with(
    live: _Live,
) -> None:
    assert live.listener.voice == live.stand.voice
    assert live.listener.recorder is live.stand.recorder  # What the gate records its events with.


async def test_a_wardens_url_is_where_the_listener_listens_when_none_is_advertised(
    live: _Live,
) -> None:
    assert live.listener.warden_url == live.listener.uri  # The address actually bound.


async def test_a_wardens_url_is_the_advertised_one_when_there_is_one() -> None:
    stand = make_hive_rig()
    config = ListenerConfig(
        host="127.0.0.1",
        port=0,
        tls_cert=None,
        tls_key=None,
        liveness=LivenessRules(4),
        advertise_url="wss://hive.example:8443",
    )

    listener = stand.listener(config)

    assert listener.warden_url == "wss://hive.example:8443"  # Needs no start: it is configured.


async def test_bytes_that_are_not_an_envelope_close_that_connection_and_the_listener_serves_on(
    live: _Live,
) -> None:
    async with connect(live.listener.uri) as raw:
        await raw.send(b"this is not an envelope")
        with pytest.raises(ConnectionClosed):
            async with asyncio.timeout(5.0):
                await raw.recv()

    device = await live.connected()

    assert device.transport.is_connected
    assert await live.stand.events("guard.frame_ceiling") == []
