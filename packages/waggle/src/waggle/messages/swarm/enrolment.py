"""Define the swarm family's enrolment messages: the invite, the admission, the heartbeat.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the
swarm family is about devices joining and living in the Swarm (the mesh of enrolled Real Cells,
existing devices borrowed for tasks and left exactly as found). A device's Pollen Packet (the
thin gateway installed on it) presents a one-time invite token and its signing key to the Queen
(the central orchestrator) in an ``EnrolRequest``; the Queen admits it with an ``EnrolAccept``
naming its Cell (its identity as a unit of compute), its Warden (the always-on supervisor of that
Cell), the Hive key that signs relocation notices, the trusted node keys, the granted access
level and Comb Shield tier (the Cell's security tier) and the limits it must persist; from then
on the packet reports liveness with a ``DeviceHeartbeat``. The family's other half (promotion of
a colonized device, one whose Warden lives on it, to a Nuc, one with its own model server, and
the merge of an offline Warden's Pheromone Trail segment) lives in
``waggle.messages.swarm.colonized``, split out by responsibility so each file stays under the
codingrules 5.1 size limit. The invite token is a secret: redacted from logs and the Pheromone
Trail (the append-only audit log), never echoed and never in a repr. Every bound is a named
constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by a Pollen Packet (request, heartbeat) or the Queen (accept)
    and read by the other; calls into waggle.messages.base, waggle.messages.labels and
    waggle.messages.reports only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - EnrolRequest.invite_token never appears in a repr (Field(repr=False)), and an EnrolAccept
      never grants NIGHT_VEIL, the virtual-only tier.

See Also:
    - docs/waggle/spec.md section 8.10 for the normative fields, bounds and validators.
    - waggle.messages.swarm.colonized for NucPromote, NucPromoted and TrailSegmentSync.
    - waggle.messages.labels for AccessLevel and CombShieldLevel, which EnrolAccept carries.
    - waggle.messages.reports for the platform, capability and capacity reports.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    MAX_SUB_BEES_ON_WIRE,
    VALUE_MODEL_CONFIG,
    CellIdField,
    DeviceIdField,
    HiveIdField,
    LeaseIdField,
    NodeIdField,
    WaggleMessage,
    WardenIdField,
)
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.reports import CellCapabilitiesReport, HostCapacityReport, PlatformReport

MIN_INVITE_TOKEN_CHARS = 16  # Shorter is guessable; hive swarm invite mints 32 or more.
MAX_INVITE_TOKEN_CHARS = 128  # Room for a long random token with a prefix, never a document.
PUBLIC_KEY_HEX_PATTERN = r"^[0-9a-f]{64}$"  # 32 raw Ed25519 public-key bytes as lowercase hex.
MAX_HOSTNAME_CHARS = 253  # The longest fully qualified domain name the DNS wire format allows.
MAX_RUNTIME_VERSION_CHARS = 32  # "1.2.3" plus a pre-release or local tag at most.
MIN_TRUSTED_NODES = 1  # Without a trusted key the packet could verify nothing the Hive sends.
MAX_TRUSTED_NODES = 16  # The Hive Stand plus a few Wardens on devices; bounds the verifier seed.
MAX_GRANTED_CAPABILITIES = 64  # A device's capability set is a page of strings, not a policy.
MAX_CAPABILITY_CHARS = 128  # One capability string like session:exec or net:host:example.org.
MAX_HEARTBEAT_LEASES = 64  # More leases than a device would ever hold open; bounds the report.

__all__ = [
    "MAX_CAPABILITY_CHARS",
    "MAX_GRANTED_CAPABILITIES",
    "MAX_HEARTBEAT_LEASES",
    "MAX_HOSTNAME_CHARS",
    "MAX_INVITE_TOKEN_CHARS",
    "MAX_RUNTIME_VERSION_CHARS",
    "MAX_TRUSTED_NODES",
    "MIN_INVITE_TOKEN_CHARS",
    "MIN_TRUSTED_NODES",
    "PUBLIC_KEY_HEX_PATTERN",
    "DeviceHeartbeat",
    "EnrolAccept",
    "EnrolRequest",
    "NodeKey",
    "NucAction",
    "NucOutcome",
    "RuntimeLevel",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums and value models
# ──────────────────────────────────────────────────────────────────────────────


class RuntimeLevel(Enum):
    """Which rung of the Swarm ladder a device is on: how much of the Hive runs on it."""

    GATEWAY = "GATEWAY"  # Level 0: packet only; the Warden lives on the Hive Stand.
    COLONIZED = "COLONIZED"  # Level 1: the Warden and its sub-bees live on the device.
    NUC = "NUC"  # Level 2: colonized plus a model server of its own.


class NucAction(Enum):
    """Whether a NucPromote starts or stops the device's model server; no separate demote kind."""

    PROMOTE = "PROMOTE"
    DEMOTE = "DEMOTE"


class NucOutcome(Enum):
    """How a promotion or demotion ended, as its NucPromoted reports it."""

    PROMOTED = "PROMOTED"
    DEMOTED = "DEMOTED"
    FAILED = "FAILED"


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A raw 32-byte Ed25519 public key as lowercase hex, the one form a key takes on the wire.
_PublicKeyHex = Annotated[str, Field(pattern=PUBLIC_KEY_HEX_PATTERN)]


class NodeKey(BaseModel):
    """One trusted signer: a node id and the public key that verifies its envelopes."""

    model_config = VALUE_MODEL_CONFIG

    node_id: NodeIdField = Field(description="The node whose signatures the key verifies.")
    public_key_hex: _PublicKeyHex = Field(
        description="The node's raw 32-byte Ed25519 public key, hex."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────────


class EnrolRequest(WaggleMessage):
    """Present a token, a key and a self-description to join the Swarm (swarm.enrol_request).

    A request: the token is sent exactly once, here, and never echoed; a denial is a
    control.error with code hive.swarm.enrol_denied. Never appended to an outbox (it is sent
    only on a live connection; a failed enrolment is retried by minting a new invite).
    """

    invite_token: str = Field(
        min_length=MIN_INVITE_TOKEN_CHARS,
        max_length=MAX_INVITE_TOKEN_CHARS,
        repr=False,
        description="The single-use token from hive swarm invite. A secret: redacted from logs "
        "and the trail, and this message is never appended to an outbox.",
    )
    device_id: DeviceIdField = Field(
        description="Minted by the packet at install; its bee address for life. The receiver "
        "rejects a mismatch with the envelope's sender (receiver rule)."
    )
    node_id: NodeIdField = Field(
        description="The packet's node, whose key follows. The receiver rejects a mismatch with "
        "the envelope's node_id (receiver rule)."
    )
    public_key_hex: _PublicKeyHex = Field(
        description="The packet's raw 32-byte Ed25519 public key, hex."
    )
    hostname: str = Field(max_length=MAX_HOSTNAME_CHARS, description="The device's hostname.")
    runtime_version: str = Field(
        max_length=MAX_RUNTIME_VERSION_CHARS, description="The pollen package version."
    )
    platform: PlatformReport = Field(
        description="The report every later decision about the device reads."
    )
    capabilities: CellCapabilitiesReport = Field(description="What the device can do.")
    capacity: HostCapacityReport = Field(description="The device's host capacity.")
    max_sub_bees: Annotated[int, Field(ge=0, le=MAX_SUB_BEES_ON_WIRE)] | None = Field(
        description="An operator-configured cap on concurrent sub-bees; the Queen's ceilings "
        "may only lower it. At least 0 when set, and never above the shared wire cap."
    )
    requested_access: AccessLevel = Field(
        default=AccessLevel.FULL,
        description="The level the packet asks for; the operator may grant less.",
    )


class EnrolAccept(WaggleMessage):
    """Admit the device with its ids, keys, levels and limits (swarm.enrol_accept, a reply).

    The packet keeps dialling the address it enrolled through; only a QueenMoved changes it.
    """

    device_id: DeviceIdField = Field(description="The device admitted.")
    cell_id: CellIdField = Field(description="The Real Cell this device now is.")
    hive_id: HiveIdField = Field(
        description="The Queen's bee address, the recipient of everything the packet sends up."
    )
    hive_key_hex: _PublicKeyHex = Field(
        description="The Hive key (raw 32-byte Ed25519 public key, hex) that signs QueenMoved."
    )
    trusted_nodes: tuple[NodeKey, ...] = Field(
        min_length=MIN_TRUSTED_NODES,
        max_length=MAX_TRUSTED_NODES,
        description="Node ids and keys whose signatures the packet accepts; seeds its verifier. "
        "Node ids unique.",
    )
    warden_id: WardenIdField = Field(
        description="The Cell's Warden; the packet accepts session and lease traffic only from "
        "it or the Queen."
    )
    access_level: AccessLevel = Field(
        description="The level granted; at most the request's requested_access (receiver rule)."
    )
    comb_shield: CombShieldLevel = Field(
        description="The operator-set tier; never NIGHT_VEIL, the virtual-only tier."
    )
    granted_capabilities: tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...] = (
        Field(
            max_length=MAX_GRANTED_CAPABILITIES,
            description="The node's capability strings, checked locally on every request.",
        )
    )
    offline_limit_s: float = Field(
        gt=0, description="The dead-man limit for a device with no Warden on it."
    )
    heartbeat_interval_s: float = Field(
        gt=0, description="How often the packet sends DeviceHeartbeat."
    )
    reason: _Reason = Field(description="Why admitted at this level and tier.")

    @field_validator("trusted_nodes")
    @classmethod
    def _node_ids_unique(cls, value: tuple[NodeKey, ...]) -> tuple[NodeKey, ...]:
        """Reject a node listed twice."""
        # Two keys for one node would leave the verifier to pick one; a node has exactly one
        # key at a time, and a rotation is a fresh accept, not a second entry.
        node_ids = [node.node_id for node in value]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("EnrolAccept trusted_nodes node ids must be unique.")
        return value

    @field_validator("comb_shield")
    @classmethod
    def _never_night_veil(cls, value: CombShieldLevel) -> CombShieldLevel:
        """Reject the virtual-only tier on a Real Cell."""
        # NIGHT_VEIL is teardown-only (VPN plus Tor on a Cell that is destroyed afterwards); a
        # borrowed device can never be torn down, so the tier cannot mean anything on it.
        if value is CombShieldLevel.NIGHT_VEIL:
            raise ValueError(
                "EnrolAccept comb_shield is never NIGHT_VEIL: a Real Cell cannot hold the "
                "virtual-only tier."
            )
        return value


class DeviceHeartbeat(WaggleMessage):
    """The gateway's liveness report (swarm.device_heartbeat, an event).

    Its runtime level, open leases, Hive-started process count and outbox depth. Live capacity
    never rides here; a changed figure is a forage.capacity_report with trigger CHANGED.
    """

    device_id: DeviceIdField = Field(description="The reporting device.")
    level: RuntimeLevel = Field(
        description="Which rung the device is on, so the Queen knows whether the dead-man "
        "switch or an on-device Warden handles link loss."
    )
    lease_ids: tuple[LeaseIdField, ...] = Field(
        max_length=MAX_HEARTBEAT_LEASES, description="Leases the packet holds open."
    )
    hive_started_processes: int = Field(
        ge=0, description="Processes its leases started and still track."
    )
    outbox_pending: int = Field(ge=0, description="Frames waiting in the packet's outbox.")
    uptime_s: float = Field(
        ge=0, description="Seconds since the packet started, so a restart is visible."
    )
