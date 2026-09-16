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
- `queen.autopilot`: `QueenAction` (now including `WRITE_WAX`/`REJECT_WAX`/`CLEAR_WAX`, roadmap
  step 4.2a), `decide`, `effort_for`, `decide_forage_request`, `decide_wax_proposal` -- the
  deterministic dispatch tables; never imports `hivemind.llm`.
- `queen.awake`: `QueenDecision`, `QueenSources` (its `wax(cells)` returns WRITTEN Cell Wax
  capped per Cell, `cap_wax_for_hot_state`), `decide_awake` (now takes an optional
  `cells_in_play`, roadmap step 4.2a) -- one stateless episode on `ModelSlot.QUEEN`, at the
  `Effort` autopilot chose for the event class.
- `queen.planner`: `PlanSchema`, `PlannedTask`, `PlannedPostcondition`, `plan_goal` -- decomposing
  a goal into a validated `hivemind.brood_chamber.TaskGraphDraft`; every subtask carries at least
  one acceptance postcondition (roadmap step 3.18).
- `queen.placement`: `Placement`, `PlacementError`, `decide` -- the pure v0 decision (the Hive
  Stand only); roadmap step 4.2a adds `blocked_cells`/`cautioned_cells` keyword args, both
  optional and empty by default: a candidate carrying a WRITTEN `BLOCK` Cell Wax note is excluded
  outright, one carrying a WRITTEN `CAUTION` is kept but ranked behind every clean candidate.
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
- `HumanInbox`, `ChatWaxProposal`, `propose_wax_from_chat` (`human_inbox.py`): pending questions
  (read through the chamber) and Alarms (held in memory) awaiting the human; `propose_wax_from_chat`
  (roadmap step 4.2a) is the smallest hook for a human-typed Cell Wax proposal, building the same
  wire `CellWaxProposed` shape (`origin=HUMAN`, `proposer=None`) `queen.ticks.wax` judges either way.
- `record_event` (`trail.py`): the one place a `queen.*` trail event is built.
- `queen.inbox`: `queen_attendant`, `to_inbox_item`, `ModelTieBreaker` -- the Queen's own
  Attendant, with an optional model-backed tie-breaker on `ModelSlot.ATTENDANT`. A
  `CellWaxProposed` classifies as a routine `WAGGLE_MESSAGE` (roadmap step 4.2a: "scores low"),
  never its own `InboxKind`.
- `queen.ticks`: `alarms`, `liveness`, `results`, `forage`, `wax` -- the tick handlers each
  `QueenAction` (or, for `forage`/`wax`, each received `ForageRequest`/`CellWaxProposed`) calls
  into, split out only to stay within codingrules 5.1's size limits. `alarms.handle_alarm` records
  `alarm.handled`/`alarm.escalated` (`hivemind.supervision.record_alarm_event`) and, for a REBIND,
  fills `Intervene.binding` with the fallback key it resolved, since a Warden has no other way to
  learn it; `Queen`'s own COMPLETE_TASK handling records `alarm.resolved` once a rebound or
  retried attempt actually succeeds. `liveness.handle_infrastructure_item` (roadmap steps 4.7,
  4.2a) is where a Heartbeat, a ForageRequest and a CellWaxProposed all land, ahead of
  `queen.autopilot.table.decide`: a Heartbeat renews the sending Warden's own live grants
  (`liveness.renew_grants_on_heartbeat`) the same tick it resets liveness, `check_liveness`'s own
  sweep now also returns any grant whose lease lapsed (`hivemind.queen.forage.grants.
  sweep_expired`) to the pool unconditionally every tick, and `wax.handle_wax_proposed` records the
  proposal, writes it by autopilot within the per-Cell cap (no awake episode), or runs one awake
  episode with `cells_in_play = {the Cell in question}` and applies its `WRITE_WAX`/`REJECT_WAX`
  decision -- either way sending a `CellWaxWritten` back to the proposing Warden's own link once
  written.
- `queen.forage` (roadmap steps 4.7-4.8): `ForageLedger` -- the Queen's live book of Forage: every
  Cell's latest capacity, every Warden's own local-pool report (reported, never granted --
  codingrules 8.10), every live shared grant and the headroom they leave, backed by a
  `LedgerStore` (`InMemoryLedgerStore`, `SqliteLedgerStore`); step 4.8 adds three sub-books
  (`ledger.seats`, `.spend`, `.decisions`) for shared-seat capacity/usage, per-goal spend and
  hosting-plan/ceilings decisions, and `LedgerRecorder`, a `hivemind.llm.fanner.LlmEventRecorder`
  implementation that feeds the ledger from the Fanner without `llm` ever importing `queen`. See
  `queen/forage/README.md` for the full module-by-module map. `grants` drives every grant edge
  (`activate`, `revise`, `renew_grants_for_warden`, `revoke`, `sweep_expired`) through
  `hivemind.forage.grant_state`'s own transition table, with a `forage.*` trail event
  (`hivemind.queen.trail.record_forage_event`) for every one that changes state. `requests`
  (`handle_sub_bee_request`) answers a Warden's `ForageRequest` against the ledger's own
  headroom, through the autopilot rule in `queen.autopilot.forage.decide_forage_request`: SUB_BEES
  (4.7), SHARED_SEATS and SPEND (4.8) are each granted at once within headroom, denied (with a
  reason) otherwise, or (SUB_BEES/SHARED_SEATS only) contested (could be met by shrinking another
  live grant) recorded as `NEEDS_JUDGEMENT` at `Effort.HIGH` but not yet resolved (`queen.awake`
  holds no action to shrink another grant; see that package's own report); BINDING stays denied,
  routing's own job from phase 8 on. `hosting.write_hosting_plan` and `ceilings.set_ceilings`/
  `.change_ceilings` (4.8) write a Cell's `HostingPlan` and a Warden's `Ceilings`, each a pure
  decision plus an effectful trail-and-ledger (and, for ceilings, Waggle) edge.
- `queen.cluster` (roadmap step 4.9, docs/adr/0024-clustering-protocol.md): Clustering, the
  pause-and-preserve protocol for a provider outage. `protocol.cluster`/`.resume` find every bee
  bound to a provider through `deps.ledger.live_grants()` filtered against `deps.map` (the
  cheapest honest source, module docstring of `protocol.py`), send each Warden an
  `Intervene(HANDOFF)` then a `TaskPause`, move the task to `PAUSED` in the Brood Chamber and
  record one `queen.clustered`; `resume` finds a Handoff through Bee Bread
  (`MemoryStore.list_bee_bread_by_task`) and re-assigns through `hivemind.queen.dispatcher.
  resume_paused`, so no work is redone, recording one `queen.resumed`. Neither ever awaits a
  model. `health.HealthPoller` polls a clustered provider's own `LLMProvider.health()` on an
  exponential backoff (`ClusterBackoff`, `next_probe_at`); `orders` is the durable table
  `hive cluster`/`hive wake` (roadmap step 4.11) write into and `tick.run_cluster_tick` polls
  every tick, the same durable-row-polled-each-tick shape `queen.questions.
  sync_answers_from_chamber` already uses; `triggers.check_cost_caps` clusters a goal's own
  provider once its spend headroom hits zero. `tick.awake_available` is the pure check the
  orchestrator's own `_run_awake` consults so the Queen's autopilot runs this same protocol when
  her own `ModelSlot.QUEEN` provider is itself clustered. The Queen's own mode
  (`RUNNING <-> CLUSTERED`, per provider set) lives in `hivemind.queen.state.QueenMode`/
  `ClusterState`, held on the `Queen` instance, not in `QueenDeps`.
- `queen.requeening`, `queen.supersedure`: placeholders, populated in a later roadmap phase.

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
