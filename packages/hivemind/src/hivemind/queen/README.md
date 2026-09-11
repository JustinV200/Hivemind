# hivemind.queen

The queen package is the Queen herself: the Hive's single orchestrator and only global view. It
covers her inbox, Autopilot and Awake modes, planning, placement (Real vs Virtual Cell), the
dispatcher, and the human inbox. She never provisions Cells and never spawns a Worker directly:
every assignment goes to a Warden, over Waggle.

## Public API (roadmap step 3.20)

- `Queen` (`queen.py`): the `waggle.loop.TickLoop` face. `attach_warden(link)` records an
  already-built `WardenLink` before `run()`; the Queen never creates Wardens or Cells herself. One
  tick drains every attached Warden's own link into `InboxItem`s, orders them with her own
  Attendant (`queen.inbox`, human messages heavy but not absolute), dispatches each through
  `queen.autopilot.decide` (falling back to `queen.awake.decide_awake` for `NEEDS_JUDGEMENT`),
  checks every attached Warden's liveness and places whatever the Brood Chamber now says is ready
  -- both unconditionally, every tick, never gated behind an inbox item. Also implements
  `hivemind.supervision.Supervisor` over her attached Wardens. `_recoverable_errors` names
  `InvalidTransitionError`: a chamber transition failing on a stale status backs a tick off and
  records it (`queen.decided`), rather than ending `run()` and taking the whole Hive down.
  `stop()` sets the stop flag first, then reaps every attached Warden's own receive task, so none
  is ever left pending once `run()` ends (codingrules section 11).
- `QueenDeps`, `WardenLink`, `MemoryBudget` (`deps.py`): every collaborator one Queen is built
  with, and one attached Warden's own address and link; manifest *slices* only, never a
  `HiveManifest`.
- `queen.autopilot`: `QueenAction`, `decide`, `effort_for` -- the deterministic dispatch table;
  never imports `hivemind.llm`.
- `queen.awake`: `QueenDecision`, `QueenSources`, `decide_awake` -- one stateless episode on
  `ModelSlot.QUEEN`, at the `Effort` autopilot chose for the event class.
- `queen.planner`: `PlanSchema`, `PlannedTask`, `PlannedPostcondition`, `plan_goal` -- decomposing
  a goal into a validated `hivemind.brood_chamber.TaskGraphDraft`; every subtask carries at least
  one acceptance postcondition (roadmap step 3.18).
- `queen.placement`: `Placement`, `PlacementError`, `decide` -- the pure v0 decision (the Hive
  Stand only).
- `dispatch_ready` (`dispatcher.py`): place, grant and assign every ready task, never to a Worker
  directly; a fresh task's own `chamber.assign`/`chamber.start` and `queen.assigned` land before
  either wire message is sent, so a fast sub-bee's own immediate Question can never reach
  `Queen._act` while the chamber still reads ASSIGNED.
- `handle_question`, `answer_question`, `sync_answers_from_chamber` (`questions.py`): the Queen's
  own question traffic; see that module's own docstring for why `answer_question` exists (the
  Brood Chamber's public API has no way to read an already-answered Question's content back out).
  `sync_answers_from_chamber` is the one place an Answer routed through `hive inbox answer` is
  forwarded and its own tracking dropped -- called by both the Queen's own tick and `hive run`'s
  poll loop, so a tick landing between `hive inbox answer`'s own two separate writes (`chamber.
  answer()`, then the answer Note) retries instead of losing the answer for good.
- `HumanInbox` (`human_inbox.py`): pending questions (read through the chamber) and Alarms (held
  in memory) awaiting the human.
- `record_event` (`trail.py`): the one place a `queen.*` trail event is built.
- `queen.inbox`: `queen_attendant`, `to_inbox_item`, `ModelTieBreaker` -- the Queen's own
  Attendant, with an optional model-backed tie-breaker on `ModelSlot.ATTENDANT`.
- `queen.ticks`: `alarms`, `liveness`, `results` -- the tick handlers each `QueenAction` calls
  into, split out only to stay within codingrules 5.1's size limits. `alarms.handle_alarm` records
  `alarm.handled`/`alarm.escalated` (`hivemind.supervision.record_alarm_event`) and, for a REBIND,
  fills `Intervene.binding` with the fallback key it resolved, since a Warden has no other way to
  learn it; `Queen`'s own COMPLETE_TASK handling records `alarm.resolved` once a rebound or
  retried attempt actually succeeds.
- `queen.cluster`, `queen.forage`, `queen.requeening`, `queen.supersedure`: placeholders,
  populated in a later roadmap phase.

## How to test this

Every module is tested against fakes: `hivemind.brood_chamber.MemoryTaskStore`-backed
`BroodChamber`, `hivemind.memory.InMemoryMemoryStore`, `hivemind.pheromone.trail.memory.
MemoryPheromoneTrail`, `waggle.clock.FakeClock`, the shipped `docs/supervision/default-policy.
toml`, a scriptable `hivemind.llm.FakeLLMProvider` on `ModelSlot.QUEEN` and `ModelSlot.ATTENDANT`,
and `waggle.transport.memory.MemoryTransport.pair`. `tests/builders/queen.py`'s
`make_queen_deps` builds all of that in one call; its `WardenEnd` wraps the Warden side of one
attached link the same way `tests/builders/wardens.py`'s `QueenEnd` wraps the Queen side of a
Warden's own link, so a test drives a real `Queen` end to end over real (in-memory) Waggle links,
never by calling its private methods directly.

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen -q
```
