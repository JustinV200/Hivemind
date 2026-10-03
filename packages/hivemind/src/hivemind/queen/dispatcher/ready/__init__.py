"""Place, grant and (re-)assign every task the Queen wants run: the dispatcher's ready path.

Roadmap step 3.20's own dispatch map, grown over phases 4 to 10 into two halves that each need a
file: `dispatch` decides where a ready task runs and when (placement, the waits a zero grant or a
goal's allowance impose, a Virtual Cell acquired beside the tick, the tiers' floors), and `assign`
checks, books and sends the grant and the `TaskAssign` once a task has its Cell and grant (the
zero-grant refusal, the `grant_issue` point, the recon and Honey an assignment carries).
`dispatch_ready`, `redispatch` and `resume_paused` are the three ways in; all of them end in
`assign.send_grant_and_assign`, the one choke point a grant and an assignment pass.
`requeue.requeue_for_commands` (ADR-0060) puts a task whose Cell refused its commands back in
front of `dispatch_ready`, needing a Cell that runs them, and `unfinished.requeue_unfinished` puts
back every task a stopping Hive leaves on its own Cells, for the next start to place afresh.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.dispatcher`. Called
    by `hivemind.queen.queen.Queen` (every tick, after `submit_goal`, after a finished task),
    `hivemind.queen.ticks.results` and `.alarms` (a retry, a REBIND or a re-placement for
    commands), `hivemind.queen.cluster.protocol` (a resume) and, through `hivemind.queen`, the
    CLI's `run_hive` as it stops (the unfinished tasks). Calls into this package's `dispatch`,
    `requeue` and `unfinished` only.

Key invariants:
    - `GrantIssued` is always sent before `TaskAssign`, on the same Warden link, for the same task.
    - Nothing reaches a Warden for a fresh dispatch before the chamber reads RUNNING.

See Also:
    - hivemind.queen.dispatcher.ready.dispatch for the three entry points and placement.
    - hivemind.queen.dispatcher.ready.assign for the grant and the TaskAssign.
    - hivemind.queen.dispatcher.ready.requeue for a re-placement on a Cell that runs commands.
    - hivemind.queen.dispatcher.ready.unfinished for what a stopping Hive gives back.

Public API:
    - dispatch_ready, redispatch, resume_paused: the three ways a task is (re-)assigned.
    - requeue_for_commands: a task whose Cell refused its commands, placed again (ADR-0060).
    - requeue_unfinished: every task a stopping Hive leaves on its Cells, PENDING again.
"""

from hivemind.queen.dispatcher.ready.dispatch import dispatch_ready, redispatch, resume_paused
from hivemind.queen.dispatcher.ready.requeue import requeue_for_commands
from hivemind.queen.dispatcher.ready.unfinished import requeue_unfinished

__all__ = [
    "dispatch_ready",
    "redispatch",
    "requeue_for_commands",
    "requeue_unfinished",
    "resume_paused",
]
