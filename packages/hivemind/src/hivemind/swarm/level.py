"""Define the runtime ladder's transition table: which rung a device may move to next.

A device's Pollen Packet (the thin gateway on it) is the floor of a three-rung ladder
(ADR-0052). `GATEWAY` (Level 0) is the packet alone, with the device's Warden (the always-on
supervisor of one Cell, a unit of compute) running on the Hive Stand (the machine the Queen, the
central orchestrator, runs on). `COLONIZED` (Level 1) puts the Warden and its sub-bees on the
device. `NUC` (Level 2) adds a model server there too, so the device keeps working with no link
to the Hive Stand. The Warden is always the first bee to move, so the ladder is climbed and
descended one rung at a time and never skipped: `GATEWAY` to `NUC` would put a model server on a
device with no Warden to supervise it. This module reuses the wire enum
`waggle.messages.swarm.RuntimeLevel`, member for member, and holds the one table that decides
whether a rung change is legal. The decisions that choose to climb (`decide_colonize`,
`decide_nuc`) are pure functions that land with the steps that use them (roadmap 11.6a and 11.8).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Read by the swarm registry, which
    refuses an illegal rung change before writing it, and later by the Queen's swarm gate when it
    migrates a Warden. Calls into hivemind.swarm.errors and waggle.messages.swarm only.

Key invariants:
    - The edges are exactly `GATEWAY <-> COLONIZED <-> NUC`; `GATEWAY <-> NUC` is never an edge.
    - A rung up needs a decision and the Guard's `NUC_PROMOTION` point; `COLONIZED -> GATEWAY`
      happens only when the on-device Warden is idle in `WATCH`; `NUC -> COLONIZED` is local to
      the device's Warden. This module decides which edges exist, not who may take them.
    - Every edge, up or down, is a `swarm.promoted` or `swarm.demoted` trail event with
      `from_level` and `to_level`, written in the same transaction as the change.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder and why one rung at a time.
    - .claude/codingrules.md Appendix C, "Runtime ladder" row.
    - hivemind.swarm.node_state for the other machine on a node, its reachability.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.swarm.errors import LadderError
from waggle.messages.swarm import RuntimeLevel

__all__ = ["TRANSITIONS", "RuntimeLevel", "assert_move", "can_move", "is_promotion"]

# The single transition table (codingrules section 9): one entry per rung, each edge commented
# with what causes it. GATEWAY and NUC are absent from each other's sets on purpose.
TRANSITIONS: Mapping[RuntimeLevel, frozenset[RuntimeLevel]] = {
    RuntimeLevel.GATEWAY: frozenset(
        {
            RuntimeLevel.COLONIZED,  # colonization: the Warden moves onto the device first
        }
    ),
    RuntimeLevel.COLONIZED: frozenset(
        {
            RuntimeLevel.NUC,  # a model server starts on the device under its Warden's ceilings
            RuntimeLevel.GATEWAY,  # withdrawal: the on-device Warden is idle in WATCH and stops
        }
    ),
    RuntimeLevel.NUC: frozenset(
        {
            RuntimeLevel.COLONIZED,  # demotion: the device's Warden stops its model server
        }
    ),
}

# Position on the ladder, lowest first: how a move is told to be a rung up or a rung down.
_RANK: Mapping[RuntimeLevel, int] = {
    RuntimeLevel.GATEWAY: 0,
    RuntimeLevel.COLONIZED: 1,
    RuntimeLevel.NUC: 2,
}


def can_move(from_level: RuntimeLevel, to_level: RuntimeLevel) -> bool:
    """Return whether TRANSITIONS allows moving from `from_level` to `to_level`.

    Args:
        from_level: The rung the device is on.
        to_level: The rung a caller wants it on.

    Returns:
        True if `to_level` is one of the edges TRANSITIONS lists for `from_level`.
    """
    return to_level in TRANSITIONS[from_level]


def is_promotion(from_level: RuntimeLevel, to_level: RuntimeLevel) -> bool:
    """Return whether moving from `from_level` to `to_level` climbs the ladder.

    Used to choose between the two trail kinds an edge is recorded as (`swarm.promoted` for a
    rung up, `swarm.demoted` for a rung down); it does not check the edge is legal.

    Args:
        from_level: The rung the device is on.
        to_level: The rung it is moving to.

    Returns:
        True when `to_level` is higher than `from_level`.
    """
    return _RANK[to_level] > _RANK[from_level]


def assert_move(
    from_level: RuntimeLevel, to_level: RuntimeLevel, device_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_level` to `to_level`.

    Args:
        from_level: The rung the device is on.
        to_level: The rung a caller wants it on.
        device_id: The device, when the caller has it, folded into the error message.

    Raises:
        LadderError: The move skips a rung (GATEWAY to NUC, or back) or does not move at all.
    """
    # Every writer of a node's level goes through this single check, so a rung is never skipped
    # anywhere the table itself does not list the edge.
    if not can_move(from_level, to_level):
        raise LadderError(from_level, to_level, device_id=device_id)
