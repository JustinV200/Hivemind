"""End-to-end test of the Basket's uplink from a colonized device: its Warden ships a blob up.

The Hive is built from a manifest with `[swarm] enabled = true`, on loopback with no TLS, exactly
as `hive run` builds it; the operator's act is the real one (the door mints an invite and a real
`pollen` packet enrols with it over a real WebSocket, so the registry pins the device's node key).
The device is then put on the COLONIZED rung, as a colonization that already happened leaves it,
and its own Warden dials the listener's Warden door with that node key, over a real socket, and
announces its Cell with the frames `hivemind.cli.in_cell.borrowed` sends. It ships a blob up with
a manifest signed by the node key, as a `retain` result or a scaffolded cell-scope tool package
is. The Queen must verify that signature against the key the registry pinned, re-deposit the
blob under the Hive's own signature with the link's provenance (the device's Cell, borrowed), and
acknowledge it complete; a manifest signed by a key the registry never pinned must be refused as
an unknown signer on the same link.

What is still unproven here: the real `cli.in_cell.borrowed` Warden process running on a device
(the phase 11 container scenarios, which need a docker daemon), and so the Warden-side half of the
shipment (`hivemind.wardens.ticks.basket.send`, exercised by its own unit tests). This scenario
plays that side with the same `TransferSender` and the device's real node key.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/bug-review-2026-10-16.md, queen F2, for the failure this guards against.
    - e2e/swarm/basket.py for the device Warden line this drives.
    - hivemind.queen.ticks.tools.transfer.receive for the Queen's side.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from e2e.scripted_openai import free_port
from e2e.swarm.basket import DeviceWardenLine, device_warden
from e2e.swarm.device import enrol
from e2e.swarm.hive import enrolled_node, swarm_hive, trail_events

from hivemind.cell import AccessLevel, CombShieldLevel, HoneyClearance
from hivemind.cli.compose import Hive, run_hive
from waggle.ids import new_node_id
from waggle.messages.basket import BasketAck, BasketRefusal, BasketRefuse
from waggle.messages.swarm import RuntimeLevel
from waggle.signing import Ed25519Signer

pytestmark = pytest.mark.e2e

_CONTENT = bytes(range(256)) * 1_100  # 281_600 bytes: a full 256 KiB chunk and a short one.


def test_a_colonized_devices_warden_ships_a_blob_up_and_the_queen_deposits_it(
    tmp_path: Path,
) -> None:
    hive = swarm_hive(tmp_path, free_port())

    asyncio.run(_scenario(hive, tmp_path / "device"))


async def _scenario(hive: Hive, data_dir: Path) -> None:
    """Enrol a device, colonize it on paper, dial in as its Warden and ship a blob up."""
    assert hive.swarm is not None and hive.royal_jelly is not None
    async with run_hive(hive):
        invite = await hive.swarm.door.mint_invite(
            AccessLevel.FULL, CombShieldLevel.MEADOW, label="e2e laptop"
        )
        device = await enrol(invite.invite_string, data_dir, hive.clock)
        enrolled = await enrolled_node(hive)
        # The registry's view after a colonization: the Warden lives on the device from here on.
        node = await hive.swarm.registry.set_level(enrolled.device_id, RuntimeLevel.COLONIZED)
        async with device_warden(hive, device, node) as warden:
            last = await warden.ship(_CONTENT, "an artefact a Worker kept on the device")

            assert isinstance(last, BasketAck) and last.complete, last
            await _assert_deposited_with_the_links_provenance(hive, node.cell_id, node.warden_id)
            await _assert_a_stranger_is_still_refused(warden)


async def _assert_deposited_with_the_links_provenance(
    hive: Hive, cell_id: str, warden_id: str
) -> None:
    """The Basket holds the blob under the Hive's signature, from the device's borrowed Cell."""
    assert hive.royal_jelly is not None
    basket = hive.royal_jelly.basket
    [received] = await trail_events(hive, "basket.received")
    sha256 = str(received.payload["sha256"])
    blob = await basket.get(sha256)
    assert blob.origin_cell_id == cell_id and blob.origin_bee == warden_id
    # The Cell is borrowed, which the Queen knows from the link: the declared C0 was raised to C2.
    assert blob.clearance is HoneyClearance.C2
    assert await basket.open_bytes(sha256) == _CONTENT
    assert (await basket.manifest(sha256)).signer == hive.royal_jelly.signer_id
    assert [e.kind for e in await trail_events(hive, "basket.deposited")] == ["basket.deposited"]
    assert await trail_events(hive, "basket.refused") == []


async def _assert_a_stranger_is_still_refused(warden: DeviceWardenLine) -> None:
    """On the same link, a manifest signed by a key the registry never pinned is not that node's."""
    stranger = (str(new_node_id(warden.hive.clock)), Ed25519Signer.generate())
    last = await warden.ship(b"a stranger's artefact", "signed by a stranger", signer=stranger)

    assert isinstance(last, BasketRefuse) and last.code is BasketRefusal.UNKNOWN_SIGNER
