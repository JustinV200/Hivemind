"""Define NodeStatus and its one transition table: whether an enrolled device can be reached.

A Swarm node is one enrolled device (an existing machine the Hive borrows as a Real Cell, a unit
of compute). Its status says only whether the Hive can reach it: `ONLINE` while its Pollen Packet
(the thin gateway on the device) keeps a link up and keeps reporting, `UNREACHABLE` when the link
is lost or heartbeats (its periodic "still here" reports) stop, `REVOKED` once the operator
removed it for good. Access level and Comb Shield tier (how much of the device the Hive may touch,
and its security tier) are attributes of the node, not states, and the rung of the runtime ladder
it is on has its own machine (`hivemind.swarm.level`). The transition table is the single place
that decides whether a status change is legal (codingrules section 9, Appendix C).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Read by the swarm registry
    (`hivemind.swarm.registry`), which refuses an illegal edge before writing it, and by the
    heartbeat monitor, which asks `can_transition` before it records `swarm.online` or
    `swarm.unreachable`. Calls into hivemind.swarm.errors only.

Key invariants:
    - A node is created `ONLINE` by the enrolment that redeems its invite, so there is no
      "enrolled but never seen" state.
    - `REVOKED` is final: nothing leaves it, and the registry keeps the row for the audit.
    - Every status edge is a trail event (`swarm.online`, `swarm.unreachable`, `swarm.revoked`)
      written in the same transaction as the change; the registry enforces that, this module only
      decides which edges exist.

See Also:
    - .claude/codingrules.md Appendix C, "Swarm node status" row.
    - docs/waggle/enrolment.md sections 8 and 11 for what moves a node along these edges.
    - hivemind.swarm.level for the runtime ladder, the other machine on a node.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.swarm.errors import InvalidNodeTransitionError

__all__ = ["TRANSITIONS", "NodeStatus", "assert_transition", "can_transition"]


class NodeStatus(Enum):
    """Whether the Hive can reach an enrolled device right now."""

    ONLINE = "ONLINE"  # The packet's link is up and its heartbeats arrive on time.
    UNREACHABLE = "UNREACHABLE"  # The link was lost or too many heartbeats were missed.
    REVOKED = "REVOKED"  # The operator removed the node: final, the key is refused.


# The single transition table (codingrules section 9): one entry per NodeStatus, each edge
# commented with what causes it. Nothing else decides whether a status change is legal; every
# writer goes through can_transition / assert_transition.
TRANSITIONS: Mapping[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.ONLINE: frozenset(
        {
            NodeStatus.UNREACHABLE,  # the link dropped, or heartbeat_miss_limit beats were missed
            NodeStatus.REVOKED,  # the operator ran `hive swarm revoke`
        }
    ),
    NodeStatus.UNREACHABLE: frozenset(
        {
            NodeStatus.ONLINE,  # the next verified heartbeat (or a reconnect) restored it
            NodeStatus.REVOKED,  # revoked while away: the packet finds out at its next dial
        }
    ),
    NodeStatus.REVOKED: frozenset(),  # terminal: the key is refused and the row kept for audit
}


def can_transition(from_status: NodeStatus, to_status: NodeStatus) -> bool:
    """Return whether TRANSITIONS allows moving from `from_status` to `to_status`.

    Args:
        from_status: The node's current status.
        to_status: The status a caller wants to move it to.

    Returns:
        True if `to_status` is one of the edges TRANSITIONS lists for `from_status`.
    """
    return to_status in TRANSITIONS[from_status]


def assert_transition(
    from_status: NodeStatus, to_status: NodeStatus, device_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_status` to `to_status`.

    Args:
        from_status: The node's current status.
        to_status: The status a caller wants to move it to.
        device_id: The node's device, when the caller has it, folded into the error message.

    Raises:
        InvalidNodeTransitionError: `to_status` is not one of the edges TRANSITIONS lists for
            `from_status`, for instance reviving a revoked node.
    """
    # Every writer of a node's status goes through this single check, so no edge is legal
    # anywhere the table itself does not list it.
    if not can_transition(from_status, to_status):
        raise InvalidNodeTransitionError(from_status, to_status, device_id=device_id)
