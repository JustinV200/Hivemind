"""Define CellListener: the Queen's own WebSocket listener for every Virtual Cell's control link.

ADR-0027: a Virtual Cell exposes no inbound port and dials out; the Queen is therefore the one
side with a listener at all, and `CellListener` is it. `start(queen)` binds a `waggle.transport.
websocket_server.WebSocketServer` on `[virtual_cells] listen_host`/`listen_port`
(`hivemind.manifest.schema.placement.VirtualCellsSection`) and spawns one handler task per
accepted connection; each handler drains the connection for a `CellReady` (verified, see below)
followed by a `CellHeartbeat`, then hands the now-authenticated connection to the shared attach
path (`hivemind.queen.attach.serve_warden`), which attaches its Warden to `queen` the same way
`hivemind.cli.compose` attaches the Hive Stand's own Warden (`Queen.attach_warden`), serves the
link, and detaches it (`hivemind.queen.attach.detach_if_current`) when the connection ends. The
gate resolves its `QueenReadinessGate` (the provider's ready wait) from the attach hook, once the
link is in `queen.wardens`. Everything after the handshake is that shared helper's, because the
swarm gate attaches a colonized device's Warden through the same code (roadmap 11.6a); this module
holds only what is a Virtual Cell's own: how its first frame is verified, and what its handshake
reads.

The identity puzzle (module docstring of `gate.py` explains the *why*; this is the *how*):
`.handshake.GateVerifier`, the `waggle.codec.Verifier` this module builds, is what the one shared
`Codec` every accepted connection uses checks every incoming frame's signature against. For an
already-bound `node_id` it looks the registered public key straight up; for an unknown `node_id`'s
very first frame it has no key yet, so it parses `canonical` (the exact JSON
`waggle.codec.canonical_bytes` produced, still available to a `Verifier.verify` call, since
verification happens before the payload is parsed into a typed message, `waggle.codec.Codec.
decode`'s own fixed order) to read the payload's own `cell_id`, looks *that* up in
`QueenReadinessGate.expected_key` (registered before the Cell even existed,
`hive.backends.bootstrap.mint_cell_bootstrap`), and verifies against it.
Every frame is still fully authenticated end to end -- signatures stay mandatory -- only the very
first lookup differs from every one after it, once `node_id` is bound. A forged or replayed frame
naming someone else's `cell_id` still fails: the signature must verify against *that* `cell_id`'s
own registered key, which only the real Cell holds.

`_await_ready` also captures the `CapacityReport` a real in-Cell Warden's own
`hivemind.cli.in_cell.main._connect_and_announce` sends between `CellReady` and the first
`CellHeartbeat`, converting it into a real `ForageCapacity` (`_capacity_from_report`) instead of
always using `_PLACEHOLDER_CAPACITY` (all zeros): without it a real Virtual Cell's every grant was
capped at zero sub-bees. A Cell that never sends one (a fake or stubbed connection in a unit test)
still gets `_PLACEHOLDER_CAPACITY`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`. Built
    by the composition root when `[virtual_cells] backend` is set; attaches Wardens onto whichever
    `Queen` it is given. Calls into `hivemind.cell` (CellCapabilities), `hivemind.forage`
    (ForageCapacity, HostCapacity), `hivemind.queen.attach` (the shared attach path),
    `hivemind.queen.deps` (WardenLink), `hivemind.queen.errors` (WardenSpawnRefusedError),
    `hivemind.queen.queen` (Queen), `hivemind.queen.cell_gate.gate` (QueenReadinessGate),
    `hivemind.queen.cell_gate.trust.refusals` (the transport options), `hivemind.queen.cell_gate.
    handshake` (GateVerifier, HandshakeRefusals), `hivemind.queen.trail`
    (TrailSegmentReceiver), `hivemind.pheromone` (TrailRecorder), waggle (codec, envelope, errors,
    ids, signing, transport) and the `waggle.messages.cell`/`waggle.messages.swarm` families only.

Key invariants:
    - Every accepted connection's first frame must verify as a signed `CellReady` naming a
      `cell_id` `QueenReadinessGate.expect` already knows about; anything else closes the
      connection with `UnknownSignerError` before it is ever attached, and a handshake frame
      whose signature failed is `guard.handshake_refused` about the Hive, never the Cell it
      claimed (`.trust.handshake`).
    - `QueenReadinessGate.resolve` is called exactly once per Cell, only once both `CellReady` and
      a first `CellHeartbeat` on the same connection have arrived, and only after the Warden is in
      `queen.wardens`: a caller waking from `wait_ready` finds its link there.
    - Every accepted connection is either attached or closed outright; none is left open and
      un-tracked. A Warden the Queen's own set refuses to spawn (roadmap step 10.3,
      `WardenSpawnRefusedError`, already `guard.denied` on the trail) is closed the same way and
      its Cell's gate is never resolved, so the provider's own ready wait tears the Cell down.
    - The connection's frames are read by one iterator from the handshake to the end of the link;
      the fan-out's pump is its only reader once attached.
    - A Virtual Cell's Warden is detached the moment its connection ends, if that link is still
      its current one (its lifecycle owns a dead container); one that reconnected first keeps its
      new link.
    - The server runs with the transport's default limits (roadmap steps 11.3a and 11.3b): a
      connection over its frame ceiling is closed and a replayed envelope id is dropped, each
      recorded as a guard event naming the node (`.trust.refusals.transport_refusals`); a duplicate
      shortly after a Cell's Warden attached is the expected outbox replay of a reconnection and
      is dropped silently.
    - A frame a Queen-side wait registered for (`AnswerClaims`: an Overwintering scrub's
      `LeaseReleased`, `hivemind.queen.cell_gate.scrub`) is handed to that wait on the fan-out's
      pump, with the Cell its link proved, and never queued for the Queen's reader: her tick never
      reads it, so it never wakes an awake episode. Any other frame reaches her exactly as before.

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the connection
      direction and per-Cell signing key this module verifies against.
    - hivemind.queen.attach for the shared attach path, the fan-out and the link refusals.
    - hivemind.queen.cell_gate.gate for QueenReadinessGate, this module's own collaborator.
    - hivemind.queen.cell_gate.scrub for ScrubAcks, the answers `AnswerClaims` takes.
    - waggle.transport.websocket_server for WebSocketServer, the listener this module wraps.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from hivemind.cell.models import Cell, CellCapabilities, CellKind
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.forage.models.capacity import ForageCapacity, HostCapacity
from hivemind.hive.backends.bootstrap import CellReadyInfo
from hivemind.pheromone import TrailRecorder
from hivemind.queen.attach import (
    FANOUT_QUEUE_SIZE,
    AnswerClaims,
    LinkHooks,
    LinkServices,
    ProvedLink,
    SnapshotRequestHandler,
    serve_warden,
)
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.port import ListenerPorts, bind_sticky
from hivemind.queen.cell_gate.trust.handshake import GateVerifier, HandshakeRefusals
from hivemind.queen.cell_gate.trust.refusals import transport_options
from hivemind.queen.deps import WardenLink
from hivemind.queen.errors import WardenSpawnRefusedError
from hivemind.queen.queen import Queen
from hivemind.queen.trail import TrailSegmentReceiver
from hivemind.swarm.gateway import GatewayHub
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop
from waggle.errors import CodecError, ConnectionLostError, SignatureError
from waggle.ids import CellId, HiveId, NodeId, WardenId
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.forage.hosting import CapacityReport
from waggle.messages.labels import OsFamily
from waggle.messages.reports import PlatformReport
from waggle.signing import Ed25519Signer
from waggle.transport.websocket import WebSocketTransport
from waggle.transport.websocket_server import DEFAULT_HOST, OS_ASSIGNED_PORT, WebSocketServer

__all__ = [
    "FANOUT_QUEUE_SIZE",
    "AnswerClaims",
    "CellListener",
    "CellListenerDeps",
    "SnapshotRequestHandler",
]

# A placeholder ForageCapacity for a Cell that has reported CellReady/CellHeartbeat but no
# forage.capacity_report (CapacityReport), e.g. a stubbed connection in a unit test. Zero-capacity
# rather than a guess, so a caller that reads it sees "unknown", not a real-looking number.
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


@dataclass(frozen=True, slots=True)
class CellListenerDeps:
    """Every collaborator one CellListener is built with (codingrules section 5.1).

    Attributes:
        gate: Registers each Cell's expected key and resolves once it is ready; also this
            listener's own `GateVerifier` key source.
        queen_signer: Signs every frame the Queen sends over this listener's connections.
        queen_node_id: The node id the Queen signs her own frames as; stamped on every `Hop`.
        hive_id: The Queen's own bee address; the `sender` of every envelope this listener sends.
        host: The interface to bind (`[virtual_cells] listen_host`).
        port: The port to bind, 0 for OS-assigned (`[virtual_cells] listen_port`).
        recorder: The Queen's trail and identity, where what a link refused is recorded (roadmap
            step 10.6); None records nothing, for a test that never reads the trail.
    """

    gate: QueenReadinessGate
    queen_signer: Ed25519Signer
    queen_node_id: NodeId
    hive_id: HiveId
    host: str = DEFAULT_HOST
    port: int = OS_ASSIGNED_PORT
    recorder: TrailRecorder | None = None


class CellListener:
    """Accept every Virtual Cell's outbound connection, verify it, and attach it to a Queen."""

    def __init__(self, deps: CellListenerDeps, clock: Clock) -> None:
        """Build a CellListener; call `start(queen)` to begin accepting.

        Args:
            deps: Every collaborator this listener needs.
            clock: Stamps every `CellSnapshotReply`/`CellRollbackReply` this listener sends back.
        """
        self._deps = deps
        codec = Codec(signer=deps.queen_signer, verifier=GateVerifier(deps.gate))
        # A handshake that fails its signature proves no Cell: recorded about the Hive (10.6).
        self._handshakes = HandshakeRefusals(deps.recorder)
        # The transport's own defences (ADR-0055), on as they are on the swarm listener: a flood
        # closes the connection (4029), a replayed envelope id is dropped, and each is recorded.
        # A Cell's Warden that reconnects replays its outbox, whose duplicates are expected for a
        # short grace after it attaches: this hub only remembers when each node last did.
        self._attached = GatewayHub(clock)
        options = transport_options(deps.recorder, clock, self._attached)

        def make_server(port: int) -> WebSocketServer:
            """Build the server on `port`: `start` may bind the last Queen's (`bind_sticky`)."""
            return WebSocketServer(codec, host=deps.host, port=port, **options)

        self._make_server = make_server
        self._server = make_server(deps.port)
        self._accept_task: asyncio.Task[None] | None = None
        self._handlers: set[asyncio.Task[None]] = set()
        self._queen: Queen | None = None
        # Late-bound (the same shape `LifecycleVirtualCellProvider.bind_queen` uses, for the
        # identical reason): the composition root's own
        # `CellSnapshotHandler`/`TrailSegmentReceiver` need this listener to already exist (for
        # `_build_registry`'s own lazy endpoint closures), so neither can be a `CellListenerDeps`
        # field. Unbound, neither kind a link may carry is answered or merged -- a Hive with no
        # Virtual backend wired for snapshotting or offline trail sync yet.
        self._services = LinkServices(clock=clock, recorder=deps.recorder)
        self._ports: ListenerPorts | None = None  # Late-bound too: see `bind_ports`.

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
        self._services.snapshots = handler

    def bind_trail_receiver(self, receiver: TrailSegmentReceiver) -> None:
        """Merge every `swarm.trail_segment_sync` this listener drains into `receiver` from now on.

        Args:
            receiver: Built over the Queen's own Pheromone Trail.
        """
        self._services.trail = receiver

    def bind_ports(self, ports: ListenerPorts) -> None:
        """Keep the port the OS chooses in `ports`, and bind the kept one first from now on.

        Late-bound like the other services: the store lives on the Queen's deps, built after this
        listener (ADR-0062: a restarted Queen binds the port her Cells still dial).

        Args:
            ports: The Queen's Requeening store.
        """
        self._ports = ports

    def bind_answer_claims(self, claims: AnswerClaims) -> None:
        """Hand every frame `claims` takes to its waiting Queen-side caller from now on.

        Args:
            claims: Shared with whatever registers the waits (the composition root hands the
                same `hivemind.queen.cell_gate.scrub.ScrubAcks` to `make_scrub`).
        """
        self._services.claims = claims

    async def start(self, queen: Queen) -> None:
        """Bind the listener and begin accepting connections, attaching each to `queen`.

        Args:
            queen: Every verified connection is attached to, and detached from, this Queen.
        """
        self._queen = queen
        # The port the Cells were told, when the OS chose it last time (ADR-0062).
        self._server = await bind_sticky(self._make_server, self._deps.port, self._ports)
        self._accept_task = asyncio.ensure_future(self._accept_loop())

    async def stop(self) -> None:
        """Stop accepting, close every connection, and wait for every handler to finish.

        Cancels every still-running handler explicitly: a handler attached to the Queen never
        reads its own transport again once attached, so it never notices `self._server.close()`
        closing the underlying connection out from under it on its own; without an explicit
        cancel here, `stop()` would wait forever on a handler that has nothing left to wait for.
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
        """Drain one accepted connection for CellReady then CellHeartbeat, then serve its link.

        The connection's frames are read through one iterator from here to the end of the link:
        the handshake reads it, and the shared attach path hands the same iterator to the
        fan-out's pump, because a transport allows one `receive()` stream at a time.
        """
        frames = transport.receive()
        binding = await _await_ready(frames, self._handshakes, transport.remote_address)
        if binding is None:
            await transport.close()
            return  # Never became ready; nothing was attached, so nothing to detach either.
        assert self._queen is not None  # noqa: S101 - start() always runs before a connection.
        hop = Hop(
            sender=self._deps.hive_id, recipient=binding.warden_id, node_id=self._deps.queen_node_id
        )
        proved = ProvedLink(
            cell=_cell_from_binding(binding),
            node_id=binding.node_id,  # Proved by the handshake: the node that speaks for the Cell.
            first=binding.ready,
            transport=transport,
            frames=frames,
            hop=hop,
        )

        def returning() -> None:
            """Note a returning Cell's replay as expected before its first frame is read."""
            self._attached.note_attach(binding.node_id)

        async def resolve(link: WardenLink, reconnected: bool) -> None:
            """Resolve the gate once the Warden is attached (module docstring)."""
            if reconnected:
                self._attached.note_attach(binding.node_id)  # The grace runs from here too.
            self._deps.gate.resolve(binding.cell_id, binding.node_id, binding.info)

        hooks = LinkHooks(on_returning=returning, on_attached=resolve)
        try:
            await serve_warden(self._queen, proved, self._services, hooks)
        except WardenSpawnRefusedError:
            # The Queen's set refused this Warden (the refusal is already on the trail): it is
            # never attached, so there is nothing to detach, only the connection to close.
            await transport.close()


@dataclass(frozen=True, slots=True)
class _ReadyBinding:
    """What one connection's own CellReady + CellHeartbeat handshake resolved to."""

    cell_id: CellId
    warden_id: WardenId
    node_id: NodeId
    comb_shield: CombShieldLevel
    info: CellReadyInfo
    ready: Envelope  # The verified CellReady that identified the Warden.


async def _await_ready(
    frames: AsyncIterator[Envelope], handshakes: HandshakeRefusals, address: str
) -> _ReadyBinding | None:
    """Drain `frames` until a verified CellReady then a CellHeartbeat both arrive, or it ends.

    Reads the connection's one frame iterator and leaves it positioned just after the heartbeat,
    still open: the shared attach path hands the same iterator to the fan-out's pump. The Cell's
    `CapacityReport`, which a real in-Cell Warden sends between the two, is captured too (module
    docstring). A frame whose signature fails here proves no Cell, so it is recorded through
    `handshakes` about the Hive, with `address` and what it claimed kept apart
    (`hivemind.queen.cell_gate.trust.handshake`).

    Returns:
        The binding once both frames arrived, or None if the connection ended (or a frame failed
        to verify) before they did.
    """
    ready: Envelope | None = None
    capacity: ForageCapacity | None = None
    try:
        async for envelope in frames:
            payload = envelope.payload
            if isinstance(payload, CellReady):
                ready = envelope
            elif isinstance(payload, CapacityReport) and ready is not None:
                assert isinstance(ready.payload, CellReady)  # noqa: S101 - set from a CellReady.
                capacity = _capacity_from_report(payload, ready.payload.platform)
            elif isinstance(payload, CellHeartbeat) and ready is not None:
                return _binding(ready, capacity)
    except SignatureError as error:
        # Roadmap step 10.6: the Guard Bee hears of the knock, attributed to no Cell.
        await handshakes.refused(error, address)
    except (ConnectionLostError, CodecError):
        pass  # The link ended, or a frame did not decode; report "never became ready" below.
    return None


def _binding(ready: Envelope, capacity: ForageCapacity | None) -> _ReadyBinding:
    """Build the binding a verified CellReady and its Cell's capacity (if any) resolve to."""
    payload = ready.payload
    assert isinstance(payload, CellReady)  # noqa: S101 - the caller passes a CellReady envelope.
    return _ReadyBinding(
        cell_id=payload.cell_id,
        warden_id=payload.warden_id,
        node_id=ready.node_id,
        comb_shield=CombShieldLevel.from_wire(payload.comb_shield),
        info=CellReadyInfo(
            capabilities=CellCapabilities.from_wire(payload.platform, payload.capabilities),
            # The real report when one arrived; _PLACEHOLDER_CAPACITY (all zeros) only for a Cell
            # that never sends one (module docstring).
            capacity=capacity if capacity is not None else _PLACEHOLDER_CAPACITY,
        ),
        ready=ready,
    )


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
        # The Queen provisioned this Cell, so she knows its processes are contained (ADR-0060);
        # its own report never carries that.
        capabilities=binding.info.capabilities.model_copy(update={"processes_contained": True}),
        capacity=binding.info.capacity,
        access_level=AccessLevel.FULL,  # Cell's own validator: every VIRTUAL Cell is FULL.
        comb_shield=binding.comb_shield,
    )
