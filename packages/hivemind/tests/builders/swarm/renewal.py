"""Build a terms renewal scenario: a colonized device at work whose terms the operator changes.

A colonized device's Warden holds the terms it was started under, so when the operator changes them
the Queen has its packet stop that Warden and start it again. The scenario is the whole device: a
colonized (or Nuc) device with its own Warden dialled into the gate's door, a task running on it,
and a scripted packet that behaves as the real one does when the Queen orders the restart. On
WITHDRAW the old Warden's connection ends, as its process does when the packet stops it; on START
a new Warden dials in under the same id, started under whatever terms the registry holds at that
moment, as a packet that was told them would build it. A test changes the script (a refused
WITHDRAW, a Warden that never arrives, one that comes up under the old terms) to see the renewal
fall back, and reads the Wardens the packet started off `started`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the terms renewal tests
    under tests/unit/queen/swarm_gate/colonize/renew.

Key invariants:
    - A new Warden dials only when the packet answers START, so a test that scripts START to fail
      or to stay silent never sees one arrive.
    - A new Warden is started under the terms the registry holds when START is answered, unless
      `stale_terms` is set, which makes it come up under the terms the device had before, and
      answers an order to start its model server as `server` says.
    - `close` ends the packet and every Warden, whatever a test did.

See Also:
    - builders.swarm.colonizing for the scripted packet and the dial this is built from.
    - builders.swarm.nuc for the scripted device Warden.
    - hivemind.queen.swarm_gate.colonize.renew for the flow under test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from builders.isolation import place_running
from builders.quarantine import settle, settle_for
from builders.swarm.colonizing import ScriptedPacket, dial_warden
from builders.swarm.gate import GateRig
from builders.swarm.nuc import NucDevice, ScriptedNucWarden, make_nuc_device

from hivemind.brood_chamber import Task
from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm import SwarmNode
from hivemind.swarm.operator import OperatorDesk
from waggle.messages.swarm import Colonize, ColonizeAction, NucAction, NucOutcome

__all__ = ["RenewalScenario", "make_renewal"]

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole renewal may take.


@dataclass
class RenewalScenario:
    """A colonized device with a task running, its packet scripted and its Warden dialled in."""

    rig: GateRig
    device: NucDevice
    packet: ScriptedPacket
    task: Task
    first: ScriptedNucWarden  # The Warden that was running before the change.
    started: list[ScriptedNucWarden] = field(default_factory=list)  # Those the packet started.
    stale_terms: bool = False  # Whether a Warden it starts comes up under the terms before.
    server: NucOutcome = NucOutcome.PROMOTED  # What a Warden it starts answers to PROMOTE.
    terms_before: SwarmNode | None = None

    async def node(self) -> SwarmNode:
        """The device as the registry holds it now."""
        row = await self.rig.hive.registry.node(self.device.node.device_id)
        assert row is not None
        return row

    async def narrow(self, level: AccessLevel = AccessLevel.SCRATCH) -> SwarmNode:
        """The operator changes the device's access level (`hive swarm access`)."""
        self.terms_before = await self.node()
        desk = OperatorDesk(self.rig.hive.registry, self.rig.hive.recorder)
        return (await desk.set_access(self.terms_before, level)).node

    async def reshield(self, tier: CombShieldLevel = CombShieldLevel.PROPOLIS) -> SwarmNode:
        """The operator changes the device's Comb Shield tier (`hive swarm shield`)."""
        self.terms_before = await self.node()
        desk = OperatorDesk(self.rig.hive.registry, self.rig.hive.recorder)
        return (await desk.set_shield(self.terms_before, tier)).node

    async def renew(self) -> bool:
        """Ask the colonizer to renew the device's Warden; whether a flow began."""
        return await self.rig.colonizer.renew(await self.node())

    async def finish(self) -> None:
        """Wait until no flow owns the device any more (in real time: a packet may be silent)."""
        await settle_for(lambda: not self.rig.colonizer.busy(self.device.node.device_id))

    async def stop_old(self, order: Colonize) -> None:
        """End the running Warden's connection, as its process does when the packet stops it."""
        await self.device.warden.drop()

    async def start_new(self, order: Colonize) -> None:
        """Dial in a new Warden under the same id, as the process the packet started would."""
        fresh = await self.node()
        before = self.terms_before
        if self.stale_terms and before is not None:
            old = before.access_level.to_wire(), before.comb_shield.to_wire()
            dialled = await dial_warden(self.rig, fresh, access_level=old[0], comb_shield=old[1])
        else:
            dialled = await dial_warden(self.rig, fresh)
        warden = ScriptedNucWarden(dialled, fresh, self.rig.clock, self.rig.hive.voice.hive_id)
        warden.outcomes[NucAction.PROMOTE] = self.server
        self.started.append(warden)
        self.device.warden = warden
        await warden.declare("box")

    async def close(self) -> None:
        """End the packet and every Warden."""
        await self.packet.close()
        for warden in self.started:
            await warden.close()
        await self.first.close()


async def make_renewal(rig: GateRig, *, nuc: bool = False) -> RenewalScenario:
    """Build the scenario inside an open `rig.serving()`: a colonized device with work on it.

    Args:
        rig: The gate rig, serving, with the gate's numbers allowing the server `box`.
        nuc: Whether the device is a Nuc (the Queen promoted it) rather than a colonized one.

    Returns:
        The scenario: a FULL device on the Meadow whose Warden declared `box`, a task running on
        that Warden, and a packet that answers WITHDRAW by ending the Warden's connection and
        START by dialling a new Warden in.
    """
    device = await make_nuc_device(rig, "box")
    node = device.node
    if nuc:
        assert await rig.colonizer.request(node)
        await settle(lambda: not rig.colonizer.busy(node.device_id), _FLOW_ROUNDS)
    else:
        await rig.hive.registry.set_desired_level(node.device_id, None)
    link = rig.queen.wardens[0]
    task = await place_running(rig.queen_deps, link)
    packet = ScriptedPacket(device.packet, rig.clock, rig.hive.voice.hive_id)
    scenario = RenewalScenario(rig, device, packet, task, device.warden)
    packet.before[ColonizeAction.WITHDRAW] = scenario.stop_old
    packet.on_start = scenario.start_new
    return scenario
