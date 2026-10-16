"""Define HeldCells and ResumeHints: what the dispatcher leaves alone, and where it resumes from.

When a Swarm device is lost (its link dropped, or its Warden's did) the Queen does not fail the
tasks on it and does not move them: she *holds* them for `[swarm] unreachable_grace_s`, because the
device usually comes back, and a Nuc is still working on its own pool (ADR-0053). `HeldCells` is the
whole of that hold as the dispatcher sees it: the Cells whose node is away, each with a short
reason. The swarm gate fills it when a device is lost and empties it when the device returns (or is
revoked), after it has told the returning Warden which tasks were placed elsewhere meanwhile; the
dispatcher only reads it, and where it reads it, it acts on nothing that is held:
it does not retry or resume a held Cell's task, it does not place new work on a held Cell, and the
Queen's result handling does not fail one. `ResumeHints` is the other half of a decision that does
move tasks: when the grace ends on a device whose Warden cannot be waited for, its tasks go back to
PENDING to be placed again (roadmap 11.9), and each one is told where to resume from, the last
Handoff (the document a bee writes before its context is reset) the Queen holds for it, so the
placement that follows carries `resume_from` instead of starting the work over.

Both are plain in-memory books owned by the dispatcher's own `DispatchBook` (one per Queen): a
restarted Queen holds nothing she did not decide in this life, and the hold is re-derived from the
registry and the live links on her first reconcile pass.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's holds
    sub-package. Held by `hivemind.queen.deps.DispatchBook`; written by the swarm gate
    (`hivemind.queen.swarm_gate.unreachable`) and `hivemind.queen.attach`; read by
    `hivemind.queen.dispatcher` and `hivemind.queen.ticks.results`. Calls into waggle (ids,
    messages) only.

Key invariants:
    - A Cell is held or it is not: holding it twice keeps the first reason, releasing an unheld
      Cell does nothing, and neither raises.
    - A resume hint is taken once: `take` removes it, so a task re-placed twice resumes from what
      was noted for the second placement and never from a stale first one.
    - Neither book touches the chamber, the trail or a wire: they are decisions written down, and
      whoever reads them acts.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the hold and the decision.
    - hivemind.queen.swarm_gate.unreachable for the writer of both books.
    - hivemind.queen.deps for DispatchBook, which owns one of each.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from waggle.ids import CellId, TaskId
from waggle.messages import HandoffRef

__all__ = ["HeldCells", "ResumeHints"]


@dataclass(slots=True)
class HeldCells:
    """The Cells whose tasks the Queen is holding, and why; owned in place (codingrules 8.5).

    Attributes:
        reasons: Each held Cell's id and the short reason it is held (a stable word, never an
            address or a path).
    """

    reasons: dict[CellId, str] = field(default_factory=dict)

    def hold(self, cell_id: CellId, reason: str) -> None:
        """Hold `cell_id`'s tasks; a Cell already held keeps the reason it was first held for."""
        self.reasons.setdefault(cell_id, reason)

    def release(self, cell_id: CellId) -> bool:
        """Stop holding `cell_id`; True when it was held, False when there was nothing to lift."""
        return self.reasons.pop(cell_id, None) is not None

    def is_held(self, cell_id: CellId | None) -> bool:
        """Whether `cell_id` is held; a task with no Cell (not placed yet) never is."""
        return cell_id is not None and cell_id in self.reasons

    def reason(self, cell_id: CellId) -> str | None:
        """Return why `cell_id` is held, or None when it is not."""
        return self.reasons.get(cell_id)


@dataclass(slots=True)
class ResumeHints:
    """Where each re-placed task resumes from, until its next placement takes the hint.

    Attributes:
        refs: A re-placed task's id and the Handoff it should resume from.
    """

    refs: dict[TaskId, HandoffRef] = field(default_factory=dict)

    def note(self, task_id: TaskId, ref: HandoffRef) -> None:
        """Remember that `task_id`, when placed next, resumes from `ref`."""
        self.refs[task_id] = ref

    def take(self, task_id: TaskId) -> HandoffRef | None:
        """Return and forget the Handoff `task_id` resumes from, or None when it starts fresh."""
        return self.refs.pop(task_id, None)
