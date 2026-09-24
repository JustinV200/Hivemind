"""Build hot-state test inputs: a settable HotStateSources, an AssembleRequest, a scan verdict.

The taint and scanner tests (roadmap steps 10.6b, 10.6d) assemble real prompts and then look for
what must never be in them; they need a `HotStateSources` whose answers a test sets directly, an
`AssembleRequest` with sensible defaults, and verdicts for outside text without running a scanner.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/memory.

Key invariants:
    - `SettableSources.wax` honours `HotStateSources.wax`'s own contract: only notes about the
      Cells asked for.

See Also:
    - hivemind.memory.hot_state for assemble and HotStateSources.
    - builders.memory for the summary builders.
"""

from __future__ import annotations

from dataclasses import dataclass

from builders.memory import make_principal, make_token_budget, make_trigger_event

from hivemind.guard.scanner import HASH_PREFIX, ScanAction, ScanVerdict
from hivemind.memory import (
    AlarmSummary,
    AssembleRequest,
    CellWaxSummary,
    DecisionSummary,
    Handoff,
    Note,
    Pin,
    QuestionSummary,
    TaskSummary,
)
from waggle.clock import Clock
from waggle.ids import CellId

__all__ = ["SettableSources", "make_assemble_request", "make_verdict"]


@dataclass
class SettableSources:
    """A HotStateSources whose every answer is a field a test sets."""

    tasks: tuple[TaskSummary, ...] = ()
    alarms: tuple[AlarmSummary, ...] = ()
    questions: tuple[QuestionSummary, ...] = ()
    decisions: tuple[DecisionSummary, ...] = ()
    pins_: tuple[Pin, ...] = ()
    notes_: tuple[Note, ...] = ()
    wax_: tuple[CellWaxSummary, ...] = ()
    handoff_: Handoff | None = None

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        """The tasks a test set."""
        return self.tasks

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        """The Alarms a test set."""
        return self.alarms

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        """The Questions a test set."""
        return self.questions

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        """The first `limit` decisions a test set."""
        return self.decisions[:limit]

    async def pins(self) -> tuple[Pin, ...]:
        """The pins a test set."""
        return self.pins_

    async def notes(self) -> tuple[Note, ...]:
        """The notes a test set."""
        return self.notes_

    async def wax(self, cells: frozenset[CellId]) -> tuple[CellWaxSummary, ...]:
        """The wax a test set, about the Cells in `cells` only."""
        return tuple(item for item in self.wax_ if item.cell_id in cells)

    async def handoff(self) -> Handoff | None:
        """The Handoff a test set, or None."""
        return self.handoff_


def make_assemble_request(clock: Clock, **overrides: object) -> AssembleRequest:
    """Build a valid AssembleRequest for a C1 Worker principal, scored at `clock.now()`.

    Args:
        clock: Supplies the reference time relevance is scored against.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated AssembleRequest.
    """
    fields: dict[str, object] = {
        "principal": make_principal(),
        "event": make_trigger_event(),
        "budget": make_token_budget(),
        "now": clock.now(),
    }
    fields.update(overrides)
    return AssembleRequest(**fields)  # type: ignore[arg-type]  # a pydantic model


def make_verdict(action: ScanAction = ScanAction.PASS, **overrides: object) -> ScanVerdict:
    """Build a valid ScanVerdict: PASS with no hash, or a flag with a fixed-looking keyed hash.

    Args:
        action: The verdict's action.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated ScanVerdict.
    """
    flagged = action is not ScanAction.PASS
    fields: dict[str, object] = {
        "action": action,
        "score": 9.0 if flagged else 0.0,
        "families": ("imperative", "role_override") if flagged else (),
        "content_hash": f"{HASH_PREFIX}{'ab' * 32}" if flagged else None,
    }
    fields.update(overrides)
    return ScanVerdict(**fields)  # type: ignore[arg-type]  # a pydantic model
