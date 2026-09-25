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
`WRITE_WAX`/`REJECT_WAX`/`CLEAR_WAX` (roadmap step 4.2a) are the three ways a Cell Wax note's own
life resolves -- reached by `hivemind.queen.autopilot.wax.decide_wax_proposal` for `WRITE_WAX`
alone (a Warden's NOTE/CAUTION about its own Cell, within the per-Cell cap), and by
`hivemind.queen.awake.decision.QueenDecision.action` for all three, since an awake episode judging
a BLOCK, a Worker's proposal, a proposal about another Cell, or a clear may pick any of them;
`GRANT_BY_SHRINKING`/`DENY_REQUEST` (roadmap step 4.7's leftover) are the two ways a contested
`ForageRequest` resolves once `hivemind.queen.awake` judges it: shrink another live grant (named
by `QueenDecision.shrink_grant_id`/`.shrink_amount`) to free the headroom the request needs, or
deny it with a reason; `hivemind.queen.ticks.forage` is the one place either is acted on.
`REPLY` (roadmap step 10.5, ADR-0040) is the Queen answering the human in the chat, with the words
in `QueenDecision.message`: only ever an awake decision (autopilot has no rule for free text), and
`hivemind.queen.ticks.human.chat.reply` is the one place it is acted on. `QUARANTINE_BEE` (roadmap
step 10.6c, the row a `PolicyAction.QUARANTINE` maps to) orders the Warden of an Alarm's task to
quarantine the bee it names (`hivemind.queen.quarantine.order`), and `PAUSE_TASK` holds a task
PAUSED in the Brood Chamber once its Warden reports it held (`hivemind.queen.quarantine.hold`).
`ISOLATE_CELL` (roadmap step 10.6a, ADR-0043, the row a `PolicyAction.ISOLATE` maps to) isolates
one Cell through the one isolation path (`hivemind.queen.isolation`), and `DISMISS` settles a Guard
request that needs nothing done (the report stays on the trail); with `QUARANTINE_BEE` they are
the three ways her decision on a Guard request resolves (`hivemind.queen.guard_requests`).
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
    WRITE_WAX = "WRITE_WAX"  # cell_wax.write_wax: PROPOSED -> WRITTEN, by autopilot or awake.
    REJECT_WAX = "REJECT_WAX"  # cell_wax.reject_wax: PROPOSED -> REJECTED, an awake decision.
    CLEAR_WAX = "CLEAR_WAX"  # cell_wax.clear_wax: WRITTEN -> CLEARED, an awake decision.
    GRANT_BY_SHRINKING = "GRANT_BY_SHRINKING"  # Shrink QueenDecision.shrink_grant_id, then grant.
    DENY_REQUEST = "DENY_REQUEST"  # A contested ForageRequest denied, with QueenDecision.reason.
    REPLY = "REPLY"  # Answer the human in the chat with QueenDecision.message; awake only.
    QUARANTINE_BEE = "QUARANTINE_BEE"  # Intervene(QUARANTINE) to the Warden of the Alarm's task.
    PAUSE_TASK = "PAUSE_TASK"  # chamber.pause: a Warden reported its task held (a quarantine).
    ISOLATE_CELL = "ISOLATE_CELL"  # The one isolation path: cut one Cell off, keep its evidence.
    DISMISS = "DISMISS"  # A Guard request judged to need nothing: the report stays on the trail.
    NEEDS_JUDGEMENT = "NEEDS_JUDGEMENT"  # Autopilot has no rule; hand off to queen.awake.
