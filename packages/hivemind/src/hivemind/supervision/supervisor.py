"""Define the Supervisor protocol: the one interface human, Queen, Warden and sub-bee all share.

The tree is human -> Queen -> Wardens -> sub-bees, and the same `Supervisor` protocol is used at
every level (codingrules section 8.8, README "Core concepts" 1 and 2): list the children being
supervised, read a child's telemetry, ask for a compacted view of its context, and pull one of the
six intervention levers on it. `ChildRef` is a supervisor's own summary row for one child -- a
plain, frozen value (codingrules 8.5: an internal value, never crossing a network boundary itself)
rather than a pydantic boundary model, because nothing here validates untrusted input; it is built
from whatever state the supervisor already holds. `ChildKind` says which of the two shapes a child
takes: a Warden supervises Workers only, but the Queen supervises Wardens, so the protocol is
generic over both.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implemented by the Queen (over
    Wardens), a Warden (over its sub-bees) and `hivemind.supervision.fake.FakeSupervisor` (in
    tests). Calls into `hivemind.supervision.intervention` and waggle only.

Key invariants:
    - Every Supervisor implementation raises `hivemind.supervision.errors.UnknownChildError` from
      `telemetry`, `inspect` and `intervene` when `child` names nothing it supervises.
    - `children()` never raises for an unknown id: there is no id to be unknown yet, since it is
      the source of every id `telemetry`/`inspect`/`intervene` accept.

See Also:
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule this module follows,
      and its Supervisor row.
    - .claude/codingrules.md section 8.8 for the supervision tree this protocol is the seam of.
    - README.md "Core concepts" 1 (the Queen) and 2 (Wardens) for the plain-English shape.
    - hivemind.supervision.intervention for Intervention, the argument to `intervene`.
    - hivemind.supervision.fake for FakeSupervisor, the scripted implementation tests use.
    - waggle.messages.supervision for ContextTelemetry and CompactView, the two wire value models
      `telemetry` and `inspect` return directly (codingrules section 6.1: not mirrored).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from hivemind.supervision.intervention import Intervention
from waggle.ids import TaskId
from waggle.messages.supervision import CompactView, ContextTelemetry

__all__ = ["ChildKind", "ChildRef", "Supervisor"]


class ChildKind(Enum):
    """Which of the two shapes a supervised child takes."""

    WARDEN = "WARDEN"  # A per-Cell supervisor, supervised by the Queen.
    WORKER = "WORKER"  # A sub-bee doing one task, supervised by a Warden.


@dataclass(frozen=True, slots=True)
class ChildRef:
    """One supervised child, as a supervisor's own summary row.

    An internal value (codingrules 8.5), not a pydantic boundary model: it never crosses a
    network boundary itself, only ever gets built from state a `Supervisor` implementation
    already holds (its own children table, a Heartbeat's `children` rows).
    """

    id: str  # The child's WorkerId or WardenId, as a plain string.
    kind: ChildKind
    task_id: TaskId | None  # The child's current task; None while idle or in WATCH.
    state: str  # The child's own WorkerState/WardenState wire value, as a plain string.


class Supervisor(Protocol):
    """List, watch and steer the children one level of the supervision tree supervises.

    Implementations (the Queen over Wardens, a Warden over its sub-bees, `FakeSupervisor` in
    tests) must be safe to call concurrently: several inbox items about different children may be
    handled in the same tick.
    """

    async def children(self) -> tuple[ChildRef, ...]:
        """Return every child this supervisor currently supervises.

        Returns:
            One ChildRef per child, in no particular order.
        """
        ...

    async def telemetry(self, child: str) -> ContextTelemetry:
        """Return `child`'s most recently reported telemetry.

        Args:
            child: The child's id, from a ChildRef this supervisor returned.

        Returns:
            The child's ContextTelemetry as of its last heartbeat.

        Raises:
            UnknownChildError: `child` names nothing this supervisor supervises.
        """
        ...

    async def inspect(self, child: str) -> CompactView:
        """Ask `child` for a compacted view of its context.

        Args:
            child: The child's id, from a ChildRef this supervisor returned.

        Returns:
            The child's CompactView at reply time.

        Raises:
            UnknownChildError: `child` names nothing this supervisor supervises.
        """
        ...

    async def intervene(self, child: str, intervention: Intervention) -> None:
        """Pull one lever on `child`: compact, checkpoint, hand off, rebind, take over or cancel.

        Args:
            child: The child's id, from a ChildRef this supervisor returned.
            intervention: The lever to pull, and why.

        Returns:
            None, once the intervention has been sent.

        Raises:
            UnknownChildError: `child` names nothing this supervisor supervises.
        """
        ...
