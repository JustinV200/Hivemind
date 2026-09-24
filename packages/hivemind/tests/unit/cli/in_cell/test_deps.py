"""Tests for hivemind.cli.in_cell.deps: build_in_cell_warden_deps, end to end with a real Warden.

Roadmap step 5.3's own deliverable: the in-Cell Warden is a genuine task-executing
`hivemind.wardens.warden.Warden`, not the old announce-and-heartbeat stand-in. This module proves
that by building a real `WardenDeps` the way `hivemind.cli.in_cell.main.run_in_cell_warden` does
(`InCellSpawnSource`, a signed `WebSocketClientTransport`, `build_in_cell_warden_deps`), scripting
the in-Cell provider registry's own `FakeLLMProvider` (`hivemind.cli.in_cell.providers`), and
driving a `GrantIssued` + `TaskAssign` for a trivial task (empty acceptance, one text-only
response) against a real `hivemind.workers.roles.Drone` over the loopback transport -- a
`TaskResult` comes back, and a Queen-sent `Shutdown` afterwards stops the Warden with every
sub-bee reaped. The Drone's model call passes through the Cell's own Fanner
(`hivemind.cli.in_cell.fanner`), so it is recorded as an `llm.call` on the Cell's own trail
segment, under the Cell's own node id and attributed to the task's grant and goal.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/deps.py (codingrules section 3). Exercises the full in-Cell
    composition together with `hivemind.wardens.warden.Warden`, `hivemind.wardens.spawn.in_cell.
    InCellSpawnSource` and `hivemind.cli.in_cell.providers`, the pieces `main.py`'s own
    `run_in_cell_warden` composes but does not expose a seam to script from outside (test_main.py
    covers that function directly, without a live task).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.deps for build_in_cell_warden_deps, the module under test.
    - test_main.py for the entry point's own connect/announce/stop sequence.
    - test_link.py for CellReady/CapacityReport/CellHeartbeat, not repeated here.
"""

from __future__ import annotations

import asyncio
import dataclasses
import ipaddress
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from hivemind.cell.source import CellIdentity
from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.cli.in_cell.deps import VIRTUAL_CELL_LEASE, build_in_cell_warden_deps
from hivemind.cli.in_cell.link import CellLinkDeps, announce
from hivemind.guard import load_guard_policy
from hivemind.guard.policy import HiveState
from hivemind.llm import text_response
from hivemind.llm.fake import FakeLLMProvider
from hivemind.manifest.env import read_in_cell_env
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.wardens.spawn import InCellSpawnSource
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import WardenId, new_grant_id, new_hive_id, new_node_id, new_task_id
from waggle.messages.control.protocol import Shutdown
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.labels import AccuracyBar, Postcondition, PostconditionKind, Tempo, Urgency
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import TaskAssign, TaskOutcome, TaskResult, WorkerRole
from waggle.signing import Ed25519Signer
from waggle.transport.websocket import WebSocketTransport
from waggle.transport.websocket_client import WebSocketClientTransport
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
_CELL_ID = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _environ(
    server_uri: str,
    queen_node_id: str,
    hive_id: str,
    queen_signer: Ed25519Signer,
    scratch_root: Path,
) -> dict[str, str]:
    """A real `HIVEMIND_*` environment naming `server_uri` as this Cell's own Queen.

    `scratch_root` stands in for the image's own /var/lib/hivemind/scratch, which this host
    (Linux CI in particular) cannot create.
    """
    cell_signer = Ed25519Signer.generate()
    return {
        "HIVEMIND_QUEEN_WAGGLE_URL": server_uri,
        "HIVEMIND_CELL_ID": _CELL_ID,
        "HIVEMIND_HIVE_ID": hive_id,
        "HIVEMIND_QUEEN_NODE_ID": queen_node_id,
        "HIVEMIND_CELL_SIGNING_KEY": cell_signer.private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": queen_signer.public_key_bytes.hex(),
        "HIVEMIND_SCRATCH_ROOT": str(scratch_root),
    }


def _grant(clock: FakeClock, grant_id: str, holder: object) -> GrantIssued:
    """A grant allowing one sub-bee on the WORKER slot, enough budget for one trivial call."""
    return GrantIssued(
        grant_id=grant_id,
        holder=holder,
        cell_id=_CELL_ID,
        task_id=None,
        revision=0,
        allowed=(
            AllowedBinding(
                slot="WORKER",
                source=SourceRef(
                    source_id="local",
                    provider="fake",
                    model="in-cell-placeholder",
                    host_cell_id=None,
                ),
                max_effort=WireEffort.MEDIUM,
            ),
        ),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=clock.now(),
        reason="test grant",
    )


async def _drain(server_receive: AsyncIterator[Envelope], count: int) -> Envelope:
    """Read and discard `count` - 1 envelopes off `server_receive`, and return the last one."""
    envelope: Envelope | None = None
    for _ in range(count):
        envelope = await asyncio.wait_for(anext(server_receive), timeout=WAIT_S)
    assert envelope is not None
    return envelope


@dataclass(frozen=True, slots=True)
class _Scenario:
    """Everything one test needs once a real in-Cell Warden is built and started."""

    warden: Warden
    run_task: asyncio.Task[None]
    provider: FakeLLMProvider
    transport: WebSocketClientTransport
    server_transport: WebSocketTransport
    server_receive: AsyncIterator[Envelope]
    queen_hop: Hop
    cell_id: str
    warden_id: WardenId
    trail: MemoryPheromoneTrail  # This Cell's own local segment, the one trail_sync ships.
    config: InCellRuntimeConfig


@dataclass(frozen=True, slots=True)
class _Connected:
    """What `_connect_and_announce` hands `_build_scenario`: config plus every collaborator."""

    config: InCellRuntimeConfig
    transport: WebSocketClientTransport
    source: InCellSpawnSource
    trail: MemoryPheromoneTrail
    clock: FakeClock
    cell_id: str
    server_transport: WebSocketTransport


async def _connect_and_announce(
    server: WebSocketServer, queen_signer: Ed25519Signer, scratch_root: Path
) -> _Connected:
    """Build this Cell's config/transport/source/cell and send its CellReady, draining it."""
    clock = FakeClock()
    environ = _environ(
        server.uri, new_node_id(clock), new_hive_id(clock), queen_signer, scratch_root
    )
    config = build_runtime_config(read_in_cell_env(environ), clock)
    codec = Codec(signer=config.signer, verifier=config.verifier)
    transport = WebSocketClientTransport(config.queen_waggle_url, codec, clock)
    trail = MemoryPheromoneTrail(clock)
    identity = CellIdentity(
        hive_id=config.hive_id, node_id=config.node_id, actor=str(config.warden_id)
    )
    source = InCellSpawnSource(config.spawn_config, identity, trail, clock)
    cell = (await source.cells())[0]
    connections = server.connections()
    link_deps = CellLinkDeps(
        transport=transport,
        cell=cell,
        warden_id=config.warden_id,
        hive_id=config.hive_id,
        node_id=config.node_id,
        clock=clock,
        heartbeat_interval_s=config.heartbeat_interval_s,
        runtime_version="0.1.0-test",
    )
    await announce(link_deps)
    server_transport = await asyncio.wait_for(anext(connections), timeout=WAIT_S)
    await _drain(server_transport.receive(), 1)  # CellReady.
    return _Connected(
        config=config,
        transport=transport,
        source=source,
        trail=trail,
        clock=clock,
        cell_id=cell.id,
        server_transport=server_transport,
    )


async def _build_scenario(
    server: WebSocketServer, queen_signer: Ed25519Signer, scratch_root: Path
) -> _Scenario:
    """Build and start a real in-Cell Warden, connected to `server`, its provider scripted."""
    connected = await _connect_and_announce(server, queen_signer, scratch_root)
    config = connected.config
    deps = build_in_cell_warden_deps(
        config, connected.source, connected.transport, connected.trail, connected.clock
    )
    provider = deps.bound.provider
    assert isinstance(provider, FakeLLMProvider)
    provider.script(text_response("Done."))  # A trivial, tool-free completion.
    # No [guard] inside a Cell: the shipped policy, naming only the Hive Stand's address as this
    # Cell dials it (roadmap step 10.3a: its Queen URL's host, here the loopback test server).
    assert dataclasses.replace(deps.guard, hive_state=HiveState()) == load_guard_policy()
    host = urlsplit(config.queen_waggle_url).hostname or ""
    assert deps.guard.hive_state.own_addresses == frozenset({ipaddress.ip_address(host)})
    assert deps.local_providers == frozenset({"fake"})  # The in-process fallback provider.
    # Roadmap step 10.3: one policy for the sets and the Enforcer, and a Virtual Cell's lease.
    assert deps.enforcer.policy is deps.guard
    assert deps.lease_capability == VIRTUAL_CELL_LEASE
    assert deps.bindings == config.slots

    warden = Warden(config.warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    queen_hop = Hop(sender=config.hive_id, recipient=config.warden_id, node_id=config.queen_node_id)
    return _Scenario(
        warden=warden,
        run_task=run_task,
        provider=provider,
        transport=connected.transport,
        server_transport=connected.server_transport,
        server_receive=connected.server_transport.receive(),
        queen_hop=queen_hop,
        cell_id=connected.cell_id,
        warden_id=config.warden_id,
        trail=connected.trail,
        config=config,
    )


def _assignment(clock: FakeClock, cell_id: str, grant_id: str) -> TaskAssign:
    """A trivial TaskAssign: one FILE_ABSENT criterion, true without the bee doing anything."""
    return TaskAssign(
        task_id=new_task_id(clock),
        goal_id=new_task_id(clock),
        cell_id=cell_id,
        role=WorkerRole.DRONE,
        slot="WORKER",
        objective="Say hello; nothing to write.",
        # Trivially holds without the bee doing anything: a real acceptance check still runs
        # (hivemind.wardens.acceptance.run_acceptance), just against a fact already true.
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_ABSENT,
                subject="scratch/never-created.txt",
                argv=(),
                expected=None,
            ),
        ),
        tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
        clearance=WireHoneyClearance.C1,
        grant_id=grant_id,
        attempt=1,
        resume_from=None,
        reason="test",
    )


async def _wait_for_result(server_receive: AsyncIterator[Envelope]) -> TaskResult:
    """Pump `server_receive` until a TaskResult arrives (heartbeats/progress may precede it)."""
    for _ in range(20):  # Generous: heartbeats and progress may arrive before the result.
        envelope = await asyncio.wait_for(anext(server_receive), timeout=WAIT_S)
        if isinstance(envelope.payload, TaskResult):
            return envelope.payload
    raise AssertionError("No TaskResult arrived within 20 envelopes.")


async def test_a_task_assign_completes_via_a_real_drone_and_a_scripted_fake_provider(
    tmp_path: Path,
) -> None:
    queen_signer = Ed25519Signer.generate()
    server = WebSocketServer(Codec(signer=queen_signer))
    await server.start()
    try:
        scenario = await _build_scenario(server, queen_signer, tmp_path)
        clock = FakeClock()  # A fresh clock for id minting only; not shared with the Warden's own.
        grant_id = new_grant_id(clock)
        grant = _grant(clock, grant_id, scenario.warden_id)
        assignment = _assignment(clock, scenario.cell_id, grant_id)
        await scenario.server_transport.send(wrap(grant, scenario.queen_hop, clock=clock))
        await scenario.server_transport.send(wrap(assignment, scenario.queen_hop, clock=clock))

        result = await _wait_for_result(scenario.server_receive)
        assert result.outcome is TaskOutcome.SUCCEEDED
        assert result.task_id == assignment.task_id
        assert result.checked_by == scenario.warden_id

        # The completed sub-bee is reaped once its result is accepted, freeing the slot.
        await asyncio.sleep(0)  # Let the Warden's own tick settle after the accept.
        assert scenario.warden.sub_bees == ()

        shutdown = Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown")
        await scenario.server_transport.send(wrap(shutdown, scenario.queen_hop, clock=clock))
        await asyncio.wait_for(scenario.run_task, timeout=WAIT_S)

        assert scenario.warden.state is WardenState.STOPPED
        assert scenario.warden.sub_bees == ()
        await scenario.transport.close()
    finally:
        await server.close()


async def test_a_drones_model_call_is_metered_and_recorded_on_the_cells_own_trail(
    tmp_path: Path,
) -> None:
    queen_signer = Ed25519Signer.generate()
    server = WebSocketServer(Codec(signer=queen_signer))
    await server.start()
    try:
        scenario = await _build_scenario(server, queen_signer, tmp_path)
        clock = FakeClock()  # A fresh clock for id minting only; not shared with the Warden's own.
        grant_id = new_grant_id(clock)
        assignment = _assignment(clock, scenario.cell_id, grant_id)
        grant = _grant(clock, grant_id, scenario.warden_id)
        await scenario.server_transport.send(wrap(grant, scenario.queen_hop, clock=clock))
        await scenario.server_transport.send(wrap(assignment, scenario.queen_hop, clock=clock))

        result = await _wait_for_result(scenario.server_receive)

        assert result.outcome is TaskOutcome.SUCCEEDED
        # The Drone's one model call went through the Cell's Fanner, on its grant's own lane.
        [call] = await scenario.trail.query(TrailQuery(kind="llm.call"))
        assert call.node_id == scenario.config.node_id
        assert call.hive_id == scenario.config.hive_id
        assert call.payload["grant_id"] == grant_id
        assert call.payload["goal_id"] == assignment.goal_id
        shutdown = Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown")
        await scenario.server_transport.send(wrap(shutdown, scenario.queen_hop, clock=clock))
        await asyncio.wait_for(scenario.run_task, timeout=WAIT_S)
        await scenario.transport.close()
    finally:
        await server.close()
