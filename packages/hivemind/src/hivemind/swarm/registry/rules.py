"""Define the registry's pure rules: who may redeem an invite and how a node's record changes.

The swarm registry (the Hive's table of enrolled devices and their invites) has two storage
implementations, a SQLite one and an in-memory fake, and they must agree on every rule or the
contract suite is a lie. This module is where the rules live once, as pure functions over the
`SwarmInvite` and `SwarmNode` values (codingrules 8.3: pure core, effectful edges): whether an
invite can be redeemed right now and why not, what a redeemed or expired invite looks like, and
the new node record each mutation produces (a status compare-and-set, a rung change, a new
access level, a heartbeat's touch, a refreshed capacity report). The storage classes only fetch a
row, call one of these, and write the result back inside their own transaction, so the single-use
and one-rung-at-a-time rules cannot drift between them.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.registry`.
    Called by `SqliteSwarmRegistry` and `MemorySwarmRegistry`. Calls into hivemind.swarm.errors,
    .level, .models and .node_state only.

Key invariants:
    - Every function returns a new value and raises before returning anything half-changed;
      nothing here mutates a node or an invite in place.
    - A status or rung change is checked against the one transition table of its machine, so a
      forbidden edge raises whichever storage ran it.
    - A revoked node accepts no change at all: its enrolment is final.

See Also:
    - docs/waggle/enrolment.md section 6 for the redemption rules these functions state.
    - hivemind.swarm.registry.protocol for the contract the storage classes keep with them.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.cell import AccessLevel, CellCapabilities, CombShieldLevel
from hivemind.forage import ForageCapacity
from hivemind.swarm.errors import (
    EnrolRefusal,
    NodeRevokedError,
    NodeStatusConflictError,
)
from hivemind.swarm.level import assert_move
from hivemind.swarm.models import (
    InviteState,
    SwarmInvite,
    SwarmNode,
    assert_invite_transition,
)
from hivemind.swarm.node_state import NodeStatus, assert_transition
from waggle.ids import DeviceId
from waggle.messages.swarm import RuntimeLevel

__all__ = [
    "expired_invite",
    "judge_redemption",
    "redeemed_invite",
    "replaced",
    "revoked_invite",
    "touched",
    "with_access",
    "with_ceiling",
    "with_desired_level",
    "with_facts",
    "with_level",
    "with_shield",
    "with_status",
]

# What each non-ISSUED invite state means to a device presenting its token.
_REFUSAL_FOR_STATE = {
    InviteState.REDEEMED: EnrolRefusal.USED,
    InviteState.EXPIRED: EnrolRefusal.EXPIRED,
    InviteState.REVOKED: EnrolRefusal.REVOKED,
}


def judge_redemption(invite: SwarmInvite | None, at: datetime) -> EnrolRefusal | None:
    """Decide whether `invite` can admit a device at `at`, and if not, why.

    Args:
        invite: The invite the presented token hashes to, or None when none does.
        at: The Hive Stand's clock at the moment of presentation; the Queen's clock decides, not
            the device's.

    Returns:
        None when the invite exists, is ISSUED and has not lapsed; otherwise the reason code.
    """
    if invite is None:
        return EnrolRefusal.UNKNOWN_INVITE
    # A used, expired or revoked invite is final, so its state alone answers.
    if invite.state is not InviteState.ISSUED:
        return _REFUSAL_FOR_STATE[invite.state]
    # Still ISSUED but past its time to live: it lapsed without anyone moving it, so the clock
    # answers (the registry persists the EXPIRED state when it sees this).
    if invite.expires_at <= at:
        return EnrolRefusal.EXPIRED
    return None


def expired_invite(invite: SwarmInvite) -> SwarmInvite:
    """Return `invite` moved to EXPIRED.

    Args:
        invite: An ISSUED invite whose time to live has lapsed.

    Returns:
        The same invite in state EXPIRED.

    Raises:
        InviteConflictError: The invite is no longer ISSUED.
    """
    assert_invite_transition(invite.state, InviteState.EXPIRED, invite.code_hash)
    return invite.model_copy(update={"state": InviteState.EXPIRED})


def revoked_invite(invite: SwarmInvite) -> SwarmInvite:
    """Return `invite` moved to REVOKED, as `hive swarm revoke` cancels an unused one.

    Args:
        invite: An ISSUED invite.

    Returns:
        The same invite in state REVOKED.

    Raises:
        InviteConflictError: The invite was already redeemed, expired or revoked.
    """
    assert_invite_transition(invite.state, InviteState.REVOKED, invite.code_hash)
    return invite.model_copy(update={"state": InviteState.REVOKED})


def redeemed_invite(invite: SwarmInvite, device_id: DeviceId) -> SwarmInvite:
    """Return `invite` moved to REDEEMED by the device it admitted.

    Args:
        invite: An ISSUED invite.
        device_id: The device whose enrolment redeemed it.

    Returns:
        The same invite in state REDEEMED, remembering the device.

    Raises:
        InviteConflictError: The invite is no longer ISSUED.
    """
    assert_invite_transition(invite.state, InviteState.REDEEMED, invite.code_hash)
    return invite.model_copy(update={"state": InviteState.REDEEMED, "device_id": device_id})


def replaced(node: SwarmNode, **changes: object) -> SwarmNode:
    """Return `node` with `changes` applied, validated as a whole.

    A plain `model_copy(update=...)` skips validation, which would let a change break the
    node's cross-field rules (a revoked node without a revocation time); rebuilding through the
    model keeps every validator in force.

    Args:
        node: The node to change.
        **changes: Field values that replace the node's own.

    Returns:
        A new, validated SwarmNode.

    Raises:
        pydantic.ValidationError: The result breaks a field or cross-field rule.
    """
    fields = {name: getattr(node, name) for name in SwarmNode.model_fields}
    return SwarmNode.model_validate({**fields, **changes})


def with_status(node: SwarmNode, expected: NodeStatus, new: NodeStatus, at: datetime) -> SwarmNode:
    """Return `node` moved from `expected` to `new`, a compare-and-set on its status.

    Args:
        node: The node as the registry holds it.
        expected: The status the caller believes the node has.
        new: The status to move it to.
        at: When it happened; becomes `revoked_at` for a revocation.

    Returns:
        The node in its new status.

    Raises:
        NodeStatusConflictError: The node is not in `expected`.
        InvalidNodeTransitionError: `expected` to `new` is not an edge.
    """
    # The caller decided on the status it read; if another writer moved it since, the decision
    # is stale and the caller must read again rather than overwrite.
    if node.status is not expected:
        raise NodeStatusConflictError(node.device_id, expected, node.status)
    assert_transition(expected, new, node.device_id)
    if new is NodeStatus.REVOKED:
        return replaced(node, status=new, revoked_at=at)
    return replaced(node, status=new)


def with_level(node: SwarmNode, level: RuntimeLevel) -> SwarmNode:
    """Return `node` moved to the adjacent rung `level`.

    Args:
        node: The node as the registry holds it; not revoked.
        level: The rung to move to.

    Returns:
        The node on its new rung, with a pending `desired_level` cleared once it is reached.

    Raises:
        NodeRevokedError: The node is revoked.
        LadderError: `level` is not an adjacent rung.
    """
    _require_not_revoked(node)
    assert_move(node.level, level, node.device_id)
    # Reaching the rung the operator asked for ends the request; any other rung leaves it pending.
    desired = None if node.desired_level in (None, level) else node.desired_level
    return replaced(node, level=level, desired_level=desired)


def with_desired_level(node: SwarmNode, level: RuntimeLevel | None) -> SwarmNode:
    """Return `node` with the operator's pending rung request set, or cleared by None.

    Args:
        node: The node as the registry holds it; not revoked.
        level: The rung the operator asked for, or None to withdraw the request.

    Returns:
        The node carrying the request. A request for the rung it is already on clears it.

    Raises:
        NodeRevokedError: The node is revoked.
    """
    _require_not_revoked(node)
    # Asking for the rung the node is on asks for nothing.
    return replaced(node, desired_level=None if level is node.level else level)


def with_ceiling(node: SwarmNode, ceiling: RuntimeLevel | None) -> SwarmNode:
    """Return `node` with the highest rung the Queen may take it to on her own set, or cleared.

    Args:
        node: The node as the registry holds it; not revoked.
        ceiling: The rung to hold the device at or below, or None to let the Queen climb freely.

    Returns:
        The node carrying the ceiling. A ceiling equal to the node's own rung is allowed: that is
        exactly a device pinned where it is.

    Raises:
        NodeRevokedError: The node is revoked.
    """
    _require_not_revoked(node)
    return replaced(node, ceiling=ceiling)


def with_access(node: SwarmNode, level: AccessLevel, granted: tuple[str, ...]) -> SwarmNode:
    """Return `node` with a new access level and the capability strings that go with it.

    Args:
        node: The node as the registry holds it; not revoked.
        level: The new `AccessLevel`.
        granted: The capability strings the packet will enforce from now on.

    Returns:
        The node at its new access level.

    Raises:
        NodeRevokedError: The node is revoked.
    """
    _require_not_revoked(node)
    return replaced(node, access_level=level, granted_capabilities=granted)


def with_shield(node: SwarmNode, shield: CombShieldLevel) -> SwarmNode:
    """Return `node` with a new Comb Shield tier, set by the operator.

    Args:
        node: The node as the registry holds it; not revoked.
        shield: MEADOW or PROPOLIS.

    Returns:
        The node at its new tier.

    Raises:
        NodeRevokedError: The node is revoked.
        pydantic.ValidationError: `shield` is NIGHT_VEIL.
    """
    _require_not_revoked(node)
    return replaced(node, comb_shield=shield)


def touched(node: SwarmNode, at: datetime, runtime_version: str | None) -> SwarmNode:
    """Return `node` marked as heard from at `at`, with its runtime version if one was reported.

    Args:
        node: The node as the registry holds it; not revoked.
        at: When a verified frame arrived.
        runtime_version: The `pollen` version it reported, or None to keep the stored one.

    Returns:
        The node with `last_seen_at` moved forward.

    Raises:
        NodeRevokedError: The node is revoked.
    """
    _require_not_revoked(node)
    # last_seen_at only moves forward: a late-processed older frame must not rewind it.
    latest = at if node.last_seen_at is None else max(node.last_seen_at, at)
    version = node.runtime_version if runtime_version is None else runtime_version
    return replaced(node, last_seen_at=latest, runtime_version=version)


def with_facts(
    node: SwarmNode, capabilities: CellCapabilities, capacity: ForageCapacity
) -> SwarmNode:
    """Return `node` with the capabilities and capacity its latest report carried.

    Args:
        node: The node as the registry holds it; not revoked.
        capabilities: What the device reports it can do.
        capacity: Its Forage capacity.

    Returns:
        The node with both replaced.

    Raises:
        NodeRevokedError: The node is revoked.
    """
    _require_not_revoked(node)
    return replaced(node, capabilities=capabilities, capacity=capacity)


def _require_not_revoked(node: SwarmNode) -> None:
    """Raise NodeRevokedError unless the node can still change: revocation is final."""
    if node.status is NodeStatus.REVOKED:
        raise NodeRevokedError(node.device_id)
