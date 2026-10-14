"""Build the signing parties of a Swarm test: the Hive Stand's node and a device's node.

A Swarm test needs two sides with real Ed25519 keys: the Hive Stand, whose node signs every frame
the listener sends and whose key a device pins from its invite, and a device, whose node signs
what its Pollen Packet sends and whose public key the registry stores. `HiveParty` and
`DeviceParty` bundle each side's ids and signer with the codec that signs for it, so a test states
who is talking and never touches key bytes. `make_swarm_node` builds the registry row for a device
party (ONLINE on the GATEWAY rung by default, with the capability strings its access level grants),
so a test that does not exercise enrolment can start from an enrolled device.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every Swarm test that
    needs a signed frame or a registry node.

Key invariants:
    - Every id and timestamp comes from the injected clock, so a run is deterministic.
    - A node built from a party carries that party's real public key, so a frame it signs
      verifies against the node.
    - Every value built here passes the models' own validators.

See Also:
    - hivemind.swarm.models for SwarmNode.
    - hivemind.swarm.enrolment.capabilities for the strings a node is granted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from builders.cells import make_capabilities
from builders.forage import make_capacity, make_host_capacity

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm.enrolment.capabilities import granted_capabilities
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from waggle.clock import Clock, FakeClock
from waggle.codec import Codec, Verifier
from waggle.ids import (
    CellId,
    DeviceId,
    HiveId,
    NodeId,
    WardenId,
    new_cell_id,
    new_device_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.cell.status import AttestationCheck, CellReady
from waggle.messages.forage.capacity import CapacityTrigger, LocalPoolUsage
from waggle.messages.forage.hosting import CapacityReport
from waggle.messages.labels import AccessLevel as WireAccessLevel
from waggle.messages.labels import CombShieldLevel as WireCombShieldLevel
from waggle.messages.swarm import DeviceHeartbeat, RuntimeLevel
from waggle.signing import Ed25519Signer, public_key_hex

__all__ = [
    "DeviceParty",
    "HiveParty",
    "make_capacity_report",
    "make_cell_ready",
    "make_device_party",
    "make_heartbeat",
    "make_hive_party",
    "make_swarm_node",
]


@dataclass(frozen=True, slots=True)
class HiveParty:
    """The Hive Stand as a signer: its Queen address, its node and its key."""

    hive_id: HiveId
    node_id: NodeId
    signer: Ed25519Signer

    @property
    def public_hex(self) -> str:
        """The Hive Stand's public key as the invite and the accept carry it."""
        return public_key_hex(self.signer.public_key_bytes)

    def codec(self, verifier: Verifier | None) -> Codec:
        """A codec that signs as the Hive Stand and verifies with `verifier`."""
        return Codec(signer=self.signer, verifier=verifier)


@dataclass(frozen=True, slots=True)
class DeviceParty:
    """A device as a signer: its bee address, its node, its Cell and Warden, and its key."""

    device_id: DeviceId
    node_id: NodeId
    cell_id: CellId
    warden_id: WardenId
    signer: Ed25519Signer = field(repr=False)

    @property
    def public_hex(self) -> str:
        """The device's public key as the registry stores it."""
        return public_key_hex(self.signer.public_key_bytes)

    def codec(self, verifier: Verifier | None) -> Codec:
        """A codec that signs as the device and verifies with `verifier`."""
        return Codec(signer=self.signer, verifier=verifier)


def make_hive_party(clock: Clock | None = None) -> HiveParty:
    """Build a Hive Stand party with a fresh key.

    Args:
        clock: Source of the minted ids; a fresh FakeClock when omitted.

    Returns:
        The party.
    """
    active = clock if clock is not None else FakeClock()
    return HiveParty(new_hive_id(active), new_node_id(active), Ed25519Signer.generate())


def make_device_party(clock: Clock | None = None) -> DeviceParty:
    """Build a device party with a fresh key and a fresh Cell and Warden.

    Args:
        clock: Source of the minted ids; a fresh FakeClock when omitted.

    Returns:
        The party.
    """
    active = clock if clock is not None else FakeClock()
    return DeviceParty(
        device_id=new_device_id(active),
        node_id=new_node_id(active),
        cell_id=new_cell_id(active),
        warden_id=new_warden_id(active),
        signer=Ed25519Signer.generate(),
    )


def make_swarm_node(
    party: DeviceParty | None = None, clock: Clock | None = None, **overrides: object
) -> SwarmNode:
    """Build a valid enrolled node: ONLINE on the GATEWAY rung, SCRATCH access, Meadow tier.

    Args:
        party: The device it is the row of; a fresh one when omitted.
        clock: Source of ids and the enrolment time; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated SwarmNode whose public key is the party's real one.
    """
    active = clock if clock is not None else FakeClock()
    owner = party if party is not None else make_device_party(active)
    access = overrides.get("access_level", AccessLevel.SCRATCH)
    assert isinstance(access, AccessLevel)
    fields: dict[str, object] = {
        "device_id": owner.device_id,
        "node_id": owner.node_id,
        "cell_id": owner.cell_id,
        "warden_id": owner.warden_id,
        "public_key_hex": owner.public_hex,
        "hostname": "test-device",
        "label": None,
        "runtime_version": "1.0.0",
        "capabilities": make_capabilities(),
        "capacity": make_capacity(),
        "requested_access": AccessLevel.FULL,
        "access_level": access,
        "comb_shield": CombShieldLevel.MEADOW,
        "status": NodeStatus.ONLINE,
        "granted_capabilities": granted_capabilities(access, owner.cell_id),
        "offline_limit_s": 120.0,
        "heartbeat_interval_s": 15.0,
        "max_sub_bees": None,
        "enrolled_at": active.now(),
        "last_seen_at": active.now(),
        "reason": "Admitted at SCRATCH on the Meadow tier.",
    }
    fields.update(overrides)
    return SwarmNode(**fields)


def make_heartbeat(party: DeviceParty, **overrides: object) -> DeviceHeartbeat:
    """Build a `swarm.device_heartbeat` from `party`: GATEWAY rung, no leases, a second up.

    Args:
        party: The reporting device.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated DeviceHeartbeat.
    """
    fields: dict[str, object] = {
        "device_id": party.device_id,
        "level": RuntimeLevel.GATEWAY,
        "lease_ids": (),
        "hive_started_processes": 0,
        "outbox_pending": 0,
        "uptime_s": 1.0,
    }
    fields.update(overrides)
    return DeviceHeartbeat(**fields)


def make_cell_ready(party: DeviceParty, **overrides: object) -> CellReady:
    """Build the `cell.ready` a colonized device's Warden sends first, at SCRATCH on the Meadow.

    Args:
        party: The device whose Cell and Warden announce themselves.
        **overrides: Field values that replace the defaults below. A tier above the Meadow that
            brings no `attestation` gets one passing check, since the frame is invalid without.

    Returns:
        A validated CellReady.
    """
    platform, capabilities = make_capabilities().to_wire()
    fields: dict[str, object] = {
        "cell_id": party.cell_id,
        "warden_id": party.warden_id,
        "platform": platform,
        "capabilities": capabilities,
        "access_level": WireAccessLevel.SCRATCH,
        "comb_shield": WireCombShieldLevel.MEADOW,
        "attestation": (),
        "runtime_version": "1.0.0",
    }
    fields.update(overrides)
    if fields["comb_shield"] is not WireCombShieldLevel.MEADOW and not fields["attestation"]:
        # A tier above the Meadow is attested, and the frame is invalid without its checks.
        fields["attestation"] = (AttestationCheck(name="tunnel", has_passed=True, detail="Held."),)
    return CellReady(**fields)


def make_capacity_report(
    party: DeviceParty, cores: int = 16, **overrides: object
) -> CapacityReport:
    """Build the `forage.capacity_report` a device sends when its capacity changes.

    Args:
        party: The reporting device; its Cell is the report's subject.
        cores: How many cores the host reports, so a test can tell one report from another.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated CapacityReport with two sub-bees allowed and nothing in use.
    """
    host = make_host_capacity().model_copy(update={"cores": cores})
    fields: dict[str, object] = {
        "cell_id": party.cell_id,
        "trigger": CapacityTrigger.CHANGED,
        "host": host.to_wire(),
        "model_servers": (),
        "max_sub_bees": 2,
        "usage": LocalPoolUsage(
            sub_bees_active=0, model_vram_bytes=0, model_disk_bytes=0, seats_exported=0
        ),
    }
    fields.update(overrides)
    return CapacityReport(**fields)
