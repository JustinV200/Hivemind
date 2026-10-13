"""Decide an enrolment: turn a request and its invite into a node and a signed-ready accept.

The enrolment door (`door.py`) is the effectful edge; this module is the pure decision behind it
(codingrules 8.3). Given the device's `swarm.enrol_request`, the invite its token matched and the
Hive's rules, it says either "refuse, and here is the reason code" or "admit": the `SwarmNode` to
create and the `swarm.enrol_accept` to send. The decision is where the operator's authority
meets the device's ask (docs/waggle/enrolment.md sections 6 and 7): the device is granted the
lower of the access level it asked for and the invite's cap, the Comb Shield tier is the
invite's and never the device's (and never Night Veil), and the Queen mints the device's Real
Cell and Warden ids. The accept carries the Hive key, the one trusted node (the Hive Stand's) and
the capability strings the packet will enforce locally. `node_update_for` is the same terms again
as a `swarm.node_update`, which the Queen sends when a node comes online or the operator changes
its level or tier.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.enrolment`.
    Called by the door and by the gateway listener. Calls into hivemind.cell, hivemind.forage,
    hivemind.manifest (the `[swarm]` section), hivemind.swarm and waggle only.

Key invariants:
    - Pure: no clock, no randomness, no I/O. Ids are minted by the caller and passed in, and the
      time is a parameter.
    - A device's granted access is never above the invite's cap, and its tier is the invite's.
    - The accept names the Hive Stand's key as the one trusted node, and repeats `hive_id` so the
      packet can refuse an accept from another Hive.
    - A device is granted exactly `granted_capabilities(access, cell_id)`; nothing is added.

See Also:
    - docs/waggle/enrolment.md sections 6 and 7 for the decision and the accept.
    - hivemind.swarm.enrolment.capabilities for the capability strings.
    - hivemind.swarm.registry.rules for the redemption rules this applies to the invite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from hivemind.cell import AccessLevel, CellCapabilities, CombShieldLevel
from hivemind.cell.local import DEFAULT_MAX_SUB_BEES
from hivemind.forage import ForageCapacity, HostCapacity
from hivemind.manifest import SwarmSection
from hivemind.swarm.enrolment.capabilities import granted_capabilities
from hivemind.swarm.enrolment.invite import InviteEndpoint
from hivemind.swarm.errors import EnrolRefusal
from hivemind.swarm.models import SwarmInvite, SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry.rules import judge_redemption
from waggle.envelope import Envelope
from waggle.ids import CellId, WardenId
from waggle.messages.swarm import (
    EnrolAccept,
    EnrolRequest,
    NodeKey,
    NodeUpdate,
    RuntimeLevel,
)

__all__ = [
    "Admission",
    "EnrolmentRules",
    "MintedIds",
    "check_request",
    "decide_enrolment",
    "node_update_for",
]


@dataclass(frozen=True, slots=True)
class EnrolmentRules:
    """The Hive's standing terms for enrolling devices: who it is and how long things last.

    Attributes:
        endpoint: The Hive's address half: the URL devices dial, the Queen's bee address, the
            Hive Stand's node id and key, and the certificate fingerprint.
        offline_limit_s: The dead-man limit a device with no Warden on it enforces on itself.
        heartbeat_interval_s: How often a device reports.
        invite_ttl: How long an invite lives.
    """

    endpoint: InviteEndpoint
    offline_limit_s: float
    heartbeat_interval_s: float
    invite_ttl: timedelta

    @classmethod
    def from_section(cls, section: SwarmSection, endpoint: InviteEndpoint) -> EnrolmentRules:
        """Build the rules from the manifest's `[swarm]` section and the Hive's address half.

        Args:
            section: The loaded `[swarm]` section.
            endpoint: The address half, built by the composition root from the listener's
                advertised URL and certificate and the Hive Stand's identity.

        Returns:
            The rules, with the invite lifetime and the two device timings from `section`.
        """
        return cls(
            endpoint=endpoint,
            offline_limit_s=section.dead_man_s,
            heartbeat_interval_s=section.heartbeat_interval_s,
            invite_ttl=timedelta(minutes=section.invite_ttl_minutes),
        )


@dataclass(frozen=True, slots=True)
class MintedIds:
    """The two ids the Queen mints for a device at enrolment, and keeps for the node's life.

    Attributes:
        cell_id: The Real Cell the device becomes.
        warden_id: The device's Warden, at every rung of the runtime ladder.
    """

    cell_id: CellId
    warden_id: WardenId


@dataclass(frozen=True, slots=True)
class Admission:
    """What an admitting decision produced: the node to create and the accept to send.

    Attributes:
        node: The new `SwarmNode`, ONLINE on the GATEWAY rung.
        accept: The `swarm.enrol_accept` payload for the device.
    """

    node: SwarmNode
    accept: EnrolAccept


def check_request(
    envelope: Envelope, request: EnrolRequest, rules: EnrolmentRules
) -> EnrolRefusal | None:
    """Check a request's addressing and identity against its envelope, before its token is read.

    Args:
        envelope: The envelope the request arrived in, already verified against its own key.
        request: The `swarm.enrol_request` payload.
        rules: The Hive's rules, for its own address.

    Returns:
        None when the request is addressed to this Hive's Queen and names the ids its envelope
        proves; otherwise the reason code.
    """
    # A frame whose recipient is not the receiver's own address is dropped (spec section 3); for
    # the door that means the invite came from another Hive, whose token this one cannot know.
    if envelope.recipient != rules.endpoint.hive_id:
        return EnrolRefusal.WRONG_HIVE
    # The signature proves the node key, so the payload's ids must be the ones it signs for.
    if request.device_id != envelope.sender or request.node_id != envelope.node_id:
        return EnrolRefusal.IDENTITY_MISMATCH
    return None


def decide_enrolment(
    request: EnrolRequest,
    invite: SwarmInvite,
    rules: EnrolmentRules,
    minted: MintedIds,
    at: datetime,
) -> Admission | EnrolRefusal:
    """Decide whether `invite` admits the device that sent `request`, and on what terms.

    Args:
        request: The device's `swarm.enrol_request`, whose identities `check_request` passed.
        invite: The invite its token matched, in whatever state it is in.
        rules: The Hive's standing terms.
        minted: The Cell and Warden ids the caller minted for this device.
        at: The Hive Stand's clock now; the Queen's clock decides expiry.

    Returns:
        An `Admission` (the node to create and the accept to send), or the `EnrolRefusal` when
        the invite cannot admit anyone right now.
    """
    refusal = judge_redemption(invite, at)
    if refusal is not None:
        return refusal
    # The device asked for an access level; the operator's cap on the invite can only lower it.
    asked = AccessLevel.from_wire(request.requested_access)
    access = asked if asked.rank <= invite.access_level.rank else invite.access_level
    capabilities = granted_capabilities(access, minted.cell_id)
    reason = _admission_reason(access, invite.comb_shield, invite.label)
    node = _new_node(request, invite, rules, minted, (access, capabilities, reason, at))
    return Admission(node=node, accept=_accept_for(node, rules))


def node_update_for(node: SwarmNode, reason: str) -> NodeUpdate:
    """Build the `swarm.node_update` that restates a node's standing terms.

    Args:
        node: The node as the registry holds it.
        reason: Why the terms are being sent (it comes online, the operator changed its level).

    Returns:
        The update: its rung, access level, tier, capability strings and timings.
    """
    return NodeUpdate(
        device_id=node.device_id,
        cell_id=node.cell_id,
        warden_id=node.warden_id,
        level=node.level,
        access_level=node.access_level.to_wire(),
        comb_shield=node.comb_shield.to_wire(),
        granted_capabilities=node.granted_capabilities,
        offline_limit_s=node.offline_limit_s,
        heartbeat_interval_s=node.heartbeat_interval_s,
        reason=reason,
    )


def _admission_reason(access: AccessLevel, shield: CombShieldLevel, label: str | None) -> str:
    """Say, in one sentence, why the device was admitted at this level and tier."""
    named = f"invite labelled {label!r}" if label else "an unlabelled invite"
    return f"Admitted at {access.name} access and {shield.name} tier by {named}."


def _new_node(
    request: EnrolRequest,
    invite: SwarmInvite,
    rules: EnrolmentRules,
    minted: MintedIds,
    terms: tuple[AccessLevel, tuple[str, ...], str, datetime],
) -> SwarmNode:
    """Assemble the SwarmNode an admitted request becomes: ONLINE on the GATEWAY rung."""
    access, capabilities, reason, at = terms
    sub_bees = request.max_sub_bees if request.max_sub_bees is not None else DEFAULT_MAX_SUB_BEES
    return SwarmNode(
        device_id=request.device_id,
        node_id=request.node_id,
        cell_id=minted.cell_id,
        warden_id=minted.warden_id,
        public_key_hex=request.public_key_hex,
        hostname=request.hostname,
        label=invite.label,
        runtime_version=request.runtime_version,
        capabilities=CellCapabilities.from_wire(request.platform, request.capabilities),
        capacity=ForageCapacity(
            host=HostCapacity.from_wire(request.capacity, request.platform),
            local_seats=(),  # A gateway hosts no model server; a Nuc's seats arrive later.
            max_sub_bees=sub_bees,
        ),
        requested_access=AccessLevel.from_wire(request.requested_access),
        access_level=access,
        comb_shield=invite.comb_shield,
        status=NodeStatus.ONLINE,
        level=RuntimeLevel.GATEWAY,
        granted_capabilities=capabilities,
        offline_limit_s=rules.offline_limit_s,
        heartbeat_interval_s=rules.heartbeat_interval_s,
        max_sub_bees=request.max_sub_bees,
        enrolled_at=at,
        last_seen_at=at,  # The enrolment frame is the first verified word from the device.
        reason=reason,
    )


def _accept_for(node: SwarmNode, rules: EnrolmentRules) -> EnrolAccept:
    """Build the `swarm.enrol_accept` for a new node: the Hive's key and the node's limits."""
    endpoint = rules.endpoint
    return EnrolAccept(
        device_id=node.device_id,
        cell_id=node.cell_id,
        hive_id=endpoint.hive_id,
        hive_key_hex=endpoint.hive_key_hex,
        # The packet verifies every later frame against the Hive Stand's key, the only signer a
        # device needs to trust at the start (later a QueenMoved may amend the list).
        trusted_nodes=(
            NodeKey(node_id=endpoint.hive_node_id, public_key_hex=endpoint.hive_key_hex),
        ),
        warden_id=node.warden_id,
        access_level=node.access_level.to_wire(),
        comb_shield=node.comb_shield.to_wire(),
        granted_capabilities=node.granted_capabilities,
        offline_limit_s=node.offline_limit_s,
        heartbeat_interval_s=node.heartbeat_interval_s,
        reason=node.reason,
    )
