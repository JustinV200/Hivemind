"""Define QueenAction: the closed set of moves the Queen's autopilot dispatch table can pick.

Codingrules section 8.8: "Autopilot first, awake second... a deterministic dispatch table over
(event kind, state)." `QueenAction` is that table's whole vocabulary: what the Queen's one tick
does about one inbox item, before -- and, for `NEEDS_JUDGEMENT`, instead of -- ever waking a model.
`RECORD` notes a routine item (a Heartbeat, a stale report) with no further action; `DISPATCH` and
`MARK_WARDEN_OFFLINE` name the two timer-driven housekeeping moves (placing ready tasks, checking
every attached Warden's liveness) so the vocabulary stays complete and independently testable, even
though the live Queen tick (`hivemind.queen.queen`) runs both unconditionally every tick rather than
through a synthetic timer `InboxItem` -- see that module's own docstring for why; `COMPLETE_TASK`/
`RETRY_TASK`/`FAIL_TASK`/`REBIND`/`ESCALATE_TO_HUMAN` are the five ways a `TaskResult` or an
escalated `AlarmRaised` resolves; `BLOCK_ON_QUESTION`/`ROUTE_ANSWER` are the Question/Answer moves;
`NEEDS_JUDGEMENT` is the one signal that hands the item to `hivemind.queen.awake` instead.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    autopilot sub-package (which never imports `hivemind.llm`). Returned by
    `hivemind.queen.autopilot.table.decide`; acted on by `hivemind.queen.queen.Queen`'s tick.
    Calls into nothing beyond the standard library.

Key invariants:
    - This module imports nothing beyond `enum`: `hivemind.queen.autopilot` never imports
      `hivemind.llm`, directly or transitively (codingrules section 4; `lint-imports` enforces it).

See Also:
    - .claude/codingrules.md section 8.8 for the autopilot-first-awake-second shape this enum's
      values are the vocabulary of.
    - hivemind.queen.autopilot.table for decide, the one function that returns these.
    - hivemind.wardens.autopilot.actions for WardenAction, the sibling vocabulary this one mirrors
      in shape (never in exact members: a Warden holds no ESCALATE_TO_HUMAN, and the Queen holds
      no ACCEPT or FORWARD_CONTROL, since the two supervisors act on different things).
"""

from __future__ import annotations

from enum import Enum

__all__ = ["QueenAction"]


class QueenAction(Enum):
    """One tick's deterministic verdict on one inbox item, or a hand-off to awake mode."""

    RECORD = "RECORD"  # Note it (a Heartbeat, a stale/duplicate report); nothing more to do.
    DISPATCH = "DISPATCH"  # Place and grant whatever ready tasks the chamber now holds.
    COMPLETE_TASK = "COMPLETE_TASK"  # TaskResult(SUCCEEDED): chamber.complete, then dispatch.
    RETRY_TASK = "RETRY_TASK"  # Re-dispatch the same task, attempt+1.
    FAIL_TASK = "FAIL_TASK"  # chamber.fail: the task is done, and it did not succeed.
    REBIND = "REBIND"  # Intervene(REBIND) to the Warden: move the task to a stronger binding.
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"  # human_inbox.add_alarm; no fallback binding is left.
    BLOCK_ON_QUESTION = "BLOCK_ON_QUESTION"  # A Warden's Question: chamber.ask, task BLOCKED.
    ROUTE_ANSWER = "ROUTE_ANSWER"  # An Answer reaching the Queen's own inbox directly.
    MARK_WARDEN_OFFLINE = "MARK_WARDEN_OFFLINE"  # A Warden missed too many heartbeats.
    NEEDS_JUDGEMENT = "NEEDS_JUDGEMENT"  # Autopilot has no rule; hand off to queen.awake.
