"""Turn enrolled devices into Real Cells: the swarm package.

The Swarm is the mesh of devices (existing machines the Hive borrows as Real Cells and leaves
exactly as found) that have enrolled with an invite. Each carries a Pollen Packet (the thin
gateway installed on it) that dials out to the Hive Stand's swarm listener. This package is the
Hive's side of that: the registry that remembers nodes and invites, the enrolment door that admits
a device and the pure decision behind it, the gateway that listens, verifies, routes and judges
liveness, `SwarmSource` that leases a device as a Real Cell, `PollenSession` that is the Cell's
terminal carried over Waggle, the Propolis shield that verifies a device's VPN before work is
placed on it, and the watch probe that observes an idle device read-only. The two pure decisions
of the runtime ladder live here too: whether a device's Warden should move onto it
(`decide_colonize`) and whether a colonized device should start a model server of its own and
become a Nuc (`decide_nuc`).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by the Queen's swarm gate
    (Layer 6) for the listener, the hub and the sources, by a device's Warden (Layer 5) for its
    source, session and watch probe, and by `hive swarm` (Layer 7) for the registry and the
    door. Calls into hivemind.guard, hivemind.cell, hivemind.memory, hivemind.supervision,
    hivemind.brood_chamber, hivemind.basket, hivemind.pheromone, hivemind.forage, hivemind.llm,
    hivemind.manifest and hivemind.common, and waggle.

Key invariants:
    - A device is a gateway: nothing in this package holds a model client or supervises; it
      reports, and the Hive decides.
    - The invite token is held only by the string shown once at the Hive Stand; the registry
      stores its SHA-256, and no event, log line or error carries a token, key, argument, path or
      file content.
    - Every frame on the swarm listener is signed, and a connection is pinned to the node of its
      first verified frame; approval and enrolment are never a remote surface of the Hive.
    - A lease on a device is released only on the device's confirmation, so a Real Cell is left
      exactly as found or the lease stays orphaned for a retry.

See Also:
    - .claude/codingrules.md section 4 for the layer 3 row this package occupies.
    - docs/waggle/enrolment.md and docs/waggle/gateway-contract.md for the protocol served.
    - docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md for the trust model.
    - hivemind/swarm/README.md for the flow from enrolment to a leased session.

Public API:
    - SwarmError and its subclasses, EnrolRefusal: the package's errors (errors).
    - SwarmNode, SwarmInvite, InviteState, SWARM_SOURCE: the registry's rows (models).
    - NodeStatus, can_transition, assert_transition: the node status machine (node_state).
    - RuntimeLevel, can_move, is_promotion, assert_move: the runtime ladder (level).
    - decide_colonize, ColonizeFacts, ColonizeDecision, ColonizeReason: whether a device's Warden
      should move onto it (level).
    - decide_nuc, NucFacts, NucDecision, NucReason, OfferedServer: whether a colonized device
      should start a model server of its own, and which (nuc).
    - SwarmRecorder: the trail, clock and identity the Swarm records with (events).
    - SwarmSource, SwarmLeaseReleaser: an enrolled device as a Real Cell source (source).
    - PollenWatchProbe: read-only observation of an idle device (watch).
    - SwarmRegistry, SqliteSwarmRegistry, MemorySwarmRegistry: the node and invite store
      (registry).
    - EnrolmentDoor, EnrolmentRules, MintedInvite, InviteEndpoint, parse_invite_string: enrolment
      (enrolment).
    - SwarmListener, ListenerConfig, ListenerDeps, GatewayHub, HubListener, DeviceLink,
      WardenConnection, WardenConnectionHandler, HeartbeatMonitor: the gateway (gateway).
    - PollenSession, CommandGuard, EnforcedCommandGuard, DeviceCommand: a device's CellSession
      (session).
    - ShieldedSource, PropolisChecks, ShieldReport: the Propolis tunnel verification (shield).
"""

from hivemind.swarm.enrolment import (
    EnrolmentDoor,
    EnrolmentRules,
    InviteEndpoint,
    MintedInvite,
    parse_invite_string,
)
from hivemind.swarm.errors import (
    CommandDeniedError,
    DeviceRequestError,
    DeviceTimeoutError,
    EnrolmentDeniedError,
    EnrolRefusal,
    GatewayUnavailableError,
    InvalidNodeTransitionError,
    InviteConflictError,
    InviteFormatError,
    InviteNotFoundError,
    InviteRefusedError,
    LadderError,
    NodeNotFoundError,
    NodeRevokedError,
    NodeStatusConflictError,
    ShieldUnverifiedError,
    SwarmError,
    TransferIntegrityError,
)
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway import (
    DeviceLink,
    GatewayHub,
    HeartbeatMonitor,
    HubListener,
    ListenerConfig,
    ListenerDeps,
    SwarmListener,
    WardenConnection,
    WardenConnectionHandler,
)
from hivemind.swarm.level import (
    ColonizeDecision,
    ColonizeFacts,
    ColonizeReason,
    RuntimeLevel,
    assert_move,
    can_move,
    decide_colonize,
    is_promotion,
)
from hivemind.swarm.models import SWARM_SOURCE, InviteState, SwarmInvite, SwarmNode
from hivemind.swarm.node_state import NodeStatus, assert_transition, can_transition
from hivemind.swarm.nuc import NucDecision, NucFacts, NucReason, OfferedServer, decide_nuc
from hivemind.swarm.registry import MemorySwarmRegistry, SqliteSwarmRegistry, SwarmRegistry
from hivemind.swarm.session import (
    CommandGuard,
    DeviceCommand,
    EnforcedCommandGuard,
    PollenSession,
)
from hivemind.swarm.shield import PropolisChecks, ShieldedSource, ShieldReport
from hivemind.swarm.source import SwarmLeaseReleaser, SwarmSource
from hivemind.swarm.watch import PollenWatchProbe

__all__ = [
    "SWARM_SOURCE",
    "ColonizeDecision",
    "ColonizeFacts",
    "ColonizeReason",
    "CommandDeniedError",
    "CommandGuard",
    "DeviceCommand",
    "DeviceLink",
    "DeviceRequestError",
    "DeviceTimeoutError",
    "EnforcedCommandGuard",
    "EnrolRefusal",
    "EnrolmentDeniedError",
    "EnrolmentDoor",
    "EnrolmentRules",
    "GatewayHub",
    "GatewayUnavailableError",
    "HeartbeatMonitor",
    "HubListener",
    "InvalidNodeTransitionError",
    "InviteConflictError",
    "InviteEndpoint",
    "InviteFormatError",
    "InviteNotFoundError",
    "InviteRefusedError",
    "InviteState",
    "LadderError",
    "ListenerConfig",
    "ListenerDeps",
    "MemorySwarmRegistry",
    "MintedInvite",
    "NodeNotFoundError",
    "NodeRevokedError",
    "NodeStatus",
    "NodeStatusConflictError",
    "NucDecision",
    "NucFacts",
    "NucReason",
    "OfferedServer",
    "PollenSession",
    "PollenWatchProbe",
    "PropolisChecks",
    "RuntimeLevel",
    "ShieldReport",
    "ShieldUnverifiedError",
    "ShieldedSource",
    "SqliteSwarmRegistry",
    "SwarmError",
    "SwarmInvite",
    "SwarmLeaseReleaser",
    "SwarmListener",
    "SwarmNode",
    "SwarmRecorder",
    "SwarmRegistry",
    "SwarmSource",
    "TransferIntegrityError",
    "WardenConnection",
    "WardenConnectionHandler",
    "assert_move",
    "assert_transition",
    "can_move",
    "can_transition",
    "decide_colonize",
    "decide_nuc",
    "is_promotion",
    "parse_invite_string",
]
