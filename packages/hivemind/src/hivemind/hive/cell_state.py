"""Define VirtualCellStatus and the one transition table that governs a Virtual Cell's lifecycle.

A Virtual Cell (a VM or container the Hive provisions and later destroys or Overwinters) moves
through a fixed set of states from being requested to being gone. This module is the state machine
codingrules section 9 requires for every machine in the Hive: one `Enum` (`VirtualCellStatus`) plus
one transition table (`TRANSITIONS`), each allowed edge commented with who causes it, tested edge
by edge. The happy path mirrors roadmap step 5.6's own words almost verbatim: "provision -> Warden
ready -> grant -> release -> (overwinter | teardown)"; the Night Veil tier (`hivemind.cell.
CombShieldLevel.NIGHT_VEIL`, virtual-only and teardown-only, codingrules section 8.7) is always
"provision -> Warden ready -> grant -> release -> teardown", never Overwintered, which this module
enforces separately from the table itself: `can_enter_dormant`/`assert_dormant_allowed` are the
pure invariant check the DORMANT edge alone cannot express, because a bare transition table has no
way to see which Cell -- and therefore which CombShieldLevel -- is asking. Nothing else in the
Hive decides whether a Virtual Cell transition is legal or whether a DORMANT move is allowed; a
later step (`hivemind.hive.lifecycle`, roadmap step 5.6, not yet built) is the only intended caller
of both.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Read by hivemind.hive.lifecycle
    (roadmap step 5.6, not yet built) before every Virtual Cell state change. Calls into
    hivemind.hive.errors and hivemind.cell (CombShieldLevel) only.

Key invariants:
    - TRANSITIONS has exactly one entry per VirtualCellStatus member; DESTROYED and FAILED (the
      two terminal states) map to an empty frozenset.
    - can_transition and assert_transition read TRANSITIONS only; neither hard-codes an edge.
    - A Cell whose CombShieldLevel is NIGHT_VEIL may never reach DORMANT (codingrules section 8.7:
      "Night Veil lifecycle is teardown-only"); can_enter_dormant and assert_dormant_allowed are
      the one place that rule is checked, taking the CombShieldLevel as a plain argument since
      this table has no notion of which Cell is transitioning.

See Also:
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md section 8.7 for the Night Veil teardown-only rule this module's
      can_enter_dormant enforces.
    - .claude/roadmap.md step 5.6 for the lifecycle this table implements ahead of its own caller.
    - hivemind.cell.lease_state for LeaseState and TRANSITIONS, the pattern this module mirrors
      for a different state machine.
    - hivemind.hive.errors for InvalidCellTransitionError, the error assert_transition and
      assert_dormant_allowed both raise.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.cell import CombShieldLevel
from hivemind.hive.errors import InvalidCellTransitionError

__all__ = [
    "TRANSITIONS",
    "VirtualCellStatus",
    "assert_dormant_allowed",
    "assert_transition",
    "can_enter_dormant",
    "can_transition",
]


class VirtualCellStatus(Enum):
    """Every state a Virtual Cell can be in, from provisioning to gone.

    See TRANSITIONS below for the legal moves between these; nowhere else decides that.
    """

    PROVISIONING = "PROVISIONING"  # A CellBackend.provision call is in flight.
    READY = "READY"  # Running and reachable; the Warden connected and its first Heartbeat arrived.
    GRANTED = "GRANTED"  # Placement handed this Cell to a task; a Worker is using it.
    RELEASED = "RELEASED"  # The task ended; policy has not yet decided overwinter or teardown.
    DORMANT = "DORMANT"  # Overwintered: paused, disk kept, for fast reuse (roadmap step 5.9).
    DESTROYING = "DESTROYING"  # A CellBackend.destroy call is in flight.
    DESTROYED = "DESTROYED"  # Terminal: every resource this Cell held is gone.
    FAILED = (
        "FAILED"  # Terminal: provisioning never completed; the backend cleaned up after itself.
    )


# The single transition table (codingrules section 9): one entry per VirtualCellStatus, each edge
# commented with who or what causes it. This is the only place that decides whether a Virtual Cell
# transition is legal; every caller goes through can_transition/assert_transition.
TRANSITIONS: Mapping[VirtualCellStatus, frozenset[VirtualCellStatus]] = {
    VirtualCellStatus.PROVISIONING: frozenset(
        {
            VirtualCellStatus.READY,  # the backend's Cell became reachable in time
            VirtualCellStatus.FAILED,  # the backend could not create it, or it timed out
        }
    ),
    VirtualCellStatus.READY: frozenset(
        {
            VirtualCellStatus.GRANTED,  # placement handed the Cell to a task
            VirtualCellStatus.DESTROYING,  # torn down before ever being granted (e.g. abscond)
        }
    ),
    VirtualCellStatus.GRANTED: frozenset(
        {
            VirtualCellStatus.RELEASED,  # the task ended
            # The Undertaker destroys a Cell whose Warden died mid-grant, and `hive cells
            # abscond` destroys every Cell whatever it is doing; neither can wait for a release.
            VirtualCellStatus.DESTROYING,
        }
    ),
    VirtualCellStatus.RELEASED: frozenset(
        {
            VirtualCellStatus.DORMANT,  # Overwintering policy keeps it paused for reuse
            VirtualCellStatus.DESTROYING,  # Overwintering policy declines, or Night Veil (always)
        }
    ),
    VirtualCellStatus.DORMANT: frozenset(
        {
            VirtualCellStatus.READY,  # resumed from the pool: the backend woke it and reconnected
            VirtualCellStatus.DESTROYING,  # pool eviction: over the manifest's cap or max idle age
        }
    ),
    VirtualCellStatus.DESTROYING: frozenset({VirtualCellStatus.DESTROYED}),  # backend confirmed
    VirtualCellStatus.DESTROYED: frozenset(),  # terminal: nothing follows
    VirtualCellStatus.FAILED: frozenset(),  # terminal: nothing follows
}


def can_transition(from_status: VirtualCellStatus, to_status: VirtualCellStatus) -> bool:
    """Return whether TRANSITIONS allows moving from `from_status` to `to_status`.

    Args:
        from_status: The Cell's current status.
        to_status: The status a caller wants to move it to.

    Returns:
        True if `to_status` is one of the edges TRANSITIONS lists for `from_status`.
    """
    return to_status in TRANSITIONS[from_status]


def assert_transition(
    from_status: VirtualCellStatus, to_status: VirtualCellStatus, cell_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_status` to `to_status`.

    Args:
        from_status: The Cell's current status.
        to_status: The status a caller wants to move it to.
        cell_id: The Cell's id, when the caller has it, folded into the error message.

    Raises:
        InvalidCellTransitionError: `to_status` is not one of the edges TRANSITIONS lists for
            `from_status`, for instance moving a DESTROYED Cell anywhere.
    """
    # Every caller that changes a Virtual Cell's status goes through this single check
    # (hivemind.hive.lifecycle, roadmap step 5.6), so no edge is ever legal anywhere the table
    # itself does not list it.
    if not can_transition(from_status, to_status):
        raise InvalidCellTransitionError(from_status, to_status, cell_id=cell_id)


def can_enter_dormant(comb_shield: CombShieldLevel) -> bool:
    """Return whether a Cell at this CombShieldLevel may ever move to DORMANT.

    Args:
        comb_shield: The Cell's security tier.

    Returns:
        False for CombShieldLevel.NIGHT_VEIL (codingrules section 8.7: Night Veil is
        teardown-only and is never Overwintered); True for MEADOW and PROPOLIS.
    """
    return comb_shield is not CombShieldLevel.NIGHT_VEIL


def assert_dormant_allowed(comb_shield: CombShieldLevel, cell_id: str | None = None) -> None:
    """Raise unless a Cell at this CombShieldLevel may move to DORMANT.

    Callers still call `assert_transition(from_status, VirtualCellStatus.DORMANT, cell_id)`
    first: this only adds the Night Veil rule the table itself cannot express (see the module
    docstring).

    Args:
        comb_shield: The Cell's security tier.
        cell_id: The Cell's id, when the caller has it, folded into the error message.

    Raises:
        InvalidCellTransitionError: `comb_shield` is NIGHT_VEIL.
    """
    if not can_enter_dormant(comb_shield):
        raise InvalidCellTransitionError(
            VirtualCellStatus.RELEASED,
            VirtualCellStatus.DORMANT,
            cell_id=cell_id,
            reason="Night Veil Cells are teardown-only and may never be Overwintered "
            "(codingrules section 8.7)",
        )
