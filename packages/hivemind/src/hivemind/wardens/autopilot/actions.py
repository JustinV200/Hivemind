"""Define WardenAction: the closed set of moves the Warden's autopilot dispatch table can pick.

Codingrules section 8.8: "Autopilot first, awake second... a deterministic dispatch table over
(event kind, state)." `WardenAction` is that table's whole vocabulary: what a Warden's one tick does
about one inbox item, before -- and, for `NEEDS_JUDGEMENT`, instead of -- ever waking a model.
`SPAWN` starts a sub-bee once its `TaskAssign` and matching `GrantIssued` have both arrived;
`ACCEPT` runs a sub-bee's claimed `TaskResult` through acceptance; `RETRY`/`REBIND`/`ESCALATE`/
`CANCEL_TASK` are the four ways an Alarm's `hivemind.supervision.policy.PolicyAction` maps onto
something a Warden actually does; `FORWARD_QUESTION`/`FORWARD_ANSWER`/`FORWARD_CONTROL` relay a
message between the Queen and a sub-bee unchanged; `FORWARD_HONEY` (roadmap step 7.8) relays the
Honey Store's traffic -- a sub-bee's `HoneyQuery` or `NectarDeposit` up, the Queen's
`HoneyResponse` down, and logs her `control.error` about a relayed deposit; `RECORD` notes an item
(a Heartbeat, a `GrantIssued`, a routine `TaskProgress`, or a `CellSnapshotReply`/
`CellRollbackReply` the Warden's own `RelaySnapshotter` resolves) with no further action; `STOP` is
the Queen's own order to end this Warden (a `Shutdown` or a `CellTeardownRequest`), which stops
every sub-bee, releases the lease and ends the tick loop; `RELEASE_LEASE` (roadmap step 5.13) is
the Queen's own narrower order that stops every sub-bee and releases the lease the same way but
leaves this Warden running; `QUARANTINE` (roadmap step 10.6c) quarantines one sub-bee through
`hivemind.wardens.quarantine`, on a Queen-sent `Intervene(QUARANTINE)` or this Warden's own policy
row for a sub-bee's Alarm; `TAINT_MEMORY` (roadmap step 10.6a) runs the isolation setter over this
Warden's own memory store on a Queen-sent `CellTaintOrder` (`hivemind.wardens.isolation`);
`NEEDS_JUDGEMENT` is the one signal that hands the item to `hivemind.wardens.awake` instead.

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
    FORWARD_HONEY = "FORWARD_HONEY"  # HoneyQuery/NectarDeposit up, HoneyResponse down (7.8).
    RECORD = "RECORD"  # Note it (a heartbeat, a grant, routine progress); nothing more to do.
    STOP = "STOP"  # A Shutdown or CellTeardownRequest: stop every sub-bee and end this loop.
    RELEASE_LEASE = "RELEASE_LEASE"  # A Queen-sent Intervene(RELEASE_LEASE): stop every sub-bee,
    # release this Warden's own lease (idempotent), then report LeaseReleased. Roadmap step 5.13.
    QUARANTINE = "QUARANTINE"  # Quarantine one sub-bee (hivemind.wardens.quarantine): a Queen-sent
    # Intervene(QUARANTINE), or this Warden's own policy row for a sub-bee's Alarm. Step 10.6c.
    TAINT_MEMORY = "TAINT_MEMORY"  # A Queen-sent CellTaintOrder: label this Cell's own store
    # tainted, as the isolation's order says (hivemind.wardens.isolation). Roadmap step 10.6a.
    NEEDS_JUDGEMENT = "NEEDS_JUDGEMENT"  # Autopilot has no rule; hand off to wardens.awake.
