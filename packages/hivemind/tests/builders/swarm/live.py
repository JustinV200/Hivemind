"""Run a Hive Stand's swarm side on real time and real sockets, so a real gateway can dial it.

The Swarm tests so far drive the Hive's side with a scripted device over in-memory pairs and a
fake clock. A real Pollen Packet (or any other gateway) needs more: a listener on a port the
invite names, a clock that agrees with the packet's (a frame whose `sent_at` is hours off is
dropped, gateway contract section 7) and the Hive's own signing key. `LiveHive` is that stand: the
real `SwarmListener`, enrolment door, hub and in-memory registry on `SystemClock`, with an invite
minted for the address the listener really has. It can serve plaintext `ws://` on loopback or
`wss://` with a pinned self-signed certificate on any interface, stop and come back on the same
port (a Hive Stand restarting), and share its registry with a second stand (a Hive Stand that
moved). Its signer can sign wrongly on demand, which is how a test shows a gateway refusing a
tampered frame without reaching under the listener.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the CellSession contract
    suite's Pollen harness, the gateway conformance suite and the container integration test.

Key invariants:
    - Everything runs on `SystemClock`, because the packet does; never hand a `FakeClock` to a
      stand a real gateway dials.
    - The listener's port is chosen before the invite is minted and kept across a restart, so
      an invite minted earlier still names the stand.
    - `stop` leaves no task of the listener running; `restart` is `stop` then a new listener on
      the same port over the same registry, hub and keys.

See Also:
    - builders.swarm.hive for the same pieces on a fake clock, for the in-memory tests.
    - docs/waggle/enrolment.md section 2 for the listener a stand serves.
"""

from __future__ import annotations

import secrets
import socket
from dataclasses import dataclass, field

from builders.cells import make_identity
from builders.swarm.enrolment import make_endpoint, make_rules
from builders.swarm.hive import HubLog
from builders.swarm.parties import HiveParty
from builders.swarm.tls import TlsMaterial

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.swarm.enrolment import EnrolmentDoor, MintedInvite
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway import (
    DeviceLink,
    GatewayHub,
    HiveVoice,
    ListenerConfig,
    ListenerDeps,
    LivenessRules,
    SwarmListener,
)
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.registry import MemorySwarmRegistry
from waggle.clock import SystemClock
from waggle.ids import DeviceId, HiveId, new_hive_id, new_node_id
from waggle.signing import PRIVATE_KEY_BYTES, Ed25519Signer

__all__ = ["LiveHive", "LiveHiveOptions", "TamperableSigner", "free_port", "start_live_hive"]

MISSED_BEATS_ALLOWED = 4  # The manifest's default `heartbeat_miss_limit`.
_WRONG_BYTES = b" "  # Appended to what a tampering signer signs, so the signature fits other bytes.


class TamperableSigner(Ed25519Signer):
    """The Hive Stand's signer, able to sign the next frames wrongly on demand.

    A wrong signature here is a genuine Ed25519 signature over bytes that are not the frame's, so
    it is exactly what a frame altered in transit looks like to the receiver, and nothing about
    the frame or the connection is faked. The count is per signature, so a test says how many of
    the next frames are tampered with and the rest are untouched.
    """

    def __init__(self, private_key_bytes: bytes) -> None:
        """Load the key; no frame is tampered with until `tamper_next` is set.

        Args:
            private_key_bytes: The raw private key (seed) of the Hive Stand's node.
        """
        super().__init__(private_key_bytes)
        self.tamper_next = 0  # How many of the next signatures are made over the wrong bytes.

    def sign(self, canonical: bytes) -> str:
        """Sign `canonical`, or bytes that are not it while `tamper_next` is above zero."""
        if self.tamper_next > 0:
            self.tamper_next -= 1
            canonical = canonical + _WRONG_BYTES
        return super().sign(canonical)


@dataclass(frozen=True, slots=True)
class LiveHiveOptions:
    """How a live stand is built; every field has the default a plain loopback test wants.

    Attributes:
        host: The interface the listener binds.
        advertise_host: The name or address an invite tells a device to dial; `host` when None.
        tls: A certificate to serve `wss://` with, or None for plaintext (loopback only).
        offline_limit_s: The dead-man limit every accepted device is told.
        heartbeat_interval_s: How often every accepted device is told to report.
        registry: Nodes and invites to share with another stand, or None for an empty one.
        hive_id: The Queen's bee address, to keep it across a move; a fresh one when None.
    """

    host: str = "127.0.0.1"
    advertise_host: str | None = None
    tls: TlsMaterial | None = None
    offline_limit_s: float = 120.0
    heartbeat_interval_s: float = 15.0
    registry: MemorySwarmRegistry | None = None
    hive_id: HiveId | None = None


def free_port(host: str = "127.0.0.1") -> int:
    """Return a TCP port on `host` that nothing is listening on right now.

    Args:
        host: The interface the port will be bound on.

    Returns:
        A port number; another process could take it before the caller binds, which a test run
        accepts as the price of putting the port into an invite before the listener starts.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


@dataclass
class LiveHive:
    """A started Hive Stand on real time: the listener and everything it serves from."""

    clock: SystemClock
    party: HiveParty
    signer: TamperableSigner
    registry: MemorySwarmRegistry
    trail: MemoryPheromoneTrail
    recorder: SwarmRecorder
    door: EnrolmentDoor
    hub: GatewayHub
    log: HubLog
    config: ListenerConfig
    deps: ListenerDeps
    listener: SwarmListener
    _running: bool = field(default=True, repr=False)

    @property
    def url(self) -> str:
        """The address an invite from this stand tells a device to dial."""
        return self.config.invite_url

    @property
    def pin(self) -> str | None:
        """The certificate fingerprint an invite carries, or None on a plaintext stand."""
        return self.config.tls_pin

    async def mint(
        self,
        access: AccessLevel = AccessLevel.FULL,
        shield: CombShieldLevel = CombShieldLevel.MEADOW,
        label: str | None = None,
    ) -> MintedInvite:
        """Mint a single-use invite at the door, the way `hive swarm invite` does.

        Args:
            access: The most the device may be granted.
            shield: The Comb Shield tier the operator vouches for.
            label: The operator's name for the device.

        Returns:
            The invite; its string is for `pollen enrol`.
        """
        return await self.door.mint_invite(access, shield, label)

    async def node(self, device_id: DeviceId) -> SwarmNode:
        """Return the registry's record of an enrolled device.

        Args:
            device_id: The device.

        Returns:
            The node as the registry holds it now.

        Raises:
            LookupError: The device never enrolled.
        """
        found = await self.registry.node(device_id)
        if found is None:
            raise LookupError(f"Device {device_id} is not enrolled on this stand.")
        return found

    async def wait_for(self, device_id: DeviceId, timeout_s: float) -> DeviceLink:
        """Wait for a device's packet to dial in and verify, and return its link.

        Args:
            device_id: The device.
            timeout_s: Real seconds to wait.

        Returns:
            The device's open link.

        Raises:
            GatewayUnavailableError: The device did not connect in time.
        """
        return await self.hub.wait_for(device_id, timeout_s)

    async def kinds(self) -> list[str]:
        """The kind of every event on the stand's trail, oldest first."""
        return [event.kind for event in await self.trail.query(TrailQuery())]

    async def stop(self) -> None:
        """Stop the listener and end every connection; safe to call twice."""
        if self._running:
            self._running = False
            await self.listener.stop()

    async def restart(self) -> None:
        """Bring the stand back on the same port after `stop`, over the same registry and hub."""
        await self.stop()
        self.listener = SwarmListener(self.config, self.deps)
        await self.listener.start()
        self._running = True


async def start_live_hive(options: LiveHiveOptions | None = None) -> LiveHive:
    """Build a Hive Stand on real time, start its listener and return it.

    Args:
        options: How to build it; a plaintext loopback stand when omitted.

    Returns:
        The running stand; the caller awaits `stop()` (a fixture does).
    """
    chosen = options if options is not None else LiveHiveOptions()
    clock = SystemClock()
    signer = TamperableSigner(secrets.token_bytes(PRIVATE_KEY_BYTES))
    hive_id = chosen.hive_id if chosen.hive_id is not None else new_hive_id(clock)
    party = HiveParty(hive_id, new_node_id(clock), signer)
    trail = MemoryPheromoneTrail(clock)
    registry = chosen.registry if chosen.registry is not None else MemorySwarmRegistry(trail)
    recorder = SwarmRecorder(
        trail, clock, make_identity(clock, hive_id=party.hive_id, node_id=party.node_id)
    )
    config = _listener_config(chosen)
    rules = make_rules(
        make_endpoint(party, config.invite_url, None if chosen.tls is None else chosen.tls.sha256),
        offline_limit_s=chosen.offline_limit_s,
        heartbeat_interval_s=chosen.heartbeat_interval_s,
    )
    hub, log = GatewayHub(clock), HubLog()
    hub.subscribe(log)
    deps = ListenerDeps(
        registry,
        EnrolmentDoor(registry, recorder, rules),
        hub,
        recorder,
        HiveVoice(party.hive_id, party.node_id),
        signer,
    )
    listener = SwarmListener(config, deps)
    await listener.start()
    return LiveHive(
        clock, party, signer, registry, trail, recorder, deps.door, hub, log, config, deps, listener
    )


def _listener_config(chosen: LiveHiveOptions) -> ListenerConfig:
    """Fix the listener's address now: the invites it mints must name it before it starts."""
    port = free_port(chosen.host)
    scheme = "ws" if chosen.tls is None else "wss"
    return ListenerConfig(
        host=chosen.host,
        port=port,
        tls_cert=None if chosen.tls is None else chosen.tls.cert_path,
        tls_key=None if chosen.tls is None else chosen.tls.key_path,
        liveness=LivenessRules(MISSED_BEATS_ALLOWED),
        advertise_url=f"{scheme}://{chosen.advertise_host or chosen.host}:{port}",
    )
