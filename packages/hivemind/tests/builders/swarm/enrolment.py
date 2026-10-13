"""Build the pieces of an enrolment: the Hive's address half, its rules, an invite and a request.

An enrolment test needs the same four things in many shapes: the endpoint an invite carries (the
Hive's address, ids and key), the rules the door applies, the registry row of an invite and the
`swarm.enrol_request` a device sends. These builders return real, validated values with defaults a
test overrides one field at a time, and `make_enrol_envelope` wraps a request in the envelope a
device would send it in.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the enrolment, registry
    and gateway tests under tests/unit/swarm and the registry contract suite.

Key invariants:
    - A request built for a party carries that party's real public key, so a frame it signs
      verifies as proof of possession.
    - Every value built here passes the models' own validators.

See Also:
    - hivemind.swarm.enrolment for the models and rules built here.
    - docs/waggle/enrolment.md for the ceremony these values take part in.
"""

from __future__ import annotations

from datetime import timedelta

from builders.cells import make_capabilities
from builders.forage import make_capacity
from builders.swarm.parties import DeviceParty, HiveParty

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm.enrolment import (
    EnrolmentRules,
    InviteEndpoint,
    new_invite_token,
    token_hash,
)
from hivemind.swarm.models import InviteState, SwarmInvite, SwarmNode
from hivemind.swarm.registry import SwarmRegistry
from waggle.clock import Clock, FakeClock
from waggle.envelope import Envelope, Hop, wrap
from waggle.messages.swarm import EnrolRequest

__all__ = [
    "enrol_node",
    "make_endpoint",
    "make_enrol_envelope",
    "make_enrol_request",
    "make_rules",
    "make_swarm_invite",
]

DEFAULT_URL = "ws://127.0.0.1:8720"


def make_endpoint(
    hive: HiveParty, url: str = DEFAULT_URL, tls_sha256: str | None = None
) -> InviteEndpoint:
    """Build the address half of an invite for the Hive Stand `hive`.

    Args:
        hive: The Hive Stand's party; its ids and public key go into the endpoint.
        url: The URL devices dial; plaintext loopback by default.
        tls_sha256: The certificate fingerprint, required exactly for a `wss://` URL.

    Returns:
        A validated InviteEndpoint.
    """
    return InviteEndpoint(
        url=url,
        hive_id=hive.hive_id,
        hive_node_id=hive.node_id,
        hive_key_hex=hive.public_hex,
        tls_sha256=tls_sha256,
    )


def make_rules(
    endpoint: InviteEndpoint,
    *,
    offline_limit_s: float = 120.0,
    heartbeat_interval_s: float = 15.0,
    invite_ttl: timedelta = timedelta(minutes=15),
) -> EnrolmentRules:
    """Build the door's rules: a 120 s dead-man limit, a 15 s heartbeat and a 15-minute invite.

    A plain dataclass rather than a pydantic model, so its fields are named parameters here.

    Args:
        endpoint: The Hive's address half.
        offline_limit_s: The dead-man limit a device is told.
        heartbeat_interval_s: How often a device is told to report.
        invite_ttl: How long a minted invite lives.

    Returns:
        The rules.
    """
    return EnrolmentRules(
        endpoint=endpoint,
        offline_limit_s=offline_limit_s,
        heartbeat_interval_s=heartbeat_interval_s,
        invite_ttl=invite_ttl,
    )


def make_swarm_invite(
    token: str | None = None, clock: Clock | None = None, **overrides: object
) -> SwarmInvite:
    """Build an ISSUED invite for `token` (a fresh one when omitted), valid for fifteen minutes.

    Args:
        token: The token whose hash the invite is stored under.
        clock: Source of the creation time; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated SwarmInvite.
    """
    active = clock if clock is not None else FakeClock()
    created = active.now()
    fields: dict[str, object] = {
        "code_hash": token_hash(token if token is not None else new_invite_token()),
        "label": None,
        "access_level": AccessLevel.FULL,
        "comb_shield": CombShieldLevel.MEADOW,
        "created_at": created,
        "expires_at": created + timedelta(minutes=15),
        "state": InviteState.ISSUED,
    }
    fields.update(overrides)
    return SwarmInvite(**fields)


def make_enrol_request(party: DeviceParty, token: str, **overrides: object) -> EnrolRequest:
    """Build the `swarm.enrol_request` a device of `party` sends with `token`.

    Args:
        party: The device; its ids and real public key go into the request.
        token: The invite token the device presents.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated EnrolRequest asking for FULL access, as a Linux terminal-only device.
    """
    platform, capabilities = make_capabilities().to_wire()
    fields: dict[str, object] = {
        "invite_token": token,
        "device_id": party.device_id,
        "node_id": party.node_id,
        "public_key_hex": party.public_hex,
        "hostname": "test-device",
        "runtime_version": "1.0.0",
        "platform": platform,
        "capabilities": capabilities,
        "capacity": make_capacity().host.to_wire(),
        "max_sub_bees": None,
        "requested_access": AccessLevel.FULL.to_wire(),
    }
    fields.update(overrides)
    return EnrolRequest(**fields)


def make_enrol_envelope(
    party: DeviceParty, hive: HiveParty, request: EnrolRequest, clock: Clock | None = None
) -> Envelope:
    """Wrap an enrolment request in the envelope a device sends it in, addressed to the Queen.

    Args:
        party: The sender; its device id is the envelope's sender and its node the signer.
        hive: The Hive Stand; its Queen address is the recipient.
        request: The request to carry.
        clock: Source of the envelope's id and time; a fresh FakeClock when omitted.

    Returns:
        An unsigned Envelope; a codec signs it on send.
    """
    active = clock if clock is not None else FakeClock()
    hop = Hop(sender=party.device_id, recipient=hive.hive_id, node_id=party.node_id)
    return wrap(request, hop, clock=active)


async def enrol_node(
    registry: SwarmRegistry, node: SwarmNode, clock: Clock | None = None
) -> SwarmNode:
    """Put `node` into `registry` the only way a registry admits one: by redeeming an invite.

    Args:
        registry: The registry to enrol into.
        node: The node to create, with whatever status and terms the test needs.
        clock: Source of the invite's times; a fresh FakeClock when omitted.

    Returns:
        The stored node.
    """
    active = clock if clock is not None else FakeClock()
    invite = make_swarm_invite(clock=active)
    await registry.put_invite(invite)
    return await registry.redeem(invite.code_hash, node, active.now())
