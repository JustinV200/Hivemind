"""Tests for hivemind.swarm.gateway.hub: which devices are connected, and who hears about them.

Fits into the Hive:
    Mirrors src/hivemind/swarm/gateway/hub.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.hub for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from builders.swarm import RawLink, make_raw_link, make_swarm_node

from hivemind.swarm.errors import GatewayUnavailableError
from hivemind.swarm.gateway import DeviceLink, GatewayHub
from hivemind.swarm.models import SwarmNode
from waggle.clock import FakeClock
from waggle.envelope import Envelope


@pytest.fixture
async def raw() -> AsyncIterator[RawLink]:
    """A started link; the node it belongs to is built by `_node`."""
    link = await make_raw_link()
    yield link
    await link.close()


def _node(raw: RawLink) -> SwarmNode:
    """The enrolled node of the raw link's device."""
    return make_swarm_node(raw.party, raw.clock)


class _Recorder:
    """A hub listener that keeps what it was told, in order, under a name."""

    def __init__(self, name: str, log: list[str]) -> None:
        self.name, self.log = name, log

    async def on_online(self, node: SwarmNode, link: DeviceLink) -> None:
        self.log.append(f"{self.name}:online")

    async def on_offline(self, node: SwarmNode, reason: str) -> None:
        self.log.append(f"{self.name}:offline:{reason}")

    async def on_frame(self, node: SwarmNode, envelope: Envelope) -> None:
        self.log.append(f"{self.name}:frame")


class _Failing(_Recorder):
    """A listener with a bug."""

    async def on_online(self, node: SwarmNode, link: DeviceLink) -> None:
        raise RuntimeError("a listener bug")


async def test_an_attached_open_link_is_found_by_device_and_by_cell(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)

    previous = await hub.attach(node, raw.link)

    assert previous is None
    assert hub.link(node.device_id) is raw.link
    assert hub.link_for_cell(node.cell_id) is raw.link
    assert hub.node(node.device_id) == node
    assert hub.entries() == ((node, raw.link),)


async def test_nothing_is_found_for_a_device_that_never_attached(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)

    assert hub.link(node.device_id) is None
    assert hub.link_for_cell(node.cell_id) is None
    assert hub.node(node.device_id) is None
    assert hub.attached_at(node.device_id) is None
    assert hub.attached_at_for_node(node.node_id) is None


async def test_a_link_that_has_closed_is_not_returned(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    await hub.attach(node, raw.link)

    await raw.link.close()

    assert hub.link(node.device_id) is None
    assert hub.link_for_cell(node.cell_id) is None


async def test_a_second_link_replaces_the_first_and_hands_it_back(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    second = await make_raw_link()
    try:
        await hub.attach(node, raw.link)

        replaced = await hub.attach(node, second.link)

        assert replaced is raw.link
        assert hub.link(node.device_id) is second.link
    finally:
        await second.close()


async def test_attaching_the_same_link_again_replaces_nothing(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    await hub.attach(node, raw.link)

    assert await hub.attach(node, raw.link) is None


async def test_detaching_a_stale_link_after_a_reconnect_forgets_nothing(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    second = await make_raw_link()
    try:
        await hub.attach(node, raw.link)
        await hub.attach(node, second.link)

        assert hub.detach(node.device_id, raw.link) is False
        assert hub.link(node.device_id) is second.link
        assert hub.detach(node.device_id, second.link) is True
        assert hub.link(node.device_id) is None
        assert hub.detach(node.device_id, second.link) is False
    finally:
        await second.close()


async def test_the_hub_remembers_when_a_link_attached(raw: RawLink) -> None:
    clock = FakeClock()
    hub, node = GatewayHub(clock), _node(raw)
    clock.advance(30.0)

    await hub.attach(node, raw.link)

    assert hub.attached_at(node.device_id) == clock.now()
    assert hub.attached_at_for_node(node.node_id) == clock.now()
    assert hub.attached_at_for_node(raw.hive.node_id) is None


async def test_update_node_refreshes_an_attached_device_and_ignores_a_stranger(
    raw: RawLink,
) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    await hub.attach(node, raw.link)
    relabelled = node.model_copy(update={"label": "desk"})

    hub.update_node(relabelled)
    hub.update_node(make_swarm_node())

    assert hub.node(node.device_id) == relabelled
    assert len(hub.entries()) == 1


async def test_wait_for_returns_at_once_for_a_connected_device(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    await hub.attach(node, raw.link)

    assert await hub.wait_for(node.device_id, 1.0) is raw.link


async def test_wait_for_wakes_when_the_device_attaches(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    waiting = asyncio.ensure_future(hub.wait_for(node.device_id, 5.0))
    await asyncio.sleep(0.01)

    await hub.attach(node, raw.link)

    assert await waiting is raw.link


async def test_wait_for_gives_up_after_its_deadline(raw: RawLink) -> None:
    with pytest.raises(GatewayUnavailableError):
        await GatewayHub(raw.clock).wait_for(_node(raw).device_id, 0.05)


async def test_wait_for_notices_a_link_that_ended_as_it_attached(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    await raw.link.close()
    waiting = asyncio.ensure_future(hub.wait_for(node.device_id, 0.2))
    await asyncio.sleep(0.01)

    await hub.attach(node, raw.link)

    with pytest.raises(GatewayUnavailableError):
        await waiting


async def test_listeners_hear_everything_in_subscription_order(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    log: list[str] = []
    first, second = _Recorder("a", log), _Recorder("b", log)
    hub.subscribe(first)
    hub.subscribe(second)
    sent = await raw.send_heartbeat()

    await hub.announce_online(node, raw.link)
    await hub.announce_frame(node, sent)
    await hub.announce_offline(node, "link_lost")

    assert log == [
        "a:online",
        "b:online",
        "a:frame",
        "b:frame",
        "a:offline:link_lost",
        "b:offline:link_lost",
    ]


async def test_an_unsubscribed_listener_hears_nothing_and_a_stranger_is_ignored(
    raw: RawLink,
) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    log: list[str] = []
    listener = _Recorder("a", log)
    hub.subscribe(listener)

    hub.unsubscribe(listener)
    hub.unsubscribe(listener)
    await hub.announce_online(node, raw.link)

    assert log == []


async def test_a_listeners_exception_reaches_the_caller(raw: RawLink) -> None:
    hub, node = GatewayHub(raw.clock), _node(raw)
    log: list[str] = []
    hub.subscribe(_Failing("bad", log))
    hub.subscribe(_Recorder("after", log))

    with pytest.raises(RuntimeError, match="a listener bug"):
        await hub.announce_online(node, raw.link)

    assert log == []
