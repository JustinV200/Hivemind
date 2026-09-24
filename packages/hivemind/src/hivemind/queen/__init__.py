"""Represent the Queen herself: the Hive's single orchestrator and only global view.

It covers her inbox, Autopilot (deterministic fallback behaviour that never awaits a model) and
Awake (a bounded, stateless episode where she may think with a model) modes, planning, placement
(Real vs Virtual Cell), the dispatcher, and the human inbox. Roadmap step 3.20 builds the Queen
kernel proper: `queen.py` (`Queen`, the `waggle.loop.TickLoop` face, also a `hivemind.supervision.
Supervisor` over her attached Wardens); `deps.py` (`QueenDeps`, `WardenLink`, `MemoryBudget`); one
tick order every Warden link into `hivemind.supervision.attendant.InboxItem`s (`inbox/`), decides
deterministically first (`autopilot/`, never imports `hivemind.llm`) and, for `NEEDS_JUDGEMENT`,
through one stateless episode (`awake/`); `planner/` turns a goal into a validated task graph,
`goal_submission.py` plans one goal end to end (`Queen.submit_goal`'s own body, split out so
`queen.py` stays within its file-size cap), `placement/` picks a Cell for a ready task, and
`dispatcher.py` places, grants and assigns it; `questions.py` and `human_inbox.py` are the
blocking-question and human-Alarm traffic. `cluster/`, `forage/`, `requeening/` and `supersedure/`
stay the placeholders a later roadmap phase populates.

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
    - QueenError, UnknownWardenError, WardenSpawnRefusedError: this subsystem's error tree
      (errors); the last is the Guard refusing the Queen `warden:spawn` (roadmap step 10.3).
    - HumanInbox: pending questions and Alarms awaiting the human (human_inbox).
    - answer_question, handle_question: the Queen's own question traffic (questions).
      ANSWER_NOTE_AUTHOR_PREFIX, answer_note_author and sync_answers_from_chamber: the one
      retry-safe forwarding sweep both the Queen's own tick and a running `hive run`'s poll loop
      call (roadmap step 3.21 second half; unified across both callers by this dispatch's own
      fix 3) (questions).
    - dispatch_ready: place, grant and assign every ready task (dispatcher).
    - submit_goal: plan a goal, persist the graph and dispatch what's ready (goal_submission,
      roadmap step 5.0b); `Queen.submit_goal`'s own body, pulled out so queen.py (pinned at the
      codingrules 5.1 file cap) never grows for a new PlanBrief field.
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
    - ForageLedger: the Queen's live book of Forage, roadmap step 4.7 (forage.ledger); reached at
      `hivemind.queen.forage.grants`/`.requests` for the grant-lease and ForageRequest logic
      themselves, the same module-access convention `ticks.alarms`/`ticks.liveness` already use.
    - QueenMode, ClusterState, InvalidQueenModeTransitionError: the Queen's own mode machine and
      the small state (mode plus clustered-provider set) tracked on a `Queen` instance for
      Clustering (state).
    - cluster, resume, ClusterOutcome, ResumeOutcome: pause and resume one provider's bees
      (roadmap step 4.9, docs/adr/0024); HealthPoller, ClusterBackoff, next_probe_at:
      backoff-scheduled provider health polling; ClusterOrder, OrderKind, OrderStore,
      InMemoryOrderStore, SqliteOrderStore, apply_order_migrations, new_order_id: the durable
      `hive cluster`/`hive wake` rows the running Queen polls; run_cluster_tick, awake_available:
      the two hooks a running Queen's tick wires in; check_cost_caps: the cost-cap trigger
      (cluster).
    - resume_paused: the dispatcher-path entry point a Clustering resume goes through so no work
      is redone (dispatcher).
    - Roadmap step 10.5 (ADR-0032), the human end: GoalRequest, GoalRequestState, GoalSource,
      GoalRequestStore, GoalRequestQuery, InMemoryGoalRequestStore, SqliteGoalRequestStore (the
      durable goal requests `Queen.request_goal` commits and her own tick plans; intake);
      ChatEntry, ChatAuthor, ChatKind, ChatLog, ChatQuery, InMemoryChatLog, SqliteChatLog (the
      chat log, the human end of her inbox), HumanChannel, NullHumanChannel (the seam that tells
      the human's devices something is waiting) and ChatDoor (the human-facing methods `Queen`
      inherits: request_goal, confirm_goal_request, decline_goal_request, post_human_message,
      acknowledge_alarm) (chat); GoalTerms, plan_goal_graph: what a goal is planned under, and the
      plan-and-persist half of submit_goal (goal_submission); PlanningLane: her one in-flight
      plan (deps).
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
from hivemind.queen.chat import (
    ChatAuthor,
    ChatDoor,
    ChatEntry,
    ChatKind,
    ChatLog,
    ChatQuery,
    HumanChannel,
    InMemoryChatLog,
    NullHumanChannel,
    SqliteChatLog,
)
from hivemind.queen.cluster import (
    ClusterBackoff,
    ClusterOrder,
    ClusterOutcome,
    HealthPoller,
    InMemoryOrderStore,
    OrderKind,
    OrderStore,
    ResumeOutcome,
    SqliteOrderStore,
    apply_order_migrations,
    awake_available,
    check_cost_caps,
    cluster,
    new_order_id,
    next_probe_at,
    resume,
    run_cluster_tick,
)
from hivemind.queen.deps import MemoryBudget, PlanningLane, QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready, resume_paused
from hivemind.queen.errors import QueenError, UnknownWardenError, WardenSpawnRefusedError
from hivemind.queen.forage import ForageLedger
from hivemind.queen.goal_submission import GoalTerms, plan_goal_graph, submit_goal
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.inbox import (
    MAX_TIE_REASON_CHARS,
    ModelTieBreaker,
    queen_attendant,
    to_inbox_item,
)
from hivemind.queen.intake import (
    GoalRequest,
    GoalRequestQuery,
    GoalRequestState,
    GoalRequestStore,
    GoalSource,
    InMemoryGoalRequestStore,
    SqliteGoalRequestStore,
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
    sync_answers_from_chamber,
)
from hivemind.queen.state import ClusterState, InvalidQueenModeTransitionError, QueenMode
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
    "ChatAuthor",
    "ChatDoor",
    "ChatEntry",
    "ChatKind",
    "ChatLog",
    "ChatQuery",
    "ClusterBackoff",
    "ClusterOrder",
    "ClusterOutcome",
    "ClusterState",
    "ForageLedger",
    "GoalRequest",
    "GoalRequestQuery",
    "GoalRequestState",
    "GoalRequestStore",
    "GoalSource",
    "GoalTerms",
    "HealthPoller",
    "HumanChannel",
    "HumanInbox",
    "InMemoryChatLog",
    "InMemoryGoalRequestStore",
    "InMemoryOrderStore",
    "InvalidQueenModeTransitionError",
    "MemoryBudget",
    "ModelTieBreaker",
    "NullHumanChannel",
    "OrderKind",
    "OrderStore",
    "Placement",
    "PlacementError",
    "PlanSchema",
    "PlannedPostcondition",
    "PlannedTask",
    "PlannerError",
    "PlanningLane",
    "Queen",
    "QueenAction",
    "QueenDecision",
    "QueenDeps",
    "QueenError",
    "QueenMode",
    "QueenSources",
    "ResumeOutcome",
    "SqliteChatLog",
    "SqliteGoalRequestStore",
    "SqliteOrderStore",
    "UnknownWardenError",
    "WardenLink",
    "WardenLiveness",
    "WardenSpawnRefusedError",
    "answer_note_author",
    "answer_question",
    "apply_order_migrations",
    "awake_available",
    "check_cost_caps",
    "cluster",
    "decide",
    "decide_awake",
    "dispatch_ready",
    "effort_for",
    "handle_question",
    "new_order_id",
    "next_probe_at",
    "plan_goal",
    "plan_goal_graph",
    "queen_attendant",
    "record_event",
    "resume",
    "resume_paused",
    "run_cluster_tick",
    "submit_goal",
    "sync_answers_from_chamber",
    "to_inbox_item",
]
