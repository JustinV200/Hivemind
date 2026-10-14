"""Provide Watch mode: observe an idle Cell read-only, and review what was seen on a schedule.

A Warden with no active Workers does not sit idle (ADR-0054). Every `[supervision.watch]
interval_s` it takes one observation of its Cell through a `WatchProbe` (the process list,
resource use, and logs and file changes under the operator's roots; never the screen, never input)
and keeps it as `C2` Bee Bread for `retention_s`. Every `patrol_interval_s` it runs a Patrol: one
stateless awake episode over what it kept, helped by a fixed pre-filter (`signals`), that decides
exactly one of four things: an Alarm, a Nectar summary, a Cell Wax proposal, or nothing worth
keeping (the observations it covered are then discarded). The package is split along what each
part may touch. `probe/` looks (a session, or a packet through `hivemind.swarm`), `observer`
takes and stores a sample, `signals` and `digest` are pure, `patrol` drives a review through a
`PatrolPort`, `bridge` is that port for a real Warden, and `tick` is the one call the Warden makes.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles Watch mode: observing an idle Cell and reviewing it on a schedule instead of sitting
    idle. Called by `hivemind.wardens.warden.Warden`'s tick (`tick.watch_tick`) and built by the
    composition roots (`hivemind.cli.compose`); calls into sibling packages at Layer 5 or below.

Key invariants:
    - Watching never writes to the Cell, holds no lease of its own and never captures the screen
      or the input; what a Cell's access level permits comes from `hivemind.guard.watch`.
    - Everything observed is `C2`, short-lived, never Honey, and never a trail payload.
    - This face imports no Warden and no model machinery, because `hivemind.wardens.deps` imports
      it: `observer`, `patrol`, `bridge` and `tick` are imported by their module paths.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the design.
    - .claude/codingrules.md section 3 for where this sub-package sits under wardens.
    - .claude/roadmap.md phase 11 step 11.10 for the work that populates it.

Public API (roadmap step 11.10):
    - PatrolChoice, PatrolDecision, PatrolOutcome, PatrolWaxSeverity: the one decision a Patrol
      makes and how a Patrol ends (decision).
    - WatchDeps, WatchSchedule, WatchSettings: what a Warden is built with to watch (deps).
    - PROBE_FAILURES, SessionWatchProbe, WatchProbe: how it looks (probe).
"""

from hivemind.wardens.watch.decision import (
    PatrolChoice,
    PatrolDecision,
    PatrolOutcome,
    PatrolWaxSeverity,
)
from hivemind.wardens.watch.deps import WatchDeps, WatchSchedule, WatchSettings
from hivemind.wardens.watch.probe import PROBE_FAILURES, SessionWatchProbe, WatchProbe

__all__ = [
    "PROBE_FAILURES",
    "PatrolChoice",
    "PatrolDecision",
    "PatrolOutcome",
    "PatrolWaxSeverity",
    "SessionWatchProbe",
    "WatchDeps",
    "WatchProbe",
    "WatchSchedule",
    "WatchSettings",
]
