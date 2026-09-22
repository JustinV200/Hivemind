"""Define CellListener: the Queen's own WebSocket listener for every Virtual Cell's control link.

ADR-0027: a Virtual Cell exposes no inbound port and dials out; the Queen is therefore the one
side with a listener at all, and `CellListener` is it. `start(queen)` binds a `waggle.transport.
websocket_server.WebSocketServer` on `[virtual_cells] listen_host`/`listen_port`
(`hivemind.manifest.schema.placement.VirtualCellsSection`) and spawns one handler task per
accepted connection; each handler drains the connection for a `CellReady` (verified, see below)
followed by a `CellHeartbeat`, resolves the Queen-side `hivemind.queen.cell_gate.gate.
QueenReadinessGate` once both have arrived, builds a `hivemind.queen.deps.WardenLink` around the
now-authenticated connection and attaches it to `queen` the same way `hivemind.cli.compose`
attaches the Hive Stand's own Warden (`Queen.attach_warden`); when the connection ends, it detaches
the same way (`hivemind.queen.attach.detach_warden`).

The identity puzzle (module docstring of `gate.py` explains the *why*; this is the *how*):
`_GateVerifier`, this module's own `waggle.codec.Verifier`, is what the one shared `Codec` every
accepted connection uses checks every incoming frame's signature against. For an already-bound
`node_id` it looks the registered public key straight up; for an unknown `node_id`'s very first
frame it has no key yet, so it parses `canonical` (the exact JSON `waggle.codec.canonical_bytes`
produced, still available to a `Verifier.verify` call, since verification happens before the
payload is parsed into a typed message, `waggle.codec.Codec.decode`'s own fixed order) to read the
payload's own `cell_id`, looks *that* up in `QueenReadinessGate.expected_key` (registered before
the Cell even existed, `hive.backends.bootstrap.mint_cell_bootstrap`), and verifies against it.
Every frame is still fully authenticated end to end -- signatures stay mandatory -- only the very
first lookup differs from every one after it, once `node_id` is bound. A forged or replayed frame
naming someone else's `cell_id` still fails: the signature must verify against *that* `cell_id`'s
own registered key, which only the real Cell holds.

Fixed here (this dispatch): `_await_ready` now also captures the `CapacityReport` a real in-Cell
Warden's own `hivemind.cli.in_cell.main._connect_and_announce` sends between `CellReady` and the
first `CellHeartbeat`, converting it into a real `ForageCapacity` (`_capacity_from_report`) instead
of always using `_PLACEHOLDER_CAPACITY` (all zeros); see that function's own docstring for the
defect this closes (a real Virtual Cell's every grant was capped at zero sub-bees). A Cell that
never sends one (a fake or stubbed connection in a unit test) still gets `_PLACEHOLDER_CAPACITY`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`. Built
    by the composition root when `[virtual_cells] backend` is set; attaches Wardens onto whichever
    `Queen` it is given. Calls into `hivemind.cell` (CellCapabilities), `hivemind.forage`
    (ForageCapacity, HostCapacity), `hivemind.queen.attach` (detach_warden), `hivemind.queen.deps`
    (WardenLink), `hivemind.queen.queen` (Queen), `hivemind.queen.cell_gate.gate`
    (QueenReadinessGate), `hivemind.queen.cell_gate.snapshot` (CellSnapshotHandler),
    `hivemind.queen.trail_sync` (TrailSegmentReceiver), waggle (codec, envelope, errors, ids,
    signing, transport) and the `waggle.messages.cell`/`waggle.messages.swarm` families only.

Key invariants:
    - Every accepted connection's first frame must verify as a signed `CellReady` naming a
      `cell_id` `QueenReadinessGate.expect` already knows about; anything else closes the
      connection with `UnknownSignerError` before it is ever attached.
    - `QueenReadinessGate.resolve` is called exactly once per Cell, only once both `CellReady` and
      a first `CellHeartbeat` on the same connection have arrived (the Protocol's own contract).
    - Every accepted connection is either attached (a `WardenLink` handed to `Queen.attach_warden`)
      or closed outright; none is left open and un-tracked.
    - `_handle_connection` always calls `hivemind.queen.attach.detach_warden` on its own way out
      once attached, whether the connection ended cleanly or the listener is stopping.
    - A `CellSnapshotRequest`/`CellRollbackRequest` is answered on the same connection it arrived
      on, correlated to its own envelope id; a `TrailSegmentSync` is handed to `trail_receiver`
      and never answered (the wire kind is an event, not a request). Both are no-ops when
      `CellListenerDeps` names no handler/receiver (roadmap step 5.10/ADR-0027's own follow-up).

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the connection
      direction and per-Cell signing key this module verifies against.
    - hivemind.queen.cell_gate.gate for QueenReadinessGate, this module's own collaborator.
    - waggle.transport.websocket_server for WebSocketServer, the listener this module wraps.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from hivemind.cell.models import Cell, CellCapabilities, CellKind
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.forage.models.capacity import ForageCapacity, HostCapacity
from hivemind.hive.backends.bootstrap import CellReadyInfo
from hivemind.queen.attach import detach_warden
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.deps import WardenLink
from hivemind.queen.queen import Queen
from hivemind.queen.trail_sync import TrailSegmentReceiver
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import CodecError, ConnectionLostError, SignatureError, UnknownSignerError
from waggle.ids import CellId, HiveId, IdKind, NodeId, WardenId, parse_id
from waggle.messages.cell.snapshot import (
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
)
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.forage.hosting import CapacityReport
from waggle.messages.labels import OsFamily
from waggle.messages.reports import PlatformReport
from waggle.messages.swarm import TrailSegmentSync
from waggle.signing import Ed25519Signer, Ed25519Verifier, public_key_from_hex
from waggle.transport.websocket import WebSocketTransport
from waggle.transport.websocket_server import DEFAULT_HOST, OS_ASSIGNED_PORT, WebSocketServer

__all__ = ["CellListener", "CellListenerDeps", "SnapshotRequestHandler"]

# A placeholder ForageCapacity for a Cell that has reported CellReady/CellHeartbeat but no
# forage.capacity_report (CapacityReport): today's only sender, hivemind.cli.in_cell.link.CellLink,
# does not send one yet (module docstring's own "Known gap"). Zero-capacity rather than a guess,
# so a caller that reads it sees "unknown", not a real-looking number.
_PLACEHOLDER_CAPACITY = ForageCapacity(
    host=HostCapacity(
        cores=1,
        memory_bytes=1,
        memory_free_bytes=0,
        disk_bytes=1,
        disk_free_bytes=0,
        cpu_load=0.0,
        gpus=(),
        arch="unknown",
        os=OsFamily.LINUX,  # Every Virtual Cell image is Ubuntu Linux (codingrules section 2).
    ),
    local_seats=(),
    max_sub_bees=0,
)


class SnapshotRequestHandler(Protocol):
    """Answers a CellSnapshotRequest/CellRollbackRequest this listener drains.

    A structural Protocol, not a base class, so `hivemind.queen.cell_gate.snapshot.
    CellSnapshotHandler` (the one production implementation) and a test's own stand-in both
    satisfy it without either importing the other.
    """

    async def snapshot(self, request: CellSnapshotRequest) -> CellSnapshotReply:
        """Answer `request`; see `CellSnapshotHandler.snapshot`."""
        ...

    async def rollback(self, request: CellRollbackRequest) -> CellRollbackReply:
        """Answer `request`; see `CellSnapshotHandler.rollback`."""
        ...


@dataclass(frozen=True, slots=True)
class CellListenerDeps:
    """Every collaborator one CellListener is built with (codingrules section 5.1).

    Attributes:
        gate: Registers each Cell's expected key and resolves once it is ready; also this
            listener's own `_GateVerifier` key source.
        queen_signer: Signs every frame the Queen sends over this listener's connections.
        queen_node_id: The node id the Queen signs its own frames as; stamped on every `Hop`.
        hive_id: The Queen's own bee address; the `sender` of every envelope this listener sends.
        host: The interface to bind (`[virtual_cells] listen_host`).
        port: The port to bind, 0 for OS-assigned (`[virtual_cells] listen_port`).
    """

    gate: QueenReadinessGate
    queen_signer: Ed25519Signer
    queen_node_id: NodeId
    hive_id: HiveId
    host: str = DEFAULT_HOST
    port: int = OS_ASSIGNED_PORT


class CellListener:
    """Accept every Virtual Cell's outbound connection, verify it, and attach it to a Queen."""

    def __init__(self, deps: CellListenerDeps, clock: Clock) -> None:
        """Build a CellListener; call `start(queen)` to begin accepting.

        Args:
            deps: Every collaborator this listener needs.
            clock: Stamps every `CellSnapshotReply`/`CellRollbackReply` this listener sends back;
                held for a future step that also needs to timestamp a rejected connection on the
                trail.
        """
        self._clock = clock
        self._deps = deps
        codec = Codec(signer=deps.queen_signer, verifier=_GateVerifier(deps.gate))
        self._server = WebSocketServer(codec, host=deps.host, port=deps.port)
        self._accept_task: asyncio.Task[None] | None = None
        self._handlers: set[asyncio.Task[None]] = set()
        self._queen: Queen | None = None
        # Late-bound (the same shape `hivemind.queen.cell_gate.provider.
        # LifecycleVirtualCellProvider.bind_queen` already uses, for the identical reason): the
        # composition root's own `CellSnapshotHandler`/`TrailSegmentReceiver` need this listener
        # to already exist (for `_build_registry`'s own lazy endpoint closures), so neither can be
        # a `CellListenerDeps` field passed in at construction. `None` means neither kind this
        # listener may see is answered or merged -- a Hive with no Virtual backend wired for
        # snapshotting or offline trail sync yet.
        self._snapshot_handler: SnapshotRequestHandler | None = None
        self._trail_receiver: TrailSegmentReceiver | None = None

    @property
    def uri(self) -> str:
        """The URI a Cell dials to reach this listener (`ws://host:port`); see `start()` first."""
        return self._server.uri

    def bind_snapshot_handler(self, handler: SnapshotRequestHandler) -> None:
        """Answer every snapshot relay request this listener drains through `handler` from now on.

        Args:
            handler: Built over the same `hivemind.hive.lifecycle.CellLifecycle` and
                `hivemind.hive.snapshot.SnapshotLedgerPort` the composition root wires up.
        """
        self._snapshot_handler = handler

    def bind_trail_receiver(self, receiver: TrailSegmentReceiver) -> None:
        """Merge every `swarm.trail_segment_sync` this listener drains into `receiver` from now on.

        Args:
            receiver: Built over the Queen's own Pheromone Trail.
        """
        self._trail_receiver = receiver

    async def start(self, queen: Queen) -> None:
        """Bind the listener and begin accepting connections, attaching each to `queen`.

        Args:
            queen: Every verified connection is attached to, and detached from, this Queen.
        """
        self._queen = queen
        await self._server.start()
        self._accept_task = asyncio.ensure_future(self._accept_loop())

    async def stop(self) -> None:
        """Stop accepting, close every connection, and wait for every handler to finish.

        Cancels every still-running handler explicitly (this dispatch's own fix, see `_handle`'s
        own docstring for why): a handler attached to the Queen never reads its own transport
        again once attached, so it never notices `self._server.close()` closing the underlying
        connection out from under it on its own; without an explicit cancel here, `stop()` would
        wait forever on a handler that has nothing left to wait for.
        """
        await self._server.close()
        if self._accept_task is not None:
            await self._accept_task
        for task in tuple(self._handlers):
            task.cancel()
        if self._handlers:
            await asyncio.gather(*self._handlers, return_exceptions=True)

    async def _accept_loop(self) -> None:
        """Spawn one handler task per accepted connection until the server closes."""
        async for transport in self._server.connections():
            # A handler owns its own lifetime; this loop never awaits it directly so a slow or
            # stuck Cell never blocks the next one from being accepted (codingrules section 11:
            # every task still has an owner -- stop() awaits every entry in self._handlers).
            task = asyncio.ensure_future(self._handle(transport))
            self._handlers.add(task)
            task.add_done_callback(self._handlers.discard)

    async def _handle(self, transport: WebSocketTransport) -> None:
        """Drain one accepted connection for CellReady then CellHeartbeat, attach, and clean up.

        **Documented defect and this dispatch's own minimal fix:** the module docstring's own
        "Heartbeats and future GrantIssued/TaskAssign frames still flow through the Warden's own
        tick, which drains this same WardenLink" is not actually true once a real (non-fake)
        transport is involved: `hivemind.queen.queen.Queen.attach_warden` calls `link.transport.
        receive()` itself, a *second*, independent call to the same underlying `websockets`
        connection's own single-consumer `recv()` (`waggle.transport.base.Transport`'s own
        documented "one task sends and one task receives at a time" concurrency model) -- so a
        `_handle` that also kept iterating `transport.receive()` after attaching raced the Queen's
        own tick loop for the same socket and crashed with `websockets.exceptions.
        ConcurrencyError`, confirmed directly the first time any test actually attached a real
        Queen to a real Virtual Cell connection over a real loopback WebSocket (every prior test
        of this module used a stub `Queen`/`Transport`, never exercising the real double-consumer
        race). `hivemind.queen.queen` is not in this dispatch's allowed-to-fix list, so the fix
        stays here: `link` is now built around `_FanoutTransport`, not `transport` directly --
        that class's own docstring is the one and only real reader of `transport.receive()` from
        now on, feeding the Queen's own consumption through an internal queue and answering a
        `CellSnapshotRequest`/`CellRollbackRequest`/`TrailSegmentSync` inline itself, so both
        consumers keep seeing every envelope they need with no second `recv()` in sight.
        """
        binding = await _await_ready(transport)
        if binding is None:
            await transport.close()
            return  # Never became ready; nothing was attached, so nothing to detach either.
        assert self._queen is not None  # noqa: S101 - start() always runs before a connection.
        fanout = self._attach(transport, binding)
        try:
            # Ends normally once fanout's own pump notices the connection close (or a decode
            # failure); never raises on that path (_FanoutTransport.pump's own docstring), so the
            # only exception that can reach here is a genuine external cancel (stop(), below).
            await fanout.pump_task
        finally:
            if not fanout.pump_task.done():
                fanout.pump_task.cancel()
            await asyncio.gather(fanout.pump_task, return_exceptions=True)
            await detach_warden(self._queen, binding.warden_id)

    def _attach(self, transport: WebSocketTransport, binding: _ReadyBinding) -> _FanoutTransport:
        """Build the fan-out link for a ready Cell, attach it to the Queen, then resolve the gate.

        Attach before resolving the gate: a caller waking from QueenReadinessGate.wait_ready
        (hivemind.queen.cell_gate.provider, roadmap step 5.6) looks this Cell's own link up in
        `queen.wardens` next, so it must already be there the instant wait_ready returns.
        """
        assert self._queen is not None  # noqa: S101 - _handle checked already.
        hop = Hop(
            sender=self._deps.hive_id, recipient=binding.warden_id, node_id=self._deps.queen_node_id
        )
        fanout = _FanoutTransport(
            transport, lambda envelope: self._dispatch(transport, hop, envelope)
        )
        link = WardenLink(
            warden_id=binding.warden_id, cell=_cell_from_binding(binding), transport=fanout, hop=hop
        )
        self._queen.attach_warden(link)
        self._deps.gate.resolve(binding.cell_id, binding.node_id, binding.info)
        return fanout

    async def _dispatch(self, transport: WebSocketTransport, hop: Hop, envelope: Envelope) -> None:
        """Answer a snapshot relay request, or merge a trail segment chunk; else do nothing.

        Called only from `_FanoutTransport.pump` now (this class's own module docstring): never
        directly on `_handle`'s own former read loop, which no longer exists.
        """
        payload = envelope.payload
        if isinstance(payload, CellSnapshotRequest) and self._snapshot_handler is not None:
            snapshot_reply = await self._snapshot_handler.snapshot(payload)
            await transport.send(
                wrap(snapshot_reply, hop, clock=self._clock, correlation_id=envelope.id)
            )
        elif isinstance(payload, CellRollbackRequest) and self._snapshot_handler is not None:
            rollback_reply = await self._snapshot_handler.rollback(payload)
            await transport.send(
                wrap(rollback_reply, hop, clock=self._clock, correlation_id=envelope.id)
            )
        elif isinstance(payload, TrailSegmentSync) and self._trail_receiver is not None:
            await self._trail_receiver.receive(payload)


# Marks "the pump will never put another envelope" on _FanoutTransport's own internal queue; a
# private sentinel object (never an Envelope, so `is` identity alone tells the two apart).
_FANOUT_DONE = object()


class _FanoutTransport:
    """Wrap one accepted connection so the Queen's own read and this listener's own read agree.

    `waggle.transport.base.Transport`'s own documented concurrency model is "one task sends and
    one task receives at a time" -- a single real `websockets` connection has exactly one
    `recv()` to give out. `CellListener._handle`'s own module docstring names the defect this
    class exists to close: `hivemind.queen.queen.Queen.attach_warden` becomes a second reader the
    moment a `WardenLink` is attached, so this class is built around the real transport instead
    and handed to `attach_warden` in its place -- `pump`, started at construction, is the one and
    only task that ever calls the real transport's own `receive()`; every envelope it reads either
    answers inline (a snapshot relay request, a trail segment sync -- `CellListener._dispatch`) or
    is queued for `receive()` here to yield to the Queen, so both "readers" still see every
    envelope meant for them without a second `recv()` ever happening.

    Implements `waggle.transport.base.Transport` structurally (`send`/`receive`/`close`/
    `is_connected`/`connect`, every one forwarded to or fed from the real transport): `hivemind.
    queen.deps.WardenLink.transport` and `hivemind.queen.queen.Queen.attach_warden` need nothing
    more than that, so neither has to change to accept this in place of a real `WebSocketTransport`.
    """

    def __init__(
        self, real: WebSocketTransport, dispatch: Callable[[Envelope], Awaitable[None]]
    ) -> None:
        """Wrap `real`, starting `pump` immediately so nothing else may ever call its `receive()`.

        Args:
            real: The accepted connection this listener drains exclusively from now on.
            dispatch: Answers a `CellSnapshotRequest`/`CellRollbackRequest`/`TrailSegmentSync`
                inline, on the pump's own task (`CellListener._dispatch`, bound to this specific
                connection's own transport and Hop).
        """
        self._real = real
        self._dispatch = dispatch
        self._queue: asyncio.Queue[Envelope | object] = asyncio.Queue()
        self._closed_exc: Exception | None = None
        self.pump_task: asyncio.Task[None] = asyncio.ensure_future(self._pump())

    @property
    def is_connected(self) -> bool:
        """See `waggle.transport.base.Transport.is_connected`; forwarded to the real transport."""
        return self._real.is_connected

    async def connect(self) -> None:
        """See `waggle.transport.base.Transport.connect`; forwarded (already connected, a no-op)."""
        await self._real.connect()

    async def send(self, envelope: Envelope) -> None:
        """See `waggle.transport.base.Transport.send`; forwarded to the real transport directly."""
        await self._real.send(envelope)

    async def receive(self) -> AsyncIterator[Envelope]:
        """Yield every envelope `pump` queued for the Queen; see `Transport.receive`.

        Raises:
            ConnectionLostError | CodecError | SignatureError: Whatever `pump`'s own `receive()`
                on the real transport raised, re-raised here once queued items are exhausted, so
                a caller sees exactly the same failure shape it would from the real transport.
        """
        while True:
            item = await self._queue.get()
            if item is _FANOUT_DONE:
                if self._closed_exc is not None:
                    raise self._closed_exc
                return  # A clean end (StopAsyncIteration-shaped): nothing more will ever arrive.
            assert isinstance(item, Envelope)  # noqa: S101 - only Envelope or _FANOUT_DONE is ever queued.
            yield item

    async def close(self) -> None:
        """See `waggle.transport.base.Transport.close`; forwarded to the real transport directly."""
        await self._real.close()

    async def _pump(self) -> None:
        """The one and only real reader of the wrapped transport's own `receive()` (class doc).

        Never raises: a lost connection or a decode failure is recorded on `self._closed_exc`
        (re-raised from `receive()` above, once every already-queued envelope has been yielded)
        rather than propagated out of this task, so a caller that only awaits `pump_task` itself
        (`CellListener._handle`) sees a clean, normal return on every path.
        """
        try:
            async for envelope in self._real.receive():
                if isinstance(
                    envelope.payload, (CellSnapshotRequest, CellRollbackRequest, TrailSegmentSync)
                ):
                    # Answered right here, inline: these three kinds are never meant for the
                    # Queen's own bee-protocol dispatch (class docstring).
                    await self._dispatch(envelope)
                else:
                    await self._queue.put(envelope)
        except (ConnectionLostError, CodecError, SignatureError) as exc:
            self._closed_exc = exc
        finally:
            await self._queue.put(_FANOUT_DONE)


class _GateVerifier:
    """The Verifier every accepted connection's shared Codec checks a signature against.

    Module docstring: an already-bound node_id looks its key up directly; an unknown node_id's
    first frame is resolved by reading the claimed cell_id out of the still-raw canonical bytes.
    """

    def __init__(self, gate: QueenReadinessGate) -> None:
        """Hold the gate this verifier resolves an unknown node_id's first frame against."""
        self._gate = gate
        self._learned: dict[str, bytes] = {}  # node_id -> the raw public key it verified under.

    def verify(self, node_id: str, canonical: bytes, signature: str) -> None:
        """Verify `signature`, resolving `node_id`'s key by cell_id on its very first frame."""
        raw_key = self._learned.get(node_id)
        if raw_key is None:
            raw_key = _key_for_first_frame(self._gate, canonical)
        # A one-off Ed25519Verifier over exactly this key: raises InvalidSignatureError on a bad
        # signature, which propagates unchanged -- this method never catches its own check.
        Ed25519Verifier({node_id: raw_key}).verify(node_id, canonical, signature)
        self._learned[node_id] = raw_key  # Only cached once the signature actually verified.


def _key_for_first_frame(gate: QueenReadinessGate, canonical: bytes) -> bytes:
    """Read the claimed cell_id out of raw canonical bytes and return its registered key.

    Raises:
        UnknownSignerError: The bytes are not readable JSON, carry no valid CellId-shaped
            payload.cell_id, or that cell_id has no key registered (never expected, or forgotten).
    """
    try:
        wire = json.loads(canonical)
        raw_cell_id = str(wire["payload"]["cell_id"])
        cell_id = CellId(parse_id(raw_cell_id, IdKind.CELL))
    except (ValueError, KeyError, TypeError) as exc:
        raise UnknownSignerError(
            "The first frame from an unknown node did not carry a readable payload.cell_id."
        ) from exc
    key_hex = gate.expected_key(cell_id)
    if key_hex is None:
        raise UnknownSignerError(
            f"Cell {cell_id} is not registered with this ReadinessGate (never expected, or "
            "already forgotten)."
        )
    return public_key_from_hex(key_hex)


@dataclass(frozen=True, slots=True)
class _ReadyBinding:
    """What one connection's own CellReady + CellHeartbeat handshake resolved to."""

    cell_id: CellId
    warden_id: WardenId
    node_id: NodeId
    comb_shield: CombShieldLevel
    info: CellReadyInfo


async def _await_ready(transport: WebSocketTransport) -> _ReadyBinding | None:
    """Drain `transport` until a verified CellReady then a CellHeartbeat both arrive, or it ends.

    **Documented defect and this dispatch's own minimal fix:** the module's own former "Known gap"
    ("`hivemind.cli.in_cell.link.CellLink` -- today's only sender -- never emits `CapacityReport`")
    is stale: `hivemind.cli.in_cell.main._connect_and_announce` already sends one, between
    `CellReady` and the first `CellHeartbeat` (that module's own module docstring: "sends the three
    frames that must go out before a Warden exists... announce/send_capacity_report/
    send_cell_heartbeat, in that order"). This function simply never looked for it, so
    `_PLACEHOLDER_CAPACITY` (`max_sub_bees=0`) was used unconditionally for every real Virtual
    Cell, which zeros `hivemind.queen.forage.ceilings._initial_ceilings`'s own `max_sub_bees` the
    moment `hivemind.queen.dispatcher.ready._ensure_warden_provisioned` runs -- every grant this
    Warden is ever issued then allows zero sub-bees (`Ceilings.max_sub_bees` caps every later
    grant), so a Drone can never be spawned on it and its task sits RUNNING forever, escalating a
    `GRANT_EXCEEDED` Alarm on every attempt. Confirmed directly: the first time any test actually
    ran a real in-Cell Warden's own `CapacityReport` past a real `CellListener`. Now captured here,
    the same way `CellReady`'s own `platform`/`capabilities` already are.

    Returns:
        The binding once both frames arrived, or None if the connection ended (or a frame failed
        to verify) before they did.
    """
    ready: CellReady | None = None
    node_id: NodeId | None = None
    capacity: ForageCapacity | None = None
    try:
        async for envelope in transport.receive():
            if isinstance(envelope.payload, CellReady):
                ready, node_id = envelope.payload, envelope.node_id
            elif isinstance(envelope.payload, CapacityReport) and ready is not None:
                capacity = _capacity_from_report(envelope.payload, ready.platform)
            elif isinstance(envelope.payload, CellHeartbeat) and ready is not None:
                assert node_id is not None  # noqa: S101 - set together with `ready` just above.
                return _ReadyBinding(
                    cell_id=ready.cell_id,
                    warden_id=ready.warden_id,
                    node_id=node_id,
                    comb_shield=CombShieldLevel.from_wire(ready.comb_shield),
                    info=CellReadyInfo(
                        capabilities=CellCapabilities.from_wire(ready.platform, ready.capabilities),
                        # The real report when one arrived (this function's own docstring);
                        # _PLACEHOLDER_CAPACITY (all zeros) only for a Cell that never sends one.
                        capacity=capacity if capacity is not None else _PLACEHOLDER_CAPACITY,
                    ),
                )
    except (ConnectionLostError, CodecError, SignatureError):
        pass  # The link ended, or a frame failed to verify; report "never became ready" below.
    return None


def _capacity_from_report(report: CapacityReport, platform: PlatformReport) -> ForageCapacity:
    """Build a ForageCapacity from a Cell's own `forage.capacity_report`, plus its own platform.

    Args:
        report: The Cell's own capacity snapshot.
        platform: `CellReady.platform`, the same source `CellCapabilities.from_wire` already
            reads `arch`/`os` from (`HostCapacity.from_wire`'s own split, this module's own
            docstring).

    Returns:
        The equivalent ForageCapacity. `local_seats` is always empty: `CapacityReport` carries no
        Seat figures of its own (`waggle.messages.forage.hosting`'s own field list), matching
        `_PLACEHOLDER_CAPACITY`'s own shape.
    """
    return ForageCapacity(
        host=HostCapacity.from_wire(report.host, platform),
        local_seats=(),
        max_sub_bees=report.max_sub_bees,
    )


def _cell_from_binding(binding: _ReadyBinding) -> Cell:
    """Build this Cell straight from its own CellReady + CellReadyInfo, no VirtualCellSpec needed.

    Deliberately not "cell built from the spec": `queen.placement.decide` never reads `Cell.name`/
    `.source` for an attached candidate (only `capabilities`, `capacity`, `comb_shield` and
    `is_hive_stand`, none of which this loses), and `hivemind.hive.provider.
    LifecycleVirtualCellProvider` already holds the exact `VirtualCellSpec` this Cell was
    provisioned from -- it is free to build a fuller `Cell` for its own `hivemind.hive.lifecycle.
    LiveVirtualCell.cell` from that spec directly, without this listener ever needing to import
    `hivemind.hive` (Layer 3) back down from `hivemind.queen` (Layer 6).
    """
    return Cell(
        id=binding.cell_id,
        kind=CellKind.VIRTUAL,
        name=str(binding.cell_id),
        source="virtual",
        capabilities=binding.info.capabilities,
        capacity=binding.info.capacity,
        access_level=AccessLevel.FULL,  # Cell's own validator: every VIRTUAL Cell is FULL.
        comb_shield=binding.comb_shield,
    )
