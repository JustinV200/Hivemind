"""Provide SwarmListener: the Hive Stand's door for Pollen Packets, and the Wardens on devices.

The swarm listener is the one place a device's Pollen Packet (the thin gateway installed on an
enrolled machine) dials in. It is a `waggle.transport.websocket_server.WebSocketServer` on `[swarm]
listen_host`/`listen_port` with the listener-side defences on: TLS from `[swarm] tls_cert` and
`tls_key` when they are set (a packet pins the certificate's SHA-256 fingerprint from its invite
instead of trusting a certificate authority), a frame ceiling per connection and one replay
window for all of them, with each refusal recorded as a guard event by `TransportRefusals`. Every
frame is signed: the codec's verifier is `SwarmVerifier` over a `KeyRing` of enrolled nodes, plus
the one exception, a new device's self-signed enrolment request. Each accepted connection gets a
task that runs the `ConnectionRouter`, which decides from the first frame whether it is an
enrolment, a device's packet or a device's Warden. The listener also runs the liveness sweep
that marks a silent device unreachable. It is a Layer 3 service the Queen's swarm gate (Layer 6)
starts, stops and asks questions of: which devices are connected (`hub`), disconnect one
(`disconnect`), trust or stop trusting keys after the operator enrols or revokes (`sync_keys`),
and where to send a colonized device's Warden connection (`bind_warden_handler`).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    Built by the composition root when `[swarm] enabled`; started and stopped by the Queen's
    swarm gate. Calls the registry, the enrolment door, the hub, the router, the heartbeat monitor
    and waggle's server, codec and TLS helpers.

Key invariants:
    - Every connection accepted is served by a task this listener owns, and `stop` cancels and
      awaits every one; none is left running, and none marks its device unreachable because the
      Hive Stand is stopping.
    - The listener serves `wss://` exactly when a certificate and key are configured, and a
      configuration with only one of the pair is refused (the schema already insists).
    - The fingerprint a device pins is read from the same certificate file the listener serves,
      so the invite and the listener cannot disagree.
    - Approval and enrolment are not a remote surface of the Hive: a device enrols only with an
      invite an operator minted at the Hive Stand.

See Also:
    - docs/waggle/enrolment.md sections 2, 5, 8 and 9 for the listener, the first frame and TLS.
    - hivemind.swarm.gateway.routing for what each connection is served as.
    - hivemind.queen.cell_gate.listener for the listener of Virtual Cells' Wardens.
"""

from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass
from pathlib import Path

from hivemind.common.logging import get_logger
from hivemind.manifest import SwarmSection
from hivemind.swarm.enrolment import EnrolmentDoor
from hivemind.swarm.errors import SwarmError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway.frames import HiveVoice
from hivemind.swarm.gateway.heartbeat import HeartbeatMonitor, LivenessLoop, LivenessRules
from hivemind.swarm.gateway.hub import GatewayHub
from hivemind.swarm.gateway.refusals import TransportRefusals
from hivemind.swarm.gateway.routing import ConnectionRouter, RouterDeps, WardenConnectionHandler
from hivemind.swarm.gateway.verifier import KeyRing, SwarmVerifier
from hivemind.swarm.registry import SwarmRegistry
from waggle.codec import Codec
from waggle.ids import DeviceId
from waggle.signing import Ed25519Signer
from waggle.transport.limits import ServerLimits
from waggle.transport.tls import certificate_sha256, server_ssl_context
from waggle.transport.websocket_server import WebSocketServer

log = get_logger(__name__)

# The first certificate in a PEM file is the one the listener presents; a chain follows it.
_PEM_CERTIFICATE = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL)

__all__ = ["ListenerConfig", "ListenerDeps", "SwarmListener", "certificate_pin"]


def certificate_pin(cert_path: Path) -> str:
    """Return the pin form of a PEM certificate file: the SHA-256 of its first certificate's DER.

    Args:
        cert_path: A PEM file holding the listener's certificate, and any chain behind it.

    Returns:
        64 lowercase hex characters: what an invite carries and a packet pins.

    Raises:
        SwarmError: The file holds no certificate.
        FileNotFoundError: The file does not exist.
    """
    match = _PEM_CERTIFICATE.search(cert_path.read_text(encoding="ascii"))
    if match is None:
        raise SwarmError(f"The TLS certificate file {cert_path} holds no PEM certificate.")
    return certificate_sha256(ssl.PEM_cert_to_DER_cert(match.group(0)))


@dataclass(frozen=True, slots=True)
class ListenerConfig:
    """Where the swarm listener binds, whether it serves TLS, and how liveness is judged.

    Attributes:
        host: The interface to bind (`[swarm] listen_host`).
        port: The port to bind; 0 lets the OS choose (tests).
        tls_cert: The PEM certificate chain to serve, or None for plaintext `ws://`.
        tls_key: The PEM private key for `tls_cert`; set exactly when it is.
        advertise_url: The URL devices are told to dial, or "" to derive it from host and port.
        liveness: How many beats a device may miss and how often silence is judged.
    """

    host: str
    port: int
    tls_cert: Path | None
    tls_key: Path | None
    liveness: LivenessRules
    advertise_url: str = ""

    @classmethod
    def from_section(cls, section: SwarmSection, base_dir: Path) -> ListenerConfig:
        """Build the config from the manifest's `[swarm]` section.

        Args:
            section: The loaded `[swarm]` section.
            base_dir: The manifest's own directory, which relative certificate paths resolve
                against.

        Returns:
            The config, with the certificate and key made absolute under `base_dir`.
        """
        return cls(
            host=section.listen_host,
            port=section.listen_port,
            tls_cert=base_dir / section.tls_cert if section.tls_cert else None,
            tls_key=base_dir / section.tls_key if section.tls_key else None,
            liveness=LivenessRules.from_section(section),
            advertise_url=section.advertise_url,
        )

    @property
    def invite_url(self) -> str:
        """The URL an invite tells a device to dial: the advertised one, else the bind address."""
        if self.advertise_url:
            return self.advertise_url
        scheme = "wss" if self.tls_cert is not None else "ws"
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{scheme}://{host}:{self.port}"

    @property
    def tls_pin(self) -> str | None:
        """The fingerprint an invite carries: the served certificate's, or None without TLS."""
        return None if self.tls_cert is None else certificate_pin(self.tls_cert)


@dataclass(frozen=True, slots=True)
class ListenerDeps:
    """Every collaborator the swarm listener is built with (codingrules 5.1).

    Attributes:
        registry: Where nodes and invites live.
        door: Admits a new device with an invite.
        hub: The directory of live device links; the Queen's swarm gate subscribes to it.
        recorder: The trail, clock and identity the listener records as.
        voice: The Hive Stand's bee address and node id, the sender of every frame it sends.
        signer: Signs every frame the Hive Stand sends; its node id is `voice.hive_node_id`.
    """

    registry: SwarmRegistry
    door: EnrolmentDoor
    hub: GatewayHub
    recorder: SwarmRecorder
    voice: HiveVoice
    signer: Ed25519Signer


class SwarmListener:
    """Accept Pollen Packets and device Wardens, verify them and serve them until stopped."""

    def __init__(self, config: ListenerConfig, deps: ListenerDeps) -> None:
        """Build the listener; call `start()` to bind and begin accepting.

        Args:
            config: Where to bind, whether to serve TLS and how liveness is judged.
            deps: Every collaborator it works with.

        Raises:
            FileNotFoundError: A configured certificate or key file does not exist.
            ssl.SSLError: The certificate and key are not a matching PEM pair.
        """
        clock = deps.recorder.clock
        self._deps = deps
        self._config = config
        self._ring = KeyRing()
        monitor = HeartbeatMonitor(deps.registry, deps.hub, deps.recorder, config.liveness)
        self._router = ConnectionRouter(
            RouterDeps(deps.registry, deps.door, deps.hub, monitor, self._ring, deps.voice, clock)
        )
        tls = _tls_context(config)
        self._server = WebSocketServer(
            Codec(signer=deps.signer, verifier=SwarmVerifier(self._ring)),
            host=config.host,
            port=config.port,
            limits=ServerLimits(),
            ssl_context=tls,
            on_refusal=TransportRefusals(deps.recorder, deps.hub, deps.voice.hive_id),
            clock=clock,
        )
        self._liveness = LivenessLoop(monitor, clock, config.liveness.sweep_interval_s)
        self._accept_task: asyncio.Task[None] | None = None
        self._liveness_task: asyncio.Task[None] | None = None
        self._handlers: set[asyncio.Task[None]] = set()

    @property
    def uri(self) -> str:
        """The URI the listener is reachable at (`ws://` or `wss://`); valid once started."""
        return self._server.uri

    @property
    def hub(self) -> GatewayHub:
        """The directory of live device links this listener fills."""
        return self._deps.hub

    @property
    def voice(self) -> HiveVoice:
        """The Hive Stand's own address: the sender of every frame this listener sends."""
        return self._deps.voice

    @property
    def recorder(self) -> SwarmRecorder:
        """The trail, clock and identity the Swarm records its events with."""
        return self._deps.recorder

    @property
    def warden_url(self) -> str:
        """The URL a device's Warden is told to dial: the advertised one, else where this listens.

        Valid once the listener has started: with no `advertise_url` it is the address actually
        bound, which is the one a test with an OS-chosen port can reach.
        """
        return self._config.advertise_url or self._server.uri

    def bind_warden_handler(self, handler: WardenConnectionHandler | None) -> None:
        """Hand every verified device Warden connection to `handler` from now on.

        Args:
            handler: The Queen's swarm gate; None leaves such connections closed.
        """
        self._router.bind_warden_handler(handler)

    async def start(self) -> None:
        """Trust the enrolled nodes' keys, bind the port and begin accepting and sweeping."""
        await self.sync_keys()
        await self._server.start()
        self._accept_task = asyncio.ensure_future(self._accept_loop())
        self._liveness_task = asyncio.ensure_future(self._liveness.run())

    async def stop(self) -> None:
        """Stop sweeping, end every connection and wait for every task this listener started.

        Connections are cancelled before the server is closed, so a Hive Stand that is stopping
        does not record every device as having gone unreachable.
        """
        self._liveness.stop()
        tasks = [*self._handlers, *(t for t in (self._liveness_task,) if t is not None)]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._server.close()
        if self._accept_task is not None:
            await asyncio.gather(self._accept_task, return_exceptions=True)

    async def sync_keys(self) -> None:
        """Trust every enrolled node's key and stop trusting every revoked node's.

        Called at start and by the swarm gate after the operator enrols or revokes a device, so
        a revoked device's next frame fails like any forgery.
        """
        self._ring.sync(await self._deps.registry.nodes())

    async def disconnect(self, device_id: DeviceId) -> bool:
        """Close a device's live link, if it has one.

        Args:
            device_id: The device to cut off (a revoked one, say).

        Returns:
            True when a link was open and is now closed.
        """
        link = self._deps.hub.link(device_id)
        if link is None:
            return False
        await link.close()
        return True

    async def _accept_loop(self) -> None:
        """Serve each accepted connection in a task of its own until the server closes."""
        async for transport in self._server.connections():
            # Never awaited here: a slow or stuck peer must not delay the next accept, and `stop`
            # cancels and awaits every entry of `_handlers`, so no task is orphaned.
            task = asyncio.ensure_future(self._router.serve(transport))
            self._handlers.add(task)
            task.add_done_callback(self._handler_done)

    def _handler_done(self, task: asyncio.Task[None]) -> None:
        """Forget a finished connection task, logging one that died of a bug."""
        self._handlers.discard(task)
        if not task.cancelled() and task.exception() is not None:
            # The type only, for the same reason as the link reader: a repr can carry a frame.
            log.error("swarm.connection_crashed", error=type(task.exception()).__name__)


def _tls_context(config: ListenerConfig) -> ssl.SSLContext | None:
    """Build the TLS context the listener serves, or None when it serves plaintext."""
    if config.tls_cert is None or config.tls_key is None:
        return None
    return server_ssl_context(config.tls_cert, config.tls_key)
