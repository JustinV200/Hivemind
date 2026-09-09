"""Provide the Warden's own tick handlers: what each WardenAction actually does.

`hivemind.wardens.warden.Warden`'s one tick drains its inbox, orders it with its Attendant, and
dispatches each item through `hivemind.wardens.autopilot.decide` (or, for `NEEDS_JUDGEMENT`,
`hivemind.wardens.awake.decide_awake`) to one `hivemind.wardens.autopilot.WardenAction`. The six
modules here are what each action actually does, split out only so `warden.py` and its `Warden`
class stay within codingrules 5.1's size limits: `assign` (spawn once a `TaskAssign` and its
`GrantIssued` have both arrived), `results` (run acceptance on a sub-bee's claim), `alarms`
(RETRY/REBIND/ESCALATE/CANCEL_TASK), `questions` (forward Question/Answer), `control` (forward
TaskCancel/Pause/Resume/Intervene) and `heartbeat` (send this Warden's own Heartbeat, mirror a
sub-bee's reports, watch for a stall, and build the `HotStateSources` an awake episode reads).
Every function here is one of `Warden`'s own delegates (not general-purpose: they read and write
its private state directly, the same way `hivemind.workers.runtime.attempt.AttemptManager` does
for `WorkerRuntime`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package. Called
    by `hivemind.wardens.warden.Warden`'s own tick dispatch, once per decided WardenAction.

Key invariants:
    - Each module here takes the owning `Warden` as its first argument and reads or writes its
      private state directly; none of them constructs or owns a Warden itself.

See Also:
    - .claude/codingrules.md section 5.1 for the size-limit rule this split exists to satisfy.
    - .claude/roadmap.md phase 3 step 3.19 for the dispatch map these six modules implement.
    - hivemind.wardens.warden for Warden, the one class every module here is a delegate of.

Public API (roadmap step 3.19):
    - alarms, assign, control, heartbeat, questions, results: the six tick-handler modules.
"""

from hivemind.wardens.ticks import alarms, assign, control, heartbeat, questions, results

__all__ = ["alarms", "assign", "control", "heartbeat", "questions", "results"]
