"""Represent the Queen herself: the Hive's single orchestrator and only global view.

It covers her inbox, Autopilot (deterministic fallback behaviour that never awaits a model) and
Awake (a bounded, stateless episode where she may think with a model) modes, planning, placement
(Real vs Virtual Cell), the dispatcher, and the human inbox. Roadmap step 3.20 builds the Queen
kernel proper: `queen.py` (`Queen`, the `waggle.loop.TickLoop` face, also a `hivemind.supervision.
Supervisor` over her attached Wardens); `deps.py` (`QueenDeps`, `WardenLink`, `MemoryBudget`); one
tick order every Warden link into `hivemind.supervision.attendant.InboxItem`s (`inbox/`), decides
deterministically first (`autopilot/`, never imports `hivemind.llm`) and, for `NEEDS_JUDGEMENT`,
through one stateless episode (`awake/`); `planner/` turns a goal into a validated task graph,
`placement/` picks a Cell for a ready task, and `dispatcher.py` places, grants and assigns it;
`questions.py` and `human_inbox.py` are the blocking-question and human-Alarm traffic. `cluster/`,
`forage/`, `requeening/` and `supersedure/` stay the placeholders a later roadmap phase populates.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Called by entrance, observation
    and cli (Layer 7). Calls into wardens (Layer 5) and everything below it; never workers
    directly (she assigns to a Warden, never a Worker).

Key invariants:
    - The Queen holds no `hivemind.cell.CellSession` and no Comb Registry, anywhere in her own
      instance attributes or `QueenDeps`'s own fields (docs/adr/0019; a test introspects both).
    - `hivemind.queen.autopilot` never imports `hivemind.llm`, directly or transitively
      (codingrules section 4; `lint-imports` enforces it).
    - `QueenDeps` carries manifest *slices* only, never a `HiveManifest` (codingrules section 13).

See Also:
    - .claude/codingrules.md section 4 for the layer 6 row this package occupies.
    - .claude/codingrules.md section 8.8 for the kernel/Attendant/autopilot/awake shape this
      package implements.
    - docs/adr/0019-queen-kernel-autopilot-first-with-stateless-awake-episodes.md for the decision
      this package's own shape follows.
    - .claude/roadmap.md phase 3 step 3.20 for the work that first populates it.
    - hivemind.queen.README for the module-by-module map of this package.

Public API (roadmap step 3.20):
    - Queen: the TickLoop face, also a Supervisor over her attached Wardens (queen).
    - MemoryBudget, QueenDeps, WardenLink: every collaborator the Queen is built with (deps).
    - QueenError, UnknownWardenError: this subsystem's error tree (errors).
    - HumanInbox: pending questions and Alarms awaiting the human (human_inbox).
    - answer_question, handle_question, route_answers: the Queen's own question traffic
      (questions). Roadmap step 3.21 (second half) adds ANSWER_NOTE_AUTHOR_PREFIX,
      answer_note_author and sync_answers_from_chamber: the cross-process handoff `hive inbox
      answer` and a running `hive run` share (questions).
    - dispatch_ready: place, grant and assign every ready task (dispatcher).
    - record_event: the one place a queen.* trail event is built (trail).
    - MAX_TIE_REASON_CHARS, ModelTieBreaker, queen_attendant, to_inbox_item: her Attendant (inbox).
    - QueenAction, decide, effort_for: the deterministic dispatch table (autopilot).
    - MAX_BINDING_CHARS, MAX_REASON_CHARS, MAX_TASK_ID_CHARS, QueenDecision, ACTIVE_TASKS_LIMIT,
      AWAKE_MAX_OUTPUT_TOKENS, AWAKE_OUTPUT_RESERVE_TOKENS, NOTES_LIMIT, RECENT_DECISIONS_LIMIT,
      QueenSources, decide_awake: one stateless awake episode (awake).
    - MAX_ACCEPTANCE_ITEMS, MAX_DEPENDS_ON, MAX_KEY_CHARS, MAX_OBJECTIVE_CHARS, MAX_PLAN_TASKS,
      MAX_TITLE_CHARS, MIN_ACCEPTANCE_ITEMS, MIN_PLAN_TASKS, PLANNER_MAX_OUTPUT_TOKENS, PlanSchema,
      PlannedPostcondition, PlannedTask, PlannerError, plan_goal: decomposing a goal (planner).
    - Placement, PlacementError: the pure Real-vs-Virtual-Cell decision (placement); the decision
      function itself is reached as `hivemind.queen.placement.decide`, not re-exported here, since
      the name would collide with `hivemind.queen.autopilot.decide` above.
    - WardenLiveness: one attached Warden's own pulse, as `Queen.liveness` reports it (ticks).
"""

from hivemind.queen.autopilot import QueenAction, decide, effort_for
from hivemind.queen.awake import (
    ACTIVE_TASKS_LIMIT,
    AWAKE_MAX_OUTPUT_TOKENS,
    AWAKE_OUTPUT_RESERVE_TOKENS,
    MAX_BINDING_CHARS,
    MAX_REASON_CHARS,
    MAX_TASK_ID_CHARS,
    NOTES_LIMIT,
    RECENT_DECISIONS_LIMIT,
    QueenDecision,
    QueenSources,
    decide_awake,
)
from hivemind.queen.deps import MemoryBudget, QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.errors import QueenError, UnknownWardenError
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.inbox import (
    MAX_TIE_REASON_CHARS,
    ModelTieBreaker,
    queen_attendant,
    to_inbox_item,
)
from hivemind.queen.placement import Placement, PlacementError
from hivemind.queen.planner import (
    MAX_ACCEPTANCE_ITEMS,
    MAX_DEPENDS_ON,
    MAX_KEY_CHARS,
    MAX_OBJECTIVE_CHARS,
    MAX_PLAN_TASKS,
    MAX_TITLE_CHARS,
    MIN_ACCEPTANCE_ITEMS,
    MIN_PLAN_TASKS,
    PLANNER_MAX_OUTPUT_TOKENS,
    PlannedPostcondition,
    PlannedTask,
    PlannerError,
    PlanSchema,
    plan_goal,
)
from hivemind.queen.queen import Queen
from hivemind.queen.questions import (
    ANSWER_NOTE_AUTHOR_PREFIX,
    answer_note_author,
    answer_question,
    handle_question,
    route_answers,
    sync_answers_from_chamber,
)
from hivemind.queen.ticks.liveness import WardenLiveness
from hivemind.queen.trail import record_event

__all__ = [
    "ACTIVE_TASKS_LIMIT",
    "ANSWER_NOTE_AUTHOR_PREFIX",
    "AWAKE_MAX_OUTPUT_TOKENS",
    "AWAKE_OUTPUT_RESERVE_TOKENS",
    "MAX_ACCEPTANCE_ITEMS",
    "MAX_BINDING_CHARS",
    "MAX_DEPENDS_ON",
    "MAX_KEY_CHARS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_PLAN_TASKS",
    "MAX_REASON_CHARS",
    "MAX_TASK_ID_CHARS",
    "MAX_TIE_REASON_CHARS",
    "MAX_TITLE_CHARS",
    "MIN_ACCEPTANCE_ITEMS",
    "MIN_PLAN_TASKS",
    "NOTES_LIMIT",
    "PLANNER_MAX_OUTPUT_TOKENS",
    "RECENT_DECISIONS_LIMIT",
    "HumanInbox",
    "MemoryBudget",
    "ModelTieBreaker",
    "Placement",
    "PlacementError",
    "PlanSchema",
    "PlannedPostcondition",
    "PlannedTask",
    "PlannerError",
    "Queen",
    "QueenAction",
    "QueenDecision",
    "QueenDeps",
    "QueenError",
    "QueenSources",
    "UnknownWardenError",
    "WardenLink",
    "WardenLiveness",
    "answer_note_author",
    "answer_question",
    "decide",
    "decide_awake",
    "dispatch_ready",
    "effort_for",
    "handle_question",
    "plan_goal",
    "queen_attendant",
    "record_event",
    "route_answers",
    "sync_answers_from_chamber",
    "to_inbox_item",
]
