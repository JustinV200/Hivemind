"""Play a colonized device's Warden on its Basket side, over a real socket to the Hive's listener.

A device above Level 0 runs its own Warden (`hivemind.cli.in_cell.borrowed`), which dials the
swarm listener's Warden door with the device's node key, announces its Cell, and ships every blob it
keeps up to the Queen with a manifest signed by that same key (ADR-0048). This module is that
Warden reduced to what the shipment needs: `DeviceWardenLine` dials the listener exactly as
`DeviceDialer` does (the device's key from the packet's data directory, the Hive's key from its
enrolment state, one attempt), announces the Cell with the very frames `DeviceAnnouncer` sends
(`hivemind.cli.in_cell.link`), keeps the Queen's liveness check fed with Warden Heartbeats, and
`ship`s a blob with the real `TransferSender`, answering each ack with the next chunk until the
Queen's last word. No Worker, no lease, no trail: the scenario is the Basket's uplink alone.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by `tests/e2e/swarm/
    test_basket_uplink.py`; depends on `hivemind.basket`, `hivemind.cli.in_cell.link`, the e2e
    device scaffolding (`e2e.swarm.device`) and waggle.

Key invariants:
    - The connection is signed with the node key the packet enrolled with and verified against
      the Hive key the packet pinned, as the real device Warden's is; nothing is handed over out
      of band.
    - The manifest of a shipped blob is signed by the node key (or any key a test names), never
      by the Hive's: whether the Queen accepts it is the question under test.
    - `close` ends every task started here and the connection, whatever a test did.

See Also:
    - hivemind.cli.in_cell.borrowed.dial for the dialer and announcer this mirrors.
    - hivemind.queen.ticks.tools.transfer.receive for the Queen's side of the shipment.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from builders.basket import make_blob
from builders.supervision import make_telemetry
from e2e.swarm.device import EnrolledDevice

from hivemind.basket import (
    BlobManifest,
    TransferSender,
    ack_from_wire,
    chunk_to_wire,
    offer_to_wire,
)
from hivemind.basket.transfer import BytesChunkSource
from hivemind.cell import HoneyClearance, hive_stand_cell_id
from hivemind.cli.compose import Hive
from hivemind.cli.in_cell import link as cell_link
from hivemind.swarm import SwarmNode, readable_roots
from pollen.state import load_signer
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.messages.base import WaggleMessage
from waggle.messages.basket import BasketAck, BasketRefuse
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.forage import CapacityTrigger
from waggle.messages.supervision import Heartbeat, WardenState
from waggle.signing import Ed25519Signer, Ed25519Verifier, public_key_from_hex
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport

__all__ = ["HEARTBEAT_S", "DeviceWardenLine", "device_warden"]

HEARTBEAT_S = 1.0  # The cadence the Warden declares; the Queen judges it by three of these.
REPLY_WAIT_S = 10.0  # The longest a shipment waits on any one word from the Queen.


@dataclass
class DeviceWardenLine:
    """A colonized device's Warden on the wire: its connection, ids and the Queen's replies."""

    hive: Hive
    device: EnrolledDevice
    node: SwarmNode
    signer: Ed25519Signer = field(init=False, repr=False)
    transport: WebSocketClientTransport = field(init=False)
    hop: Hop = field(init=False)
    _replies: asyncio.Queue[Envelope] = field(default_factory=asyncio.Queue, init=False)
    _tasks: list[asyncio.Task[None]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        """Build the transport as `DeviceDialer` does: the node key signs, the Hive key verifies."""
        state = self.device.state
        self.signer = load_signer(self.device.layout.key)
        verifier = Ed25519Verifier({state.hive_node_id: public_key_from_hex(state.hive_key_hex)})
        codec = Codec(signer=self.signer, verifier=verifier)
        options = DialOptions(max_attempts=1, tls_pin_sha256=state.tls_pin_sha256)
        self.transport = WebSocketClientTransport(
            state.queen_url, codec, self.hive.clock, options=options
        )
        self.hop = Hop(
            sender=self.node.warden_id, recipient=state.hive_id, node_id=self.node.node_id
        )

    async def start(self) -> None:
        """Dial, announce the Cell as the borrowed Warden does, and feed the Queen's liveness."""
        await self.transport.connect()
        cell = self.node.as_cell()
        deps = cell_link.CellLinkDeps(
            transport=self.transport,
            cell=cell,
            warden_id=self.node.warden_id,
            hive_id=self.device.state.hive_id,
            node_id=self.node.node_id,
            clock=self.hive.clock,
            heartbeat_interval_s=HEARTBEAT_S,
            runtime_version=self.node.runtime_version,
            watch_roots=readable_roots(self.node),
        )
        # The three frames `DeviceAnnouncer.announce` sends, in its order.
        await cell_link.send_cell_ready(deps)
        await cell_link.send_capacity_report(
            deps, cell.capacity, trigger=CapacityTrigger.RECONNECTED
        )
        await cell_link.send_cell_heartbeat(deps)
        self._tasks.append(asyncio.create_task(self._read(), name="device-warden-reader"))
        self._tasks.append(asyncio.create_task(self._beat(), name="device-warden-heartbeat"))

    async def ship(
        self, content: bytes, reason: str, signer: tuple[str, Ed25519Signer] | None = None
    ) -> BasketAck | BasketRefuse | ErrorMessage:
        """Offer `content` to the Queen, chunk it on her acks, and return her last word.

        Args:
            content: The blob's bytes.
            reason: Why it is shipped, as the offer carries it.
            signer: Who signs the manifest, as (signer id, key); the device's own node by default.

        Returns:
            The final `basket.ack` (complete) on success, else the `basket.refuse` or
            `control.error` the Queen answered with.
        """
        signer_id, signing = signer if signer is not None else (str(self.node.node_id), self.signer)
        # Declared C0: the Basket's floor for a borrowed Cell raises it, which the test reads.
        blob = make_blob(content, origin_cell_id=self.node.cell_id, clearance=HoneyClearance.C0)
        manifest = BlobManifest.sign(blob, signer_id, signing)
        sender = TransferSender(BytesChunkSource(content))
        stand = hive_stand_cell_id(self.hive.manifest.hive.node_id)
        offer = sender.offer(manifest, stand, reason)
        await self._send(offer_to_wire(offer))
        while True:
            payload = await self._basket_reply(offer.sha256)
            if not isinstance(payload, BasketAck):
                return payload
            chunks = sender.on_ack(ack_from_wire(payload))
            if chunks is None:
                return payload
            for chunk in chunks:
                await self._send(chunk_to_wire(chunk))

    async def close(self) -> None:
        """End the reader and the heartbeats, then the connection."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await self.transport.close()

    async def _send(self, payload: WaggleMessage) -> None:
        """Send one frame from the Warden to the Queen, stamped and signed as the device."""
        await self.transport.send(wrap(payload, self.hop, clock=self.hive.clock))

    async def _basket_reply(self, sha256: str) -> BasketAck | BasketRefuse | ErrorMessage:
        """Wait for the Queen's next word on the blob `sha256`; other frames are let by."""
        while True:
            async with asyncio.timeout(REPLY_WAIT_S):
                envelope = await self._replies.get()
            payload = envelope.payload
            if isinstance(payload, BasketAck | BasketRefuse) and payload.sha256 == sha256:
                return payload
            if isinstance(payload, ErrorMessage):
                return payload

    async def _read(self) -> None:
        """Queue every frame the Queen sends; the Warden's other orders are of no interest here."""
        async for envelope in self.transport.receive():
            await self._replies.put(envelope)

    async def _beat(self) -> None:
        """Send Warden Heartbeats at a fraction of the declared cadence, as a watching one does."""
        while True:
            await self._send(
                Heartbeat(
                    telemetry=make_telemetry(),
                    task_id=None,
                    worker_state=None,
                    warden_state=WardenState.WATCH,
                    children=(),
                    grant_id=None,
                    grant_spend=None,
                    interval_s=HEARTBEAT_S,
                )
            )
            await asyncio.sleep(HEARTBEAT_S / 4)


@contextlib.asynccontextmanager
async def device_warden(
    hive: Hive, device: EnrolledDevice, node: SwarmNode
) -> AsyncIterator[DeviceWardenLine]:
    """Run a colonized device's Warden line against `hive` for the length of the block.

    Args:
        hive: The running Hive whose listener the Warden dials.
        device: The enrolled device whose node key signs.
        node: The device as the registry holds it, on a rung whose Warden lives on the device.

    Yields:
        The line, connected and announced; closed when the block ends.
    """
    line = DeviceWardenLine(hive, device, node)
    await line.start()
    try:
        yield line
    finally:
        await line.close()
