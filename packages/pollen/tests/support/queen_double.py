"""Script a Queen: a real loopback WebSocket listener that speaks Waggle as the Hive Stand does.

The Pollen Packet (the thin gateway program on a device the Hive borrows) dials the Queen (the
Hive's central orchestrator) and never listens, so testing it end to end needs something to dial.
This is that something: a ``websockets`` server on 127.0.0.1 (plain, or TLS with a self-signed
certificate for the pin tests) that signs every frame it sends with a Hive key, verifies every
frame it receives with the keys the device registered, applies the first-frame rule of enrolment
(the only unverified frame is a ``swarm.enrol_request``, checked against the key inside it) and
records everything it hears. A test drives it like a script: ``await queen.next_session()`` waits
for a packet to connect and announce itself, ``await session.expect(LeaseOpened)`` waits for a
frame of a type, ``await session.send(CellRequest(...))`` speaks as the Queen or the Warden, and
``session.send_frame(raw)`` puts any bytes on the wire (a tampered frame, a replayed one).
Nothing here is the real swarm listener; it is only as clever as the tests need.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by the pollen end-to-end tests through
    conftest.py; depends on websockets, waggle and frame_overrides.py, and imports nothing from
    pollen.

Key invariants:
    - The frames it sends are signed with the Hive key and carry the Hive node's id; the frames it
      accepts are verified with the keys registered for the device, so a tampered or unsigned
      frame closes the connection with 1008, as the real listener does.
    - ``expect`` consumes the frame it returns, so a later ``expect`` of the same type returns
      the next one; frames of other types stay available.
    - Every connection and the listener itself are closed by ``stop``.

See Also:
    - waggle.transport.websocket_server for the real listener this stands in for.
    - queen_tls.py for the certificates it can serve.
    - docs/waggle/enrolment.md section 5.3 for the first-frame rule.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from frame_overrides import Overrides
from queen_tls import TlsMaterial
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from waggle.clock import Clock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import CodecError, SignatureError
from waggle.ids import (
    MessageId,
    new_cell_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.base import WaggleMessage
from waggle.messages.control import ErrorMessage
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.swarm import EnrolAccept, EnrolRequest, NodeKey
from waggle.signing import Ed25519Signer, Ed25519Verifier, public_key_hex
from waggle.transport.tls import server_ssl_context

DEFAULT_WAIT_S = 10.0  # Real seconds a test waits for a frame before it fails.
INVITE_CODE = "H7QK-3MZP-WD2F-XNVA-R5TG-C4YB-J6"  # The single-use token the scripted Queen accepts.
CAPABILITIES = {
    AccessLevel.READ_ONLY: ("fs:read:**",),
    AccessLevel.SCRATCH: ("fs:read:**", "fs:write:{scratch}/**", "exec:*"),
    AccessLevel.FULL: (
        "fs:read:**",
        "fs:write:{scratch}/**",
        "exec:*",
        "fs:write:**",
        "cell:outside_scratch:**",
    ),
}  # The guard's grants per access level, as enrolment.md section 7.1 lists them.

__all__ = [
    "DEFAULT_WAIT_S",
    "INVITE_CODE",
    "Frame",
    "QueenClosedError",
    "QueenDouble",
    "QueenSession",
]


@dataclass(frozen=True, slots=True)
class Frame[PayloadT: WaggleMessage]:
    """A frame the packet sent, with its payload typed as the test asked for it."""

    envelope: Envelope  # The whole frame, as the Queen decoded it.
    payload: PayloadT  # Its payload, known to be of the requested class.

    @property
    def id(self) -> MessageId:
        """The envelope's id."""
        return self.envelope.id

    @property
    def correlation_id(self) -> MessageId | None:
        """The request the frame answers, if any."""
        return self.envelope.correlation_id

    @property
    def sender(self) -> str:
        """The sender's bee address."""
        return self.envelope.sender

    @property
    def recipient(self) -> str:
        """The recipient's bee address."""
        return self.envelope.recipient

    @property
    def version(self) -> str:
        """The protocol version the sender wrote."""
        return self.envelope.version


class QueenClosedError(AssertionError):
    """The packet's connection closed while a test was still waiting for a frame."""


class QueenSession:
    """One connection from a packet, as the Queen sees it."""

    def __init__(self, queen: QueenDouble, connection: ServerConnection) -> None:
        """Wrap an accepted connection; ``run`` starts reading it."""
        self._queen = queen
        self._connection = connection
        self.frames: list[Envelope] = []  # Every frame received, in order.
        self.refusal: str | None = None  # The error code that made the Queen close the link.
        self.close_code: int | None = None
        self.is_closed = False
        self._taken: set[int] = set()
        self._changed = asyncio.Event()

    @property
    def device_id(self) -> str:
        """The bee address of the packet, from its first frame."""
        return self.frames[0].sender

    async def run(self) -> None:
        """Read frames until the connection ends, answering an enrolment request."""
        try:
            async for raw in self._connection:
                if not isinstance(raw, bytes) or not await self._admit(raw):
                    await self._connection.close(1008, self.refusal or "waggle.codec.malformed")
                    break
        except ConnectionClosed:
            pass
        finally:
            self.close_code = self._connection.close_code
            self.is_closed = True
            self._changed.set()
            self._queen.note_arrival()  # A waiting ``next_session`` must see a silent one end.

    async def _admit(self, raw: bytes) -> bool:
        """Decode and record one frame; False if it must close the connection."""
        try:
            envelope = self._queen.decode(raw, first=not self.frames)
        except (CodecError, SignatureError) as error:
            self.refusal = error.code
            return False
        self.frames.append(envelope)
        self._changed.set()
        self._queen.note_arrival()
        if self._queen.is_revoking:
            self.refusal = "waggle.signature.unknown_node"
            return False  # As the real listener does for a revoked node: close at its first frame.
        if isinstance(envelope.payload, EnrolRequest):
            await self._queen.answer_enrolment(self, envelope)
        return True

    async def expect[PayloadT: WaggleMessage](
        self,
        kind: type[PayloadT],
        *,
        wait_s: float = DEFAULT_WAIT_S,
        where: Callable[[Envelope], bool] | None = None,
    ) -> Frame[PayloadT]:
        """Wait for the next unconsumed frame whose payload is a ``kind``.

        Args:
            kind: The payload class wanted.
            wait_s: Real seconds to wait.
            where: An extra test the frame must pass.

        Returns:
            The frame, now consumed, with its payload typed as ``kind``.

        Raises:
            QueenClosedError: The connection ended and no such frame had arrived.
            TimeoutError: No such frame arrived in time.
        """
        envelope = await self.expect_envelope(kind, wait_s=wait_s, where=where)
        payload = envelope.payload
        assert isinstance(payload, kind)
        return Frame(envelope, payload)

    async def expect_envelope(
        self,
        *types: type[WaggleMessage],
        wait_s: float = DEFAULT_WAIT_S,
        where: Callable[[Envelope], bool] | None = None,
    ) -> Envelope:
        """Wait for the next unconsumed frame whose payload is one of ``types`` (any if none).

        Args:
            types: The payload classes wanted; any frame when empty.
            wait_s: Real seconds to wait.
            where: An extra test the frame must pass.

        Returns:
            The frame, now consumed.

        Raises:
            QueenClosedError: The connection ended and no such frame had arrived.
            TimeoutError: No such frame arrived in time.
        """
        async with asyncio.timeout(wait_s):
            while True:
                for index, envelope in enumerate(self.frames):
                    wanted = not types or isinstance(envelope.payload, types)
                    if index not in self._taken and wanted and (where is None or where(envelope)):
                        self._taken.add(index)
                        return envelope
                if self.is_closed:
                    raise QueenClosedError("The connection closed before the frame arrived.")
                self._changed.clear()
                await self._changed.wait()

    async def expect_none(self, *types: type[WaggleMessage], within: float = 0.4) -> None:
        """Assert that no frame of ``types`` arrives within ``within`` seconds.

        Raises:
            AssertionError: One did.
        """
        try:
            found = await self.expect_envelope(*types, wait_s=within)
        except (TimeoutError, QueenClosedError):
            return
        raise AssertionError(f"Unexpected frame of kind {found.kind}.")

    async def send(
        self,
        payload: WaggleMessage,
        *,
        sender: str | None = None,
        overrides: Overrides | None = None,
    ) -> Envelope:
        """Sign and send a frame to the packet; by default as the Queen.

        Args:
            payload: The message.
            sender: The sender's bee address; the Queen's by default (use ``queen.warden_id``).
            overrides: What should differ from a plain frame: the recipient, the send time (a
                stale or future frame), an envelope id (the first chunk of a put-file names its
                own) and the request a reply answers.

        Returns:
            The envelope sent, so a test can resend its exact bytes.
        """
        changes = overrides or Overrides()
        envelope = self._queen.build(payload, changes.recipient or self.device_id, sender, changes)
        await self._connection.send(self._queen.encode(envelope))
        return envelope

    async def send_frame(self, raw: bytes) -> None:
        """Put exact bytes on the wire (a replayed or tampered frame)."""
        await self._connection.send(raw)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the connection from the Queen's side."""
        await self._connection.close(code, reason)


class QueenDouble:
    """The scripted Queen: identity, keys, listener and the sessions it has seen."""

    def __init__(self, clock: Clock, tls: TlsMaterial | None = None) -> None:
        """Mint the Hive's identity; nothing listens until ``start``.

        Args:
            clock: Stamps ids and ``sent_at``.
            tls: Serve ``wss://`` with this certificate; plain ``ws://`` when None.
        """
        self.clock = clock
        self.tls = tls
        self.hive_signer = Ed25519Signer.generate()
        self.hive_id = new_hive_id(clock)
        self.hive_node_id = new_node_id(clock)
        self.warden_id = new_warden_id(clock)
        self.cell_id = new_cell_id(clock)
        self.sessions: list[QueenSession] = []
        self.enrolments: list[Envelope] = []
        self.access_cap = AccessLevel.FULL
        self.offline_limit_s = 120.0
        self.heartbeat_interval_s = 0.2
        self.decide: Callable[[EnrolRequest], WaggleMessage] | None = None
        self.is_revoking = False  # When true, every connection is closed (1008) at its first frame.
        self.is_silent = False  # When true, an enrolment request gets no answer at all.
        self._keys: dict[str, bytes] = {}
        self._server: Server | None = None
        self._port = 0
        self._handed = 0
        self._arrived = asyncio.Event()

    @property
    def url(self) -> str:
        """The address a packet dials."""
        return f"{'wss' if self.tls else 'ws'}://127.0.0.1:{self._port}"

    @property
    def port(self) -> int:
        """The port the listener is (or was) bound to."""
        return self._port

    @property
    def pin(self) -> str | None:
        """The certificate fingerprint a packet pins, or None for plain ``ws://``."""
        return None if self.tls is None else self.tls.sha256

    @property
    def hive_key_hex(self) -> str:
        """The Hive key a packet pins."""
        return public_key_hex(self.hive_signer.public_key_bytes)

    async def start(self, port: int = 0) -> None:
        """Listen on loopback (the same port again after a ``stop``, when given)."""
        context = (
            None if self.tls is None else server_ssl_context(self.tls.cert_path, self.tls.key_path)
        )
        self._server = await serve(self._accept, "127.0.0.1", port, ssl=context)
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Close every connection and the listener."""
        if self._server is None:
            return
        self._server.close(close_connections=True)
        await self._server.wait_closed()
        self._server = None

    def register(self, node_id: str, public_key: bytes) -> None:
        """Trust a device's key (what enrolment pins)."""
        self._keys[node_id] = public_key

    def invite(self, *, expires_in_s: float = 900.0, label: str | None = "test") -> str:
        """Mint the ``pollen1.`` string for this listener."""
        body = {
            "token": INVITE_CODE,
            "url": self.url,
            "hive_id": self.hive_id,
            "hive_node_id": self.hive_node_id,
            "hive_key": self.hive_key_hex,
            "tls_sha256": self.pin,
            "expires_at": (self.clock.now() + timedelta(seconds=expires_in_s)).isoformat(),
            "label": label,
        }
        packed = json.dumps(body, separators=(",", ":")).encode()
        return "pollen1." + base64.urlsafe_b64encode(packed).rstrip(b"=").decode()

    async def next_session(self, wait_s: float = DEFAULT_WAIT_S) -> QueenSession:
        """Wait for a packet connection that has sent its first frame; each is returned once."""
        async with asyncio.timeout(wait_s):
            while True:
                if self._handed < len(self.sessions):
                    candidate = self.sessions[self._handed]
                    if candidate.frames:
                        self._handed += 1
                        return candidate
                    if candidate.is_closed:
                        self._handed += 1  # It ended without ever speaking: nothing to hand over.
                        continue
                self._arrived.clear()
                await self._arrived.wait()

    def build(
        self,
        payload: WaggleMessage,
        recipient: str,
        sender: str | None = None,
        overrides: Overrides | None = None,
    ) -> Envelope:
        """Wrap a payload as a frame from the Hive node."""
        changes = overrides or Overrides()
        hop = Hop(sender=sender or self.hive_id, recipient=recipient, node_id=self.hive_node_id)
        link = None if changes.correlation_id is None else MessageId(changes.correlation_id)
        envelope = wrap(payload, hop, clock=self.clock, correlation_id=link)
        fixed = {
            key: value
            for key, value in (("sent_at", changes.sent_at), ("id", changes.message_id))
            if value is not None
        }
        return envelope.model_copy(update=fixed)

    def encode(self, envelope: Envelope) -> bytes:
        """Sign an envelope with the Hive key and serialise it."""
        return Codec(signer=self.hive_signer).encode(envelope)

    def decode(self, raw: bytes, *, first: bool) -> Envelope:
        """Decode a frame from a device, applying the first-frame rule of enrolment."""
        if first:
            peek = json.loads(raw)
            if peek.get("kind") == "swarm.enrol_request":
                body = peek["payload"]
                key = bytes.fromhex(body["public_key_hex"])
                return Codec(verifier=Ed25519Verifier({body["node_id"]: key})).decode(raw)
        return Codec(verifier=Ed25519Verifier(dict(self._keys))).decode(raw)

    async def answer_enrolment(self, session: QueenSession, envelope: Envelope) -> None:
        """Pin the device's key and send the accept (or whatever ``decide`` returns)."""
        request = envelope.payload
        assert isinstance(request, EnrolRequest)
        self.enrolments.append(envelope)
        if self.is_silent:
            return
        self.register(request.node_id, bytes.fromhex(request.public_key_hex))
        reply = self.decide(request) if self.decide else self.accept_for(request)
        await session.send(reply, overrides=Overrides(correlation_id=envelope.id))

    def accept_for(self, request: EnrolRequest) -> EnrolAccept:
        """The accept the Hive would send: the lower access level, the guard's grants."""
        asked = request.requested_access
        level = asked if asked.rank <= self.access_cap.rank else self.access_cap
        grants = (*CAPABILITIES[level], f"watch:{self.cell_id}")
        return EnrolAccept(
            device_id=request.device_id,
            cell_id=self.cell_id,
            hive_id=self.hive_id,
            hive_key_hex=self.hive_key_hex,
            trusted_nodes=(NodeKey(node_id=self.hive_node_id, public_key_hex=self.hive_key_hex),),
            warden_id=self.warden_id,
            access_level=level,
            comb_shield=CombShieldLevel.MEADOW,
            granted_capabilities=grants,
            offline_limit_s=self.offline_limit_s,
            heartbeat_interval_s=self.heartbeat_interval_s,
            reason="Admitted by the scripted Queen.",
        )

    def deny(self, _request: EnrolRequest) -> WaggleMessage:
        """A ``decide`` that refuses every request, as a spent token would be."""
        return ErrorMessage(
            code="hive.swarm.enrol_denied",
            message="The invite was refused.",
            failed_kind="swarm.enrol_request",
            is_retryable=False,
        )

    def note_arrival(self) -> None:
        """Wake ``next_session``: a session has a frame to look at, or has ended."""
        self._arrived.set()

    async def _accept(self, connection: ServerConnection) -> None:
        """Handle one accepted connection until it ends."""
        session = QueenSession(self, connection)
        self.sessions.append(session)
        await session.run()
