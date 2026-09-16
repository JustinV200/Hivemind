"""Define QueenMode and the one transition table the Queen's own mode moves through.

The Queen (the Hive's single orchestrator) has a mode of her own, distinct from any one Warden's
`WardenState` or any one task's `TaskStatus`: whether she is still coming up after a restart
(`REQUEENING`, roadmap step 4.14), running normally (`RUNNING`), paused because one or more model
providers are unavailable (`CLUSTERED`, this module's own reason for existing -- Clustering, the
pause-and-preserve protocol of `hivemind.queen.cluster`), or moving the Hive Stand to a new host
(`SUPERSEDING`/`SUPERSEDED`, roadmap step 8.16, docs/adr not yet written for that phase). This
module is the state machine codingrules section 9 requires for every machine in the Hive: one
`Enum` (`QueenMode`) plus one transition table (`TRANSITIONS`), mirroring
`hivemind.wardens.state.WardenState`'s own shape (`can_transition`, `assert_transition`) field for
field. `ClusterState` is the small piece of mutable bookkeeping codingrules Appendix C's "Queen
mode" row asks for beyond the bare enum: "per provider set" -- the Queen's mode is `CLUSTERED`
while *any* provider is currently clustered, and returns to `RUNNING` only once none are.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Read by
    `hivemind.queen.cluster.protocol` (`cluster`/`resume`, which mutate one `ClusterState` via
    `mark_clustered`/`mark_resumed`) and by `hivemind.queen.cluster.tick.run_cluster_tick`
    (`awake_available`, roadmap step 4.9's own autopilot hook: the Queen's own awake mode is
    skipped while her own `ModelSlot.QUEEN` provider is clustered). `Queen` holds one `ClusterState`
    instance (see `hivemind.queen.cluster.protocol`'s own module docstring for the exact
    constructor line the orchestrator adds, since this dispatch may not edit `queen/queen.py`
    itself). Calls into `hivemind.queen.errors` only.

Key invariants:
    - QueenMode's member names and values are exactly Appendix C's "Queen mode" row: `REQUEENING`,
      `RUNNING`, `CLUSTERED`, `SUPERSEDING`, `SUPERSEDED`.
    - TRANSITIONS has exactly one entry per QueenMode member; `SUPERSEDED` maps to an empty
      frozenset, the one mode a Queen never leaves once she reaches it (the old Queen, after a
      completed Supersedure).
    - `SUPERSEDING`/`SUPERSEDED` are declared members with their own table rows present (this
      dispatch's own instruction), even though nothing in roadmap phase 4 ever drives a `Queen`
      into either: Supersedure is roadmap step 8.16, not this one.
    - `can_transition`, `assert_transition` and `is_terminal` read TRANSITIONS only; none
      hard-codes an edge.
    - `ClusterState.mode` is `QueenMode.CLUSTERED` exactly while `clustered_providers` is
      non-empty; `mark_clustered`/`mark_resumed` are the only two ways to change either, and both
      go through `assert_transition` on the way (a caller can never see `ClusterState` move to an
      edge the table forbids).

See Also:
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md Appendix C, "Queen mode" row, for the transition table implemented
      here.
    - .claude/codingrules.md section 8.13 and docs/adr/0024-clustering-protocol.md for Clustering,
      the protocol `ClusterState` tracks the Queen's own mode for.
    - hivemind.wardens.state for WardenState, the sibling machine this module's shape mirrors.
    - hivemind.queen.cluster.protocol for cluster/resume, ClusterState's two mutators' one caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

from hivemind.queen.errors import QueenError

__all__ = [
    "TRANSITIONS",
    "ClusterState",
    "InvalidQueenModeTransitionError",
    "QueenMode",
    "assert_transition",
    "can_transition",
    "is_terminal",
]


class QueenMode(Enum):
    """Every mode the Queen herself can be in; see TRANSITIONS for the legal moves between them."""

    REQUEENING = "REQUEENING"  # Coming up after a restart, reading her own copied stores back.
    RUNNING = "RUNNING"  # Normal operation: every event is autopilot then awake, per event.
    CLUSTERED = "CLUSTERED"  # At least one bound provider is down; that provider's bees are paused.
    SUPERSEDING = "SUPERSEDING"  # Moving the Hive Stand to a new host (roadmap step 8.16).
    SUPERSEDED = "SUPERSEDED"  # Terminal: the old Queen, once a Supersedure has completed.


# The single transition table (codingrules section 9): one entry per QueenMode, each edge
# commented with who or what causes it. Mirrors Appendix C's own compressed notation, expanded to
# one edge per line, the same way hivemind.wardens.state.TRANSITIONS expands "any -> CLUSTERED".
TRANSITIONS: Mapping[QueenMode, frozenset[QueenMode]] = {
    QueenMode.REQUEENING: frozenset(
        {
            QueenMode.RUNNING,  # Her own stores are read back and every attached Warden re-links.
        }
    ),
    QueenMode.RUNNING: frozenset(
        {
            QueenMode.CLUSTERED,  # A bound provider went DOWN with no fallback, or a cost cap hit.
        }
    ),
    QueenMode.CLUSTERED: frozenset(
        {
            QueenMode.RUNNING,  # The last clustered provider recovered or was woken.
            QueenMode.SUPERSEDING,  # A Supersedure may start while paused (roadmap step 8.16).
        }
    ),
    QueenMode.SUPERSEDING: frozenset(
        {
            QueenMode.SUPERSEDED,  # The move to the new host completed; this Queen stands down.
            QueenMode.CLUSTERED,  # The move was rolled back; she is still clustered underneath.
        }
    ),
    QueenMode.SUPERSEDED: frozenset(),  # terminal: nothing follows
}


class InvalidQueenModeTransitionError(QueenError):
    """Raise when the Queen mode machine is asked for an edge its transition table does not have.

    Rooted at `hivemind.queen.errors.QueenError` directly rather than a new shared root: this is
    the one error `hivemind.queen.state` raises, the same way `hivemind.queen.placement.
    PlacementError` roots at `QueenError` beside the one function that raises it (codingrules
    section 10: every subsystem roots at `hivemind.common.errors.HiveMindError`, but a leaf module
    may define its own specific subclass beside its own raiser rather than growing a shared
    `errors.py` this dispatch does not own).
    """

    code: ClassVar[str] = "hivemind.queen.invalid_mode_transition"

    def __init__(self, from_mode: QueenMode, to_mode: QueenMode) -> None:
        """Build the error for a forbidden Queen mode transition.

        Args:
            from_mode: The QueenMode the machine was in.
            to_mode: The QueenMode a caller asked to move to.
        """
        super().__init__(
            f"Cannot transition the Queen's own mode from {from_mode.name} to {to_mode.name}: "
            "no such edge exists in the Queen mode machine."
        )
        self.from_mode = from_mode
        self.to_mode = to_mode


def can_transition(from_mode: QueenMode, to_mode: QueenMode) -> bool:
    """Return whether TRANSITIONS allows moving from `from_mode` to `to_mode`.

    Args:
        from_mode: The Queen's current mode.
        to_mode: The mode a caller wants to move her to.

    Returns:
        True if `to_mode` is one of the edges TRANSITIONS lists for `from_mode`.
    """
    return to_mode in TRANSITIONS[from_mode]


def assert_transition(from_mode: QueenMode, to_mode: QueenMode) -> None:
    """Raise unless TRANSITIONS allows moving from `from_mode` to `to_mode`.

    Args:
        from_mode: The Queen's current mode.
        to_mode: The mode a caller wants to move her to.

    Raises:
        InvalidQueenModeTransitionError: `to_mode` is not one of the edges TRANSITIONS lists for
            `from_mode`, for instance moving a SUPERSEDED Queen anywhere.
    """
    # Every caller that advances the Queen's own mode goes through this single check
    # (ClusterState below), so no edge is ever legal anywhere the table itself does not list it.
    if not can_transition(from_mode, to_mode):
        raise InvalidQueenModeTransitionError(from_mode, to_mode)


def is_terminal(mode: QueenMode) -> bool:
    """Return whether `mode` is one the Queen never leaves once she reaches it.

    Args:
        mode: The mode to check.

    Returns:
        True for SUPERSEDED (TRANSITIONS maps it to an empty frozenset); False otherwise.
    """
    return not TRANSITIONS[mode]


@dataclass(slots=True)
class ClusterState:
    """The Queen's own mode, plus which providers are currently clustered (per provider set).

    Appendix C's "Queen mode" row reads "`RUNNING <-> CLUSTERED` (per provider set)": Clustering
    is per provider (docs/adr/0024), so the Queen's *mode* is a single derived value over however
    many providers are currently down -- `CLUSTERED` while at least one is, `RUNNING` again only
    once none are. A `Queen` instance holds exactly one of these (see the module docstring for the
    constructor line this dispatch reports rather than adds).

    Every field is private; `mode` and `clustered_providers` are read-only views, and
    `mark_clustered`/`mark_resumed` are the only two ways to change either.
    """

    _mode: QueenMode = field(default=QueenMode.RUNNING)
    _clustered_providers: set[str] = field(default_factory=set)

    @property
    def mode(self) -> QueenMode:
        """The Queen's own current mode, derived from `clustered_providers`."""
        return self._mode

    @property
    def clustered_providers(self) -> frozenset[str]:
        """Every `[llm.providers.<name>]` key currently clustered, in no particular order."""
        return frozenset(self._clustered_providers)

    def mark_clustered(self, provider: str) -> None:
        """Record `provider` as clustered, moving the Queen's own mode to CLUSTERED if needed.

        Idempotent: a provider already recorded as clustered changes nothing further.

        Args:
            provider: The `[llm.providers.<name>]` key that just went DOWN with no fallback, hit
                its cost cap, or was named by `hive cluster <provider>`.

        Raises:
            InvalidQueenModeTransitionError: The Queen's own current mode has no edge to
                CLUSTERED (she is REQUEENING, SUPERSEDING or SUPERSEDED).
        """
        if provider in self._clustered_providers:
            return  # Already clustered; module docstring's idempotence contract.
        if self._mode is not QueenMode.CLUSTERED:
            assert_transition(self._mode, QueenMode.CLUSTERED)
            self._mode = QueenMode.CLUSTERED
        self._clustered_providers.add(provider)

    def mark_resumed(self, provider: str) -> None:
        """Drop `provider` from the clustered set, moving back to RUNNING once none remain.

        Idempotent: a provider not currently recorded as clustered changes nothing further.

        Args:
            provider: The provider that recovered, or was named by `hive wake`.

        Raises:
            InvalidQueenModeTransitionError: `clustered_providers` would become empty but the
                Queen's own current mode has no edge back to RUNNING (defensive: `mark_clustered`
                is the only way in, so this should never actually fire).
        """
        if provider not in self._clustered_providers:
            return  # Never clustered (or already resumed); module docstring's idempotence rule.
        self._clustered_providers.discard(provider)
        if not self._clustered_providers and self._mode is QueenMode.CLUSTERED:
            assert_transition(self._mode, QueenMode.RUNNING)
            self._mode = QueenMode.RUNNING
