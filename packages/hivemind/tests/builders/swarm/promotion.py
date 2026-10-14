"""Build a colonization scenario: a Level 0 device with work running on it, asking to be promoted.

Every test of the colonize flow starts from the same world. A device is on Level 0 with a real
Warden on the Hive Stand and one task RUNNING on it; its packet is scripted; the operator has asked
for the next rung (`desired_level`). When the packet answers START it makes the device's own
Warden dial the gate's door, exactly as the packet's process would. `PromotionScenario` builds
that world and offers the verbs a test needs: write the running task's Handoff, ask the colonizer
to carry the request out, wait for the flow to end, and look at the device as the registry now
holds it. What differs between tests is the packet's script (which step fails, which stays
silent), set on `scenario.packet` before `start`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the colonize flow tests
    under tests/unit/queen/swarm_gate/colonize.

Key invariants:
    - The device's Warden dials only when the packet answers START, so a test that scripts START
      to fail or to stay silent never sees a Warden arrive.
    - `close` ends the packet and every dialled Warden, whatever a test did.

See Also:
    - builders.swarm.colonizing for the packet and the dial this is built from.
    - hivemind.queen.swarm_gate.colonize for the flows under test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from builders.memory import make_handoff
from builders.quarantine import settle
from builders.swarm.colonizing import (
    DialledWarden,
    PlacedDevice,
    ScriptedPacket,
    dial_warden,
    place_on_device,
)
from builders.swarm.gate import GateRig

from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.swarm import RuntimeLevel, SwarmNode
from waggle.ids import DeviceId
from waggle.messages import HandoffRef
from waggle.messages.swarm import Colonize

__all__ = ["PromotionScenario", "make_promotion"]

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take; a stop alone is thousands.


@dataclass
class PromotionScenario:
    """A Level 0 device, a task running on it, a scripted packet, and the request to climb."""

    rig: GateRig
    placed: PlacedDevice
    packet: ScriptedPacket
    ref: HandoffRef | None = None
    dialled: list[DialledWarden] = field(default_factory=list)

    @property
    def device_id(self) -> DeviceId:
        """The device's id."""
        return self.placed.node.device_id

    async def node(self) -> SwarmNode:
        """The device as the registry holds it now."""
        row = await self.rig.hive.registry.node(self.placed.node.device_id)
        assert row is not None
        return row

    async def dial(self, order: Colonize) -> None:
        """Make the device's own Warden dial the gate's door, as the packet's process does."""
        self.dialled.append(await dial_warden(self.rig, await self.node()))

    async def checkpoint(self) -> HandoffRef:
        """Write the running task's Handoff into the Queen's store, as its bee would have."""
        deps = self.rig.queen_deps
        ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
        handoff = make_handoff(deps.clock, written_by=self.placed.node.warden_id)
        self.ref = await write_checkpoint(handoff, self.placed.task.id, ctx)
        return self.ref

    async def start(self) -> bool:
        """Ask the colonizer to carry out the device's pending request; whether a flow began."""
        return await self.rig.colonizer.request(await self.node())

    async def finish(self) -> None:
        """Wait until no flow owns the device any more."""
        await settle(lambda: not self.rig.colonizer.busy(self.device_id), _FLOW_ROUNDS)

    async def close(self) -> None:
        """End the packet and every Warden that dialled."""
        await self.packet.close()
        for warden in self.dialled:
            await warden.close()


async def make_promotion(rig: GateRig) -> PromotionScenario:
    """Build the scenario inside an open `rig.serving()`: a Level 0 device asking for Level 1.

    Args:
        rig: The gate rig, serving.

    Returns:
        The scenario: the device Level 0 with a running task, its packet answering as scripted
        (PREPARE, START and WITHDRAW all succeed), a Warden that dials when START is answered, and
        `desired_level` set to COLONIZED.
    """
    placed = await place_on_device(rig, RuntimeLevel.GATEWAY)
    packet = ScriptedPacket(placed.device, rig.clock, rig.hive.voice.hive_id)
    scenario = PromotionScenario(rig, placed, packet)
    packet.on_start = scenario.dial
    await rig.hive.registry.set_desired_level(placed.node.device_id, RuntimeLevel.COLONIZED)
    return scenario
