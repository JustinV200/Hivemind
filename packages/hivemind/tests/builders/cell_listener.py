"""Build a started Cell listener, its own Queen and a fake Virtual Cell that dials it for real.

The Queen's door for a Virtual Cell's Warden (`hivemind.queen.cell_gate.listener.CellListener`) is
a real WebSocket listener, so what it does with the frames of a connection can only be tested over
one: a "Cell" dials it with a real `WebSocketClientTransport`, sends the signed `cell.ready` and
`cell.heartbeat` of a handshake, and from then on sends whatever a test wants the Queen to hear,
and drops, closes and dials again as a Warden whose link failed does. `build_scenario` is the
listener over a fresh Queen with the transport's limits on (a replay window and a frame ceiling,
exactly as the composition root runs it) and the Queen's own trail recording what they refuse;
`FakeCell` is the Warden's side; `attach` dials, announces and waits until the Queen holds the
Warden. `slow_trail` makes every write to the Queen's trail yield to the event loop, as the SQLite
store the Hive really runs does (a thread hop), which is what lets the listener's reader run during
an attach, and so what makes the order of an attach observable.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by the Cell listener's unit tests under
    packages/hivemind/tests/unit/queen/cell_gate; builds on `builders.queen` for the Queen's
    deps and `builders.cells` for a Cell's capabilities.

Key invariants:
    - The listener, its codec, its replay window and its Queen are the real classes; only the
      Cell is a stand-in, and it signs with a key the gate expects, as a booted Cell does.
    - The listener's clock is the scenario's `clock`, so a test can move it past the reconnection
      grace without waiting.
    - Nothing here sleeps for longer than a poll; every wait is bounded by `WAIT_S`.

See Also:
    - hivemind.queen.cell_gate.listener for the module this plays a Cell against.
    - tests/unit/queen/cell_gate/test_listener.py for the tests of its own dispatch.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from builders.cells import make_capabilities
from builders.queen import make_queen_deps

from hivemind.honey_store import HoneyAccess
from hivemind.pheromone import PheromoneEvent, TrailRecorder
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.listener import CellListener, CellListenerDeps
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from waggle.clock import FakeClock
from waggle.codec import Codec, Signer
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import (
    CellId,
    HiveId,
    NodeId,
    WardenId,
    new_cell_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.base import WaggleMessage
from waggle.messages.cell.status import CellHeartbeat, CellMode, CellReady
from waggle.messages.labels import AccessLevel as WireAccessLevel
from waggle.messages.labels import CombShieldLevel as WireCombShieldLevel
from waggle.signing import Ed25519Signer, Ed25519Verifier, public_key_hex
from waggle.transport.websocket_client import WebSocketClientTransport

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
POLL_S = 0.01  # How often a wait looks at shared state.
SLOW_TRAIL_S = 0.02  # What a trail write yields for when `slow_trail` is on: a thread hop's worth.

__all__ = [
    "POLL_S",
    "SLOW_TRAIL_S",
    "WAIT_S",
    "FakeCell",
    "Scenario",
    "attach",
    "attached_ids",
    "build_scenario",
    "cell_ready",
    "recorded",
    "until",
    "until_async",
]


def cell_ready(cell_id: CellId, warden_id: WardenId) -> CellReady:
    """Build a valid CellReady for `cell_id`, the shape a real Cell's own CellLink sends."""
    platform, capabilities = make_capabilities().to_wire()
    return CellReady(
        cell_id=cell_id,
        warden_id=warden_id,
        platform=platform,
        capabilities=capabilities,
        access_level=WireAccessLevel.FULL,
        comb_shield=WireCombShieldLevel.MEADOW,
        attestation=(),
        runtime_version="0.1.0-test",
    )


class FakeCell:
    """Everything one test scenario needs to act as one Virtual Cell dialling the listener."""

    def __init__(self, listener: CellListener) -> None:
        """Mint the Cell's ids and the key the gate will expect; dial with `dial`."""
        self.cell_id = new_cell_id(FakeClock())
        self.warden_id = new_warden_id(FakeClock())
        self.node_id = new_node_id(FakeClock())
        self.signer = Ed25519Signer.generate()
        self._listener = listener

    async def dial(
        self, queen_node_id: NodeId, queen_signer: Ed25519Signer, signer: Signer | None = None
    ) -> WebSocketClientTransport:
        """Connect to the listener with a codec that only trusts the Queen's own key."""
        codec = Codec(
            signer=signer if signer is not None else self.signer,
            verifier=Ed25519Verifier({queen_node_id: queen_signer.public_key_bytes}),
        )
        transport = WebSocketClientTransport(self._listener.uri, codec, FakeClock())
        await transport.connect()
        return transport

    def hop(self, hive_id: HiveId) -> Hop:
        """The Warden's address, the Queen's and the node the Cell signs as."""
        return Hop(sender=self.warden_id, recipient=hive_id, node_id=self.node_id)

    def envelope(self, payload: WaggleMessage, hive_id: HiveId) -> Envelope:
        """Wrap `payload` as this Warden sends it to the Queen."""
        return wrap(payload, self.hop(hive_id), clock=FakeClock())

    def heartbeat(self) -> CellHeartbeat:
        """A `cell.heartbeat` of this Cell."""
        return CellHeartbeat(
            cell_id=self.cell_id,
            mode=CellMode.ACTIVE,
            lease_ids=(),
            worker_count=0,
            is_shield_verified=True,
            interval_s=15.0,
        )

    async def send_ready(self, transport: WebSocketClientTransport, hive_id: HiveId) -> None:
        """Send the `cell.ready` that opens a handshake."""
        await transport.send(self.envelope(cell_ready(self.cell_id, self.warden_id), hive_id))

    async def send_heartbeat(self, transport: WebSocketClientTransport, hive_id: HiveId) -> None:
        """Send a fresh `cell.heartbeat`: the frame that completes a handshake."""
        await transport.send(self.envelope(self.heartbeat(), hive_id))


@dataclass(frozen=True, slots=True)
class Scenario:
    """A started CellListener, its own Queen, gate, trail and the ids a test dials it as."""

    listener: CellListener
    queen: Queen
    gate: QueenReadinessGate
    queen_signer: Ed25519Signer
    queen_node_id: NodeId
    hive_id: HiveId
    trail: MemoryPheromoneTrail
    deps: QueenDeps
    clock: FakeClock  # The listener's: what its replay grace is measured on.


async def build_scenario(*, slow_trail: bool = False, honey: HoneyAccess | None = None) -> Scenario:
    """Build one started CellListener attached to one fresh Queen, sharing one hive_id.

    Args:
        slow_trail: Make every write to the Queen's trail yield for `SLOW_TRAIL_S`, as the real
            store's thread hop does, so an attach takes long enough for the listener's reader to
            run in the middle of it.
        honey: The Honey Store the Queen deposits Nectar into; none by default.
    """
    queen_deps: QueenDeps
    queen_deps, _link, _end = make_queen_deps(honey=honey)
    gate = QueenReadinessGate()
    queen_signer = Ed25519Signer.generate()
    queen_node_id = new_node_id(FakeClock())
    assert isinstance(queen_deps.trail, MemoryPheromoneTrail)
    if slow_trail:
        _slow_down(queen_deps.trail)
    # The Queen's own identity records what a link refused, on her own trail (roadmap step 10.6).
    identity = queen_deps.identity
    recorder = TrailRecorder(
        trail=queen_deps.trail,
        clock=queen_deps.clock,
        hive_id=identity.hive_id,
        node_id=identity.node_id,
    )
    clock = FakeClock()
    listener = CellListener(
        CellListenerDeps(
            gate=gate,
            queen_signer=queen_signer,
            queen_node_id=queen_node_id,
            hive_id=identity.hive_id,
            recorder=recorder,
        ),
        clock,
    )
    queen = Queen(queen_deps)
    await listener.start(queen)
    return Scenario(
        listener,
        queen,
        gate,
        queen_signer,
        queen_node_id,
        identity.hive_id,
        queen_deps.trail,
        queen_deps,
        clock,
    )


def _slow_down(trail: MemoryPheromoneTrail) -> None:
    """Have `trail.record` yield to the event loop first, as a store that hops threads does."""
    write = trail.record

    async def record(event: PheromoneEvent) -> None:
        await asyncio.sleep(SLOW_TRAIL_S)
        await write(event)

    trail.record = record  # type: ignore[method-assign]


async def attach(scenario: Scenario) -> tuple[FakeCell, WebSocketClientTransport]:
    """Dial, verify and attach one fake Cell to `scenario`'s own listener; return it, connected."""
    cell = FakeCell(scenario.listener)
    await scenario.gate.expect(cell.cell_id, public_key_hex(cell.signer.public_key_bytes))
    transport = await cell.dial(scenario.queen_node_id, scenario.queen_signer)
    await cell.send_ready(transport, scenario.hive_id)
    await cell.send_heartbeat(transport, scenario.hive_id)
    await asyncio.wait_for(until(lambda: cell.warden_id in attached_ids(scenario.queen)), WAIT_S)
    return cell, transport


def attached_ids(queen: Queen) -> set[WardenId]:
    """Return every currently attached WardenId, for a before/after membership check."""
    return {link.warden_id for link in queen.wardens}


async def until(condition: Callable[[], bool]) -> None:
    """Poll `condition` until it is true.

    A short poll, not an `asyncio.Event`, on purpose: the state being waited on (`queen.wardens`)
    changes inside the listener's own handler task, which a test does not instrument with an
    event of its own; every call site bounds this with `asyncio.wait_for`.
    """
    while not condition():  # noqa: ASYNC110 - polling shared state, see docstring.
        await asyncio.sleep(POLL_S)


async def until_async(condition: Callable[[], Awaitable[bool]]) -> None:
    """Same as `until`, for an async condition (an `await`-ing query, not shared state)."""
    while not await condition():  # noqa: ASYNC110 - polling shared state, see `until`.
        await asyncio.sleep(POLL_S)


async def recorded(scenario: Scenario, kind: str) -> bool:
    """Whether the Queen's trail holds a `kind` event yet."""
    from hivemind.pheromone import TrailQuery

    return bool(await scenario.trail.query(TrailQuery(kind=kind)))
