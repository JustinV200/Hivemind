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

Known gap (documented, not fixed here): `hivemind.cli.in_cell.link.CellLink` -- today's only
sender -- never emits `waggle.messages.forage.hosting.CapacityReport`, so a Cell's real
`ForageCapacity` never reaches this listener; `_PLACEHOLDER_CAPACITY` (all zeros) stands in until a
real in-Cell Warden sends one (this dispatch's own report names this explicitly).

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
from waggle.messages.labels import OsFamily
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
        """Stop accepting, close every connection, and wait for every handler to finish."""
        await self._server.close()
        if self._accept_task is not None:
            await self._accept_task
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
        """Drain one accepted connection for CellReady then CellHeartbeat, attach, and clean up."""
        binding = await _await_ready(transport)
        if binding is None:
            await transport.close()
            return  # Never became ready; nothing was attached, so nothing to detach either.
        assert self._queen is not None  # noqa: S101 - start() always runs before a connection.
        link = WardenLink(
            warden_id=binding.warden_id,
            cell=_cell_from_binding(binding),
            transport=transport,
            hop=Hop(
                sender=self._deps.hive_id,
                recipient=binding.warden_id,
                node_id=self._deps.queen_node_id,
            ),
        )
        # Attach before resolving the gate: a caller waking from QueenReadinessGate.wait_ready
        # (hivemind.hive.provider, roadmap step 5.6) looks this Cell's own link up in
        # `queen.wardens` next, so it must already be there the instant wait_ready returns.
        self._queen.attach_warden(link)
        self._deps.gate.resolve(binding.cell_id, binding.node_id, binding.info)
        try:
            async for envelope in transport.receive():
                # Heartbeats and future GrantIssued/TaskAssign frames still flow through the
                # Warden's own tick, which drains this same WardenLink; this loop only answers
                # the two kinds a Warden cannot reach the Queen's own collaborators any other
                # way for (the snapshot relay, the offline trail sync) and otherwise falls
                # through, same as before.
                await self._dispatch(transport, link.hop, envelope)
        except (ConnectionLostError, CodecError, SignatureError):
            pass
        finally:
            await detach_warden(self._queen, binding.warden_id)

    async def _dispatch(self, transport: WebSocketTransport, hop: Hop, envelope: Envelope) -> None:
        """Answer a snapshot relay request, or merge a trail segment chunk; else do nothing."""
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

    Returns:
        The binding once both frames arrived, or None if the connection ended (or a frame failed
        to verify) before they did.
    """
    ready: CellReady | None = None
    node_id: NodeId | None = None
    try:
        async for envelope in transport.receive():
            if isinstance(envelope.payload, CellReady):
                ready, node_id = envelope.payload, envelope.node_id
            elif isinstance(envelope.payload, CellHeartbeat) and ready is not None:
                assert node_id is not None  # noqa: S101 - set together with `ready` just above.
                return _ReadyBinding(
                    cell_id=ready.cell_id,
                    warden_id=ready.warden_id,
                    node_id=node_id,
                    comb_shield=CombShieldLevel.from_wire(ready.comb_shield),
                    info=CellReadyInfo(
                        capabilities=CellCapabilities.from_wire(ready.platform, ready.capabilities),
                        capacity=_PLACEHOLDER_CAPACITY,
                    ),
                )
    except (ConnectionLostError, CodecError, SignatureError):
        pass  # The link ended, or a frame failed to verify; report "never became ready" below.
    return None


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
