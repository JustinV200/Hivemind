"""Build the Hive Stand's side of the swarm for tests: registry, trail, door, hub and recorder.

The router and listener tests need the same stand every time: a Hive Stand with real signing keys,
an in-memory registry that writes to an in-memory trail, an enrolment door over them and the hub
the Queen's swarm gate would subscribe to. `HiveRig` bundles those with the helpers every test
reaches for (mint an invite and read its token, list what is on the trail), so a test names only
the fact under test. The pieces are the real classes; only the registry and trail are the in-memory
fakes the contract suites hold to the SQLite originals.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the router and listener
    tests under tests/unit/swarm/gateway.

Key invariants:
    - Everything is driven by one FakeClock, so a replay grace or a lapsed invite is a call to
      `clock.advance`, never a sleep.
    - `mint` returns the invite's token as a plain string for a test to present; no code outside
      tests does that, because the Hive never keeps it.

See Also:
    - builders.swarm.dial for a router served over in-memory connections.
    - hivemind.swarm.gateway.listener for the listener this stand's pieces are handed to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from builders.cells import make_identity
from builders.swarm.enrolment import make_endpoint, make_rules
from builders.swarm.parties import HiveParty, make_hive_party

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.swarm.enrolment import (
    EnrolmentDoor,
    EnrolmentRules,
    MintedInvite,
    parse_invite_string,
)
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway import (
    DeviceLink,
    GatewayHub,
    HiveVoice,
    ListenerConfig,
    ListenerDeps,
    LivenessRules,
    SwarmListener,
    WardenConnection,
)
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.registry import MemorySwarmRegistry
from waggle.clock import FakeClock
from waggle.envelope import Envelope

__all__ = ["HiveRig", "HubLog", "WardenHandlerLog", "make_hive_rig"]

DEFAULT_URL = "ws://127.0.0.1:8720"
WAIT_S = 5.0  # A test that waits longer than this has a bug, not a slow machine.


class HubLog:
    """A hub listener that keeps what it heard: devices online, devices offline and frames."""

    def __init__(self) -> None:
        """Start with nothing heard."""
        self.online: list[SwarmNode] = []
        self.offline: list[tuple[SwarmNode, str]] = []
        self.frames: list[Envelope] = []
        self._changed = asyncio.Event()

    async def on_online(self, node: SwarmNode, link: DeviceLink) -> None:
        """Keep the node that came online."""
        self.online.append(node)
        self._changed.set()

    async def on_offline(self, node: SwarmNode, reason: str) -> None:
        """Keep the node that went offline, with the reason it was told."""
        self.offline.append((node, reason))
        self._changed.set()

    async def on_frame(self, node: SwarmNode, envelope: Envelope) -> None:
        """Keep a frame nobody was waiting for."""
        self.frames.append(envelope)
        self._changed.set()

    async def wait_until(self, done: Callable[[], bool]) -> None:
        """Wait until `done()` holds, waking on each thing the hub announces.

        Args:
            done: A condition over this log's lists.

        Raises:
            TimeoutError: The condition did not hold within `WAIT_S`.
        """
        async with asyncio.timeout(WAIT_S):
            while not done():
                # Cleared and awaited with no suspension between the check and the clear, so an
                # announcement made after `done()` returned False is never lost.
                self._changed.clear()
                await self._changed.wait()


class WardenHandlerLog:
    """A Warden connection handler that keeps what it was handed and the one frame after it."""

    def __init__(self) -> None:
        """Start with nothing handed over."""
        self.seen: list[WardenConnection] = []
        self.frames: list[Envelope] = []
        self.handed = asyncio.Event()  # Set when a connection arrives.

    async def handle(self, connection: WardenConnection) -> None:
        """Keep the connection, read one more frame from it and return."""
        self.seen.append(connection)
        self.handed.set()
        async for envelope in connection.frames:
            self.frames.append(envelope)
            return


@dataclass(frozen=True)
class HiveRig:
    """A Hive Stand's swarm pieces over in-memory storage, driven by one fake clock."""

    clock: FakeClock
    hive: HiveParty
    voice: HiveVoice
    trail: MemoryPheromoneTrail
    registry: MemorySwarmRegistry
    recorder: SwarmRecorder
    rules: EnrolmentRules
    door: EnrolmentDoor
    hub: GatewayHub

    async def mint(
        self,
        access: AccessLevel = AccessLevel.FULL,
        shield: CombShieldLevel = CombShieldLevel.MEADOW,
        label: str | None = None,
    ) -> tuple[MintedInvite, str]:
        """Mint an invite at the door; return it with the token a device would present.

        Args:
            access: The cap on what the device may be granted.
            shield: The tier the operator vouches for.
            label: The operator's name for the device.

        Returns:
            The minted invite and the token inside its string.
        """
        minted = await self.door.mint_invite(access, shield, label)
        return minted, parse_invite_string(minted.invite_string).token

    async def events(self, kind: str | None = None) -> list[PheromoneEvent]:
        """Every event on the trail (of `kind`, when given), oldest first."""
        everything = list(await self.trail.query(TrailQuery()))
        return [event for event in everything if kind is None or event.kind == kind]

    async def kinds(self) -> list[str]:
        """The kind of every event on the trail, oldest first."""
        return [event.kind for event in await self.events()]

    def listener(self, config: ListenerConfig | None = None) -> SwarmListener:
        """Build, without starting, a swarm listener over this stand's pieces.

        Args:
            config: Where it binds and how it serves; plaintext on an OS-chosen loopback port
                with four missed beats allowed when omitted.

        Returns:
            The listener; the caller starts it and awaits `stop()`.
        """
        used = (
            config
            if config is not None
            else ListenerConfig(
                host="127.0.0.1", port=0, tls_cert=None, tls_key=None, liveness=LivenessRules(4)
            )
        )
        deps = ListenerDeps(
            self.registry, self.door, self.hub, self.recorder, self.voice, self.hive.signer
        )
        return SwarmListener(used, deps)


def make_hive_rig(
    clock: FakeClock | None = None, *, url: str = DEFAULT_URL, tls_sha256: str | None = None
) -> HiveRig:
    """Build a Hive Stand: fresh keys, empty storage, a door minting invites for `url`.

    Args:
        clock: The clock everything runs on; a fresh FakeClock when omitted.
        url: The listener URL invites carry.
        tls_sha256: The certificate pin invites carry, required exactly for a `wss://` URL.

    Returns:
        The rig.
    """
    active = clock if clock is not None else FakeClock()
    hive = make_hive_party(active)
    trail = MemoryPheromoneTrail(active)
    registry = MemorySwarmRegistry(trail)
    recorder = SwarmRecorder(
        trail, active, make_identity(active, hive_id=hive.hive_id, node_id=hive.node_id)
    )
    rules = make_rules(make_endpoint(hive, url, tls_sha256))
    return HiveRig(
        clock=active,
        hive=hive,
        voice=HiveVoice(hive.hive_id, hive.node_id),
        trail=trail,
        registry=registry,
        recorder=recorder,
        rules=rules,
        door=EnrolmentDoor(registry, recorder, rules),
        hub=GatewayHub(active),
    )
