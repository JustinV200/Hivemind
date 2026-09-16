"""Provide the Queen's own tick handlers: what each QueenAction actually does.

`hivemind.queen.queen.Queen`'s one tick drains an attached Warden's own link, orders it with her
Attendant, and dispatches each item through `hivemind.queen.autopilot.table.decide` (or, for
`NEEDS_JUDGEMENT`, `hivemind.queen.awake.episode.decide_awake`) to one `hivemind.queen.autopilot.
QueenAction`. The two modules here are what an Alarm and a Warden's own liveness actually do,
split out only so `queen.py` and its `Queen` class stay within codingrules 5.1's size limits:
`alarms` (REBIND/ESCALATE_TO_HUMAN/RETRY_TASK/FAIL_TASK for an escalated Alarm) and `liveness`
(record a Heartbeat, and the MARK_WARDEN_OFFLINE sweep -- roadmap step 4.7 adds the same tick's own
grant renewal and expiry sweep here too, since both ride the identical Heartbeat/liveness cadence).
`hivemind.queen.ticks.results` (COMPLETE_TASK/RETRY_TASK/FAIL_TASK for a TaskResult) lives beside
these two, imported by `alarms` for the two actions it shares. `forage` (roadmap step 4.7) and
`wax` (roadmap step 4.2a) are the tick handlers reached ahead of `hivemind.queen.autopilot.table.
decide`, not through it: a `waggle.messages.forage.ForageRequest` needs its own
within-headroom-or-contested rule (`hivemind.queen.autopilot.forage.decide_forage_request`), and a
`waggle.messages.cell.CellWaxProposed` needs its own within-the-per-Cell-cap rule (`hivemind.queen.
autopilot.wax.decide_wax_proposal`); `decide`'s own fallback for an unrecognised payload
(`NEEDS_JUDGEMENT`) would otherwise reach for every single one of either.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.queen.Queen`'s own tick dispatch, once per decided QueenAction, and (for
    `forage`/`wax`) once per received ForageRequest/CellWaxProposed, ahead of that dispatch.

Key invariants:
    - Each module here takes `deps`/`wardens` explicitly rather than a `Queen` instance: unlike
      `hivemind.wardens.ticks`, these are pure collaborator functions, not delegates reading a
      supervisor's own private state, since the Queen holds far less mutable bookkeeping per tick.

See Also:
    - .claude/codingrules.md section 5.1 for the size-limit rule this split exists to satisfy.
    - .claude/roadmap.md phase 3 step 3.20 for the dispatch map these modules implement; step 4.7
      for `forage` and liveness's own grant renewal and expiry sweep; step 4.2a for `wax`.
    - hivemind.queen.queen for Queen, the one caller of every module here.

Public API (roadmap steps 3.20, 4.7, 4.2a):
    - alarms, liveness, results, forage, wax: the tick-handler modules.
"""

from hivemind.queen.ticks import alarms, forage, liveness, results, wax

__all__ = ["alarms", "forage", "liveness", "results", "wax"]
