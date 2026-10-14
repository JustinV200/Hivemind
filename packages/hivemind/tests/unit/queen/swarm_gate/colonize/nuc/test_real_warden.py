"""Tests for the Queen's Nuc flows against a real device Warden: the two ends agree on the frames.

The other tests in this directory script the device's Warden, and `tests/unit/wardens/ticks/nuc`
scripts the Queen; this one connects the two real halves. A real `Warden` (the class, with its
autopilot, its tick handlers and its `ModelServerHost` over a fake terminal) is dialled into the
gate's door as a colonized device's own Warden is, and the Queen's `NucPromotion` and
`NucDemotion` run against it: she sets its ceilings, orders the server, hears the answer correlated
to her order and moves the rung; and on the way down the server stops and the rung moves back.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/nuc (codingrules section 3) and
    src/hivemind/wardens/ticks/nuc, from the one place both can be seen: the wire between them.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc for the Queen's flows.
    - hivemind.wardens.ticks.nuc for the Warden's handling of the order.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from builders.cells import make_capabilities, make_cell
from builders.forage import make_source
from builders.hosting import PROVIDER_NAME, RegistryDoor, make_server_config
from builders.quarantine import settle
from builders.swarm import GateRig, capacity_report_of, dial_warden, gpu_capacity
from builders.swarm.colonizing import DialledWarden
from builders.swarm.nuc import GIB
from builders.wardens import make_warden_deps

from hivemind.forage import ForageMap
from hivemind.llm import FakeLLMProvider
from hivemind.swarm import RuntimeLevel, SwarmNode
from hivemind.wardens import DeviceDeps, Warden
from waggle.envelope import Hop, wrap

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.
_STOP_S = 5.0  # A Warden that never stops fails the test instead of hanging it.


@dataclass
class RealNuc:
    """A colonized device whose own Warden is the real class, dialled into the gate's door."""

    node: SwarmNode
    warden: Warden
    dialled: DialledWarden
    run_task: asyncio.Task[None]
    forage_map: ForageMap

    async def close(self) -> None:
        """Stop the Warden, wait for its loop to end, and close its connection."""
        await self.warden.stop()
        await asyncio.wait_for(self.run_task, timeout=_STOP_S)
        await self.dialled.close()


async def _real_nuc(rig: GateRig) -> RealNuc:
    """Enrol a colonized device that asks for the Nuc rung and dial its real Warden in."""
    node = await rig.enrol(level=RuntimeLevel.COLONIZED, capacity=gpu_capacity(16.0))
    await rig.connect(node)
    await rig.hive.registry.set_desired_level(node.device_id, RuntimeLevel.NUC)
    row = await rig.hive.registry.node(node.device_id)
    assert row is not None
    dialled = await dial_warden(rig, row)
    await settle(lambda: len(rig.queen.wardens) == 1)
    # The machine's models: what its manifest lists for the server, none loaded until she asks.
    sources = tuple(
        make_source(
            source_id=name, provider=PROVIDER_NAME, model=f"{name}-model", vram_bytes_required=gib
        )
        for name, gib in (("small", 4 * GIB), ("large", 12 * GIB))
    )
    cell = make_cell(
        id=row.cell_id, clock=rig.clock, capabilities=make_capabilities(can_host_model=True)
    )
    forage_map = ForageMap((), clock=rig.clock)
    door = RegistryDoor(FakeLLMProvider(name=PROVIDER_NAME))
    deps, _unused, _id = make_warden_deps(
        rig.clock,
        cells=(cell,),
        warden_id=row.warden_id,
        queen_link=dialled.transport,
        hop=Hop(sender=row.warden_id, recipient=rig.hive.voice.hive_id, node_id=row.node_id),
        servers={"box": make_server_config(sources=sources, load_on_start=False)},
        map=forage_map,
        register_provider=door.register,
        unregister_provider=door.unregister,
        device=DeviceDeps(release_lease_when_idle=False),
    )
    warden = Warden(row.warden_id, deps)
    await warden.start()  # It holds its Cell and runs no server: a colonized device, not a Nuc.
    run_task = asyncio.ensure_future(warden.run())
    # What the device's announcement carries, which the door's events hand to the colonizer.
    report = wrap(capacity_report_of(row, "box"), deps.hop, clock=rig.clock)
    await dialled.transport.send(report)
    await settle(lambda: rig.colonizer.declared(row.device_id) == ("box",))
    return RealNuc(row, warden, dialled, run_task, forage_map)


async def _flow(rig: GateRig, nuc: RealNuc) -> None:
    """Ask for the rung and wait for the flow that answers the request to end."""
    assert await rig.colonizer.request(nuc.node)
    await settle(lambda: not rig.colonizer.busy(nuc.node.device_id), _FLOW_ROUNDS)


async def test_a_real_warden_starts_its_server_on_the_queens_order_and_the_rung_moves(
    rig: GateRig,
) -> None:
    async with rig.serving():
        nuc = await _real_nuc(rig)
        assert nuc.warden.hosted_servers == {}  # Level 1: nothing runs until she asks.

        await _flow(rig, nuc)

        row = await rig.hive.registry.node(nuc.node.device_id)
        assert row is not None and row.level is RuntimeLevel.NUC and row.desired_level is None
        assert set(nuc.warden.hosted_servers) == {"box"}  # Her order started the server ...
        assert {s.source_id for s in nuc.warden.hosted_servers["box"].sources} == {"small", "large"}
        assert {s.source_id for s in nuc.forage_map.sources()} == {"small", "large"}  # ... and
        ceilings = nuc.warden._ceilings  # the ceilings she sent first bound it.
        assert ceilings is not None and ceilings.model_vram_bytes == 16 * GIB
        assert ceilings.loadable_sources == ("small", "large")
        [promoted] = await rig.hive.events("swarm.promoted")
        assert promoted.payload["to_level"] == "NUC"
        await nuc.close()


async def test_a_real_warden_stops_its_server_on_the_demotion_and_the_rung_moves_back(
    rig: GateRig,
) -> None:
    async with rig.serving():
        nuc = await _real_nuc(rig)
        await _flow(rig, nuc)
        await rig.hive.registry.set_desired_level(nuc.node.device_id, RuntimeLevel.COLONIZED)
        row = await rig.hive.registry.node(nuc.node.device_id)
        assert row is not None

        assert await rig.colonizer.request(row)
        await settle(lambda: not rig.colonizer.busy(nuc.node.device_id), _FLOW_ROUNDS)

        after = await rig.hive.registry.node(nuc.node.device_id)
        assert after is not None and after.level is RuntimeLevel.COLONIZED
        assert nuc.warden.hosted_servers == {} and nuc.forage_map.sources() == ()
        [demoted] = await rig.hive.events("swarm.demoted")
        assert demoted.payload["to_level"] == "COLONIZED"
        await nuc.close()
