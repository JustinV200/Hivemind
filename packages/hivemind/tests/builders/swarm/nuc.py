"""Build a colonized device with a scripted Warden for the Queen's Nuc flows to talk to.

Promoting a device to a Nuc is a conversation between the Queen and the device's own Warden: she
sets its ceilings, orders its model server started and waits for the answer. Neither the Warden
nor its server needs to be real to test the Queen's side. `ScriptedNucWarden` is the Warden's end
of a connection the gate's door admitted: it declares the model servers its manifest lists in a
capacity report, records every ceilings message, hosting plan and order the Queen sends (in the
order they arrive), and answers each `swarm.nuc_promote` from a script (the outcome per action,
the models it says it loaded, or silence). `make_nuc_device` builds the whole scene: a colonized
device that asks for the Nuc rung, its packet link, its Warden dialled in and declared.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    tests/unit/queen/swarm_gate that promote or demote a Nuc.

Key invariants:
    - A scripted Warden answers every order it is scripted to answer, correlated to it, on its
      own end of the very connection the gate's door serves; nothing here opens a socket.
    - `received` keeps what arrived in arrival order, one short label per frame, so a test can
      assert that the ceilings came before the order.
    - `close` ends the task started here, whatever a test did.

See Also:
    - builders.swarm.colonizing for `dial_warden`, which this builds on.
    - hivemind.queen.swarm_gate.colonize.nuc for the flows under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from builders.forage import make_capacity, make_host_capacity, make_source
from builders.quarantine import settle
from builders.swarm.colonizing import DialledWarden, dial_warden
from builders.swarm.gate import GateRig, IdleDevice, gate_terms
from builders.swarm.parties import DeviceParty, make_capacity_report

from hivemind.forage import ForageCapacity, GpuInfo
from hivemind.queen.swarm_gate import GateTerms
from hivemind.swarm import OfferedServer, SwarmNode
from waggle.clock import FakeClock
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import HiveId, MessageId
from waggle.messages.base import WaggleMessage
from waggle.messages.forage import (
    CapacityReport,
    CeilingsSet,
    ModelServerReport,
    PlanWritten,
)
from waggle.messages.supervision import Intervene
from waggle.messages.swarm import NucAction, NucOutcome, NucPromote, NucPromoted, RuntimeLevel
from waggle.messages.task import TaskAssign, TaskPause
from waggle.signing import Ed25519Signer

__all__ = [
    "GIB",
    "NucDevice",
    "ScriptedNucWarden",
    "capacity_frame",
    "capacity_report_of",
    "gpu_capacity",
    "make_nuc_device",
    "nuc_terms",
    "restart_nuc_warden",
]

GIB = 1024**3


def capacity_report_of(node: SwarmNode, *server_ids: str) -> CapacityReport:
    """The capacity report a device's Warden sends, naming the servers its manifest declares."""
    party = DeviceParty(
        device_id=node.device_id,
        node_id=node.node_id,
        cell_id=node.cell_id,
        warden_id=node.warden_id,
        signer=Ed25519Signer.generate(),
    )
    servers = tuple(ModelServerReport(server_id=name, sources=()) for name in server_ids)
    return make_capacity_report(party, model_servers=servers)


def capacity_frame(rig: GateRig, node: SwarmNode, *server_ids: str) -> Envelope:
    """Wrap the capacity report `node`'s Warden would send, as it arrives at the gate's door."""
    hop = Hop(sender=node.warden_id, recipient=rig.hive.voice.hive_id, node_id=node.node_id)
    return wrap(capacity_report_of(node, *server_ids), hop, clock=rig.clock)


def gpu_capacity(free_vram_gib: float) -> ForageCapacity:
    """A device capacity with one 24 GiB GPU that has `free_vram_gib` of it free."""
    gpu = GpuInfo(
        name="scripted gpu", vram_bytes=24 * GIB, vram_free_bytes=int(free_vram_gib * GIB)
    )
    return make_capacity(host=make_host_capacity(gpus=(gpu,)))


class ScriptedNucWarden:
    """The Warden of a colonized device, answering `swarm.nuc_promote` from a script."""

    def __init__(
        self, dialled: DialledWarden, node: SwarmNode, clock: FakeClock, hive: HiveId
    ) -> None:
        """Start reading the Warden's end of `dialled`'s connection.

        Args:
            dialled: A device's Warden connected to the gate's door; its transport is read here.
            node: The device the Warden supervises.
            clock: Mints the ids and times of everything it sends.
            hive: The Queen's address, the recipient of everything it sends.
        """
        self._dialled = dialled
        self._node = node
        self._clock = clock
        self._hop = Hop(sender=node.warden_id, recipient=hive, node_id=node.node_id)
        self.orders: list[NucPromote] = []
        self.ceilings: list[CeilingsSet] = []
        self.plans: list[PlanWritten] = []
        self.assignments: list[TaskAssign] = []  # The tasks the Queen gave it, resumed or new.
        self.pauses: list[TaskPause] = []  # The bees she had it stop.
        self.intervenes: list[Intervene] = []  # The levers she pulled (a Handoff before a pause).
        self.received: list[str] = []  # One label per frame, oldest first.
        self.outcomes: dict[NucAction, NucOutcome] = {
            NucAction.PROMOTE: NucOutcome.PROMOTED,
            NucAction.DEMOTE: NucOutcome.DEMOTED,
        }
        self.silent: set[NucAction] = set()  # Orders it never answers.
        self.loaded: tuple[str, ...] | None = (
            None  # What it says it loaded; None: the first models.
        )
        self.reply_level: RuntimeLevel = RuntimeLevel.COLONIZED  # What a FAILED reply reports.
        self.before: Callable[[NucPromote], Awaitable[None]] | None = None  # Before it answers.
        self._task = asyncio.create_task(self._run())

    @property
    def actions(self) -> list[NucAction]:
        """The actions ordered so far, oldest first."""
        return [order.action for order in self.orders]

    @property
    def is_connected(self) -> bool:
        """Whether the Warden's connection to the Queen's door is still open."""
        return self._dialled.transport.is_connected

    async def declare(self, *server_ids: str) -> None:
        """Send a capacity report naming the model servers this Warden's manifest declares."""
        await self._send(capacity_report_of(self._node, *server_ids))

    async def drop(self) -> None:
        """End the connection from the Warden's side, as a device that went away does."""
        await self._dialled.transport.close()

    async def close(self) -> None:
        """Stop reading the Warden's end and close the connection."""
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        await self._dialled.close()

    async def _run(self) -> None:
        """Read every frame the Queen sends the Warden; answer orders, note the rest."""
        async for envelope in self._dialled.transport.receive():
            payload = envelope.payload
            if isinstance(payload, NucPromote):
                self.received.append(f"order:{payload.action.value}")
                await self._answer(envelope, payload)
            elif isinstance(payload, CeilingsSet):
                self.received.append("ceilings")
                self.ceilings.append(payload)
            elif isinstance(payload, PlanWritten):
                self.received.append("plan")
                self.plans.append(payload)
            elif isinstance(payload, TaskAssign):
                self.received.append("assign")
                self.assignments.append(payload)
            elif isinstance(payload, TaskPause):
                self.received.append("pause")
                self.pauses.append(payload)
            elif isinstance(payload, Intervene):
                self.received.append("intervene")
                self.intervenes.append(payload)

    async def _answer(self, envelope: Envelope, order: NucPromote) -> None:
        """Record `order` and reply as scripted."""
        self.orders.append(order)
        if self.before is not None:
            await self.before(order)
        if order.action in self.silent:
            return
        await self._send(self._reply(order), envelope.id)

    def _reply(self, order: NucPromote) -> NucPromoted:
        """Build the `swarm.nuc_promoted` that reports the scripted outcome for `order`."""
        outcome = self.outcomes[order.action]
        promoted = outcome is NucOutcome.PROMOTED
        loaded = (
            (self.loaded if self.loaded is not None else order.initial_models) if promoted else ()
        )
        level = {
            NucOutcome.PROMOTED: RuntimeLevel.NUC,
            NucOutcome.DEMOTED: RuntimeLevel.COLONIZED,
            NucOutcome.FAILED: self.reply_level,
        }[outcome]
        return NucPromoted(
            cell_id=order.cell_id,
            device_id=order.device_id,
            action=order.action,
            outcome=outcome,
            level=level,
            server_id=order.server_id if promoted else None,
            loaded_models=loaded,
            reason="Scripted.",
        )

    async def _send(self, message: WaggleMessage, correlation_id: MessageId | None = None) -> None:
        """Send `message` to the Queen over this Warden's connection."""
        envelope = wrap(message, self._hop, clock=self._clock, correlation_id=correlation_id)
        await self._dialled.transport.send(envelope)


@dataclass
class NucDevice:
    """A colonized device asking for the Nuc rung, its packet up and its Warden dialled in."""

    node: SwarmNode
    packet: IdleDevice
    warden: ScriptedNucWarden

    async def close(self) -> None:
        """Close the scripted Warden's connection."""
        await self.warden.close()


def nuc_terms(**overrides: object) -> GateTerms:
    """The gate's numbers with one allowed server, `box`: `small` (4 GiB) and `large` (12 GiB)."""
    sources = (
        make_source(source_id="small", model="small-model", vram_bytes_required=4 * GIB, seats=2),
        make_source(source_id="large", model="large-model", vram_bytes_required=12 * GIB, seats=2),
    )
    return gate_terms(servers=(OfferedServer("box", sources),), **overrides)


async def make_nuc_device(
    rig: GateRig,
    *servers: str,
    free_vram_gib: float = 16.0,
    level: RuntimeLevel = RuntimeLevel.COLONIZED,
    wanted: RuntimeLevel = RuntimeLevel.NUC,
) -> NucDevice:
    """Enrol a device on `level` that asks for `wanted`, and bring its own Warden up.

    Args:
        rig: The gate rig; its door (`rig.bind()` or `rig.serving()`) admits the Warden.
        *servers: The model server ids its Warden declares in its capacity report.
        free_vram_gib: The VRAM the device's single GPU has free, in GiB.
        level: The rung the device is on: colonized to be promoted, the Nuc rung to be demoted.
        wanted: The rung the operator asked for; the Nuc rung when omitted.

    Returns:
        The device as the registry holds it, its connected packet and its declared Warden.
    """
    node = await rig.enrol(level=level, capacity=gpu_capacity(free_vram_gib))
    packet = await rig.connect(node)
    await rig.hive.registry.set_desired_level(node.device_id, wanted)
    row = await rig.hive.registry.node(node.device_id)
    assert row is not None
    dialled = await dial_warden(rig, row)
    await settle(lambda: len(rig.queen.wardens) == 1)
    warden = ScriptedNucWarden(dialled, row, rig.clock, rig.hive.voice.hive_id)
    await warden.declare(*servers)
    await settle(lambda: rig.colonizer.declared(row.device_id) == servers)
    return NucDevice(row, packet, warden)


async def restart_nuc_warden(rig: GateRig, device: NucDevice, *servers: str) -> ScriptedNucWarden:
    """Make the device's Warden restart: its connection ends and the same Warden dials in again.

    The new Warden is a fresh process, so it runs no model server and holds no ceilings until the
    Queen sends them again; what it did is not carried over. `device.warden` is replaced with it.

    Args:
        rig: The gate rig, whose door admits the Warden.
        device: The colonized device, with its first Warden dialled in.
        *servers: The model server ids the new Warden declares in its capacity report.

    Returns:
        The new Warden, dialled in under the same Warden id and declared.
    """
    old = rig.queen.wardens[0]
    await device.warden.close()
    row = await rig.hive.registry.node(device.node.device_id)
    assert row is not None
    dialled = await dial_warden(rig, row)
    await settle(lambda: rig.queen.wardens[0] is not old)  # The Queen swapped the link.
    warden = ScriptedNucWarden(dialled, row, rig.clock, rig.hive.voice.hive_id)
    await warden.declare(*servers)
    device.warden = warden
    return warden
