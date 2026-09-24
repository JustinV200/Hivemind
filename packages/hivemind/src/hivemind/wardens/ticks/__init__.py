"""Provide the Warden's own tick handlers: what each WardenAction actually does.

`hivemind.wardens.warden.Warden`'s one tick drains its inbox, orders it with its Attendant, and
decides each item through `hivemind.wardens.autopilot.decide` (or, for `NEEDS_JUDGEMENT`,
`hivemind.wardens.awake.decide_awake`) to one `hivemind.wardens.autopilot.WardenAction`, which
`dispatch.act` hands to the module here that does it. The modules are split out only so
`warden.py` and its `Warden` class stay within codingrules 5.1's size limits: `assign` (spawn once
a `TaskAssign` and its `GrantIssued` have both arrived), `results` (run acceptance on a sub-bee's
claim), `alarms` (RETRY/REBIND/ESCALATE/CANCEL_TASK), `questions` (forward Question/Answer),
`control` (forward TaskCancel/Pause/Resume/Intervene), `honey` (relay the Honey Store's traffic --
queries and Nectar deposits up, responses down -- roadmap step 7.8), `heartbeat` (send this
Warden's own Heartbeat, mirror a sub-bee's reports, watch for a stall, and build the
`HotStateSources` an awake episode reads) and `trail_ship` (ship the trail before a result).
Every function here is one of `Warden`'s own delegates (not general-purpose: they read and write
its private state directly, the same way `hivemind.workers.runtime.attempt.AttemptManager` does
for `WorkerRuntime`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package. Called
    by `hivemind.wardens.warden.Warden`'s own tick, once per decided WardenAction, through
    `dispatch.act`.

Key invariants:
    - Each module here takes the owning `Warden` as its first argument and reads or writes its
      private state directly; none of them constructs or owns a Warden itself.

See Also:
    - .claude/codingrules.md section 5.1 for the size-limit rule this split exists to satisfy.
    - .claude/roadmap.md phase 3 step 3.19 for the dispatch map these modules implement, and
      phase 7 step 7.8 for the Honey relay.
    - hivemind.wardens.warden for Warden, the one class every module here is a delegate of.

Public API (roadmap steps 3.19, 7.8):
    - alarms, assign, control, dispatch, heartbeat, honey, questions, results: the tick-handler
      modules; `dispatch.act` is the one entry point the Warden's tick calls.
"""

from hivemind.wardens.ticks import (
    alarms,
    assign,
    control,
    dispatch,
    heartbeat,
    honey,
    questions,
    results,
)

__all__ = [
    "alarms",
    "assign",
    "control",
    "dispatch",
    "heartbeat",
    "honey",
    "questions",
    "results",
]
