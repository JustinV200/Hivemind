"""Define WardenAction: the closed set of moves the Warden's autopilot dispatch table can pick.

Codingrules section 8.8: "Autopilot first, awake second... a deterministic dispatch table over
(event kind, state)." `WardenAction` is that table's whole vocabulary: what a Warden's one tick does
about one inbox item, before -- and, for `NEEDS_JUDGEMENT`, instead of -- ever waking a model.
`SPAWN` starts a sub-bee once its `TaskAssign` and matching `GrantIssued` have both arrived;
`ACCEPT` runs a sub-bee's claimed `TaskResult` through acceptance; `RETRY`/`REBIND`/`ESCALATE`/
`CANCEL_TASK` are the four ways an Alarm's `hivemind.supervision.policy.PolicyAction` maps onto
something a Warden actually does; `FORWARD_QUESTION`/`FORWARD_ANSWER`/`FORWARD_CONTROL` relay a
message between the Queen and a sub-bee unchanged; `RECORD` notes an item (a Heartbeat, a
`GrantIssued`, a routine `TaskProgress`) with no further action; `NEEDS_JUDGEMENT` is the one
signal that hands the item to `hivemind.wardens.awake` instead.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    autopilot sub-package (which never imports `hivemind.llm`). Returned by
    `hivemind.wardens.autopilot.table.decide`; acted on by `hivemind.wardens.warden.Warden`'s tick
    and its `hivemind.wardens.ticks` handlers. Calls into nothing beyond the standard library.

Key invariants:
    - This module imports nothing beyond `enum`: `hivemind.wardens.autopilot` never imports
      `hivemind.llm`, directly or transitively (codingrules section 4; `lint-imports` enforces it).

See Also:
    - .claude/codingrules.md section 8.8 for the autopilot-first-awake-second shape this enum's
      values are the vocabulary of.
    - hivemind.wardens.autopilot.table for decide, the one function that returns these.
    - hivemind.supervision.policy for PolicyAction, the Alarm-specific vocabulary this one maps.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["WardenAction"]


class WardenAction(Enum):
    """One tick's deterministic verdict on one inbox item, or a hand-off to awake mode."""

    SPAWN = "SPAWN"  # A TaskAssign has a matching GrantIssued; start the sub-bee.
    ACCEPT = "ACCEPT"  # A sub-bee claimed TaskResult(CLAIMED); run acceptance on it.
    RETRY = "RETRY"  # Respawn the same binding, attempt+1, from the last Handoff if any.
    REBIND = "REBIND"  # Respawn on a stronger binding within the grant's allowed_bindings.
    ESCALATE = "ESCALATE"  # Forward the Alarm to the Queen, attempts incremented.
    CANCEL_TASK = "CANCEL_TASK"  # Stop the task for good; report TaskResult(FAILED).
    FORWARD_QUESTION = "FORWARD_QUESTION"  # A sub-bee's Question, relayed to the Queen.
    FORWARD_ANSWER = "FORWARD_ANSWER"  # The Queen's Answer, relayed to the asking sub-bee.
    FORWARD_CONTROL = "FORWARD_CONTROL"  # TaskCancel/Pause/Resume/Intervene, relayed unchanged.
    RECORD = "RECORD"  # Note it (a heartbeat, a grant, routine progress); nothing more to do.
    NEEDS_JUDGEMENT = "NEEDS_JUDGEMENT"  # Autopilot has no rule; hand off to wardens.awake.
