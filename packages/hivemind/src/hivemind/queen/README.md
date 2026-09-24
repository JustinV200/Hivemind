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
- `QueenDeps`, `WardenLink`, `MemoryBudget`, `Housekeeping` (`deps.py`): every collaborator one
  Queen is built with, and one attached Warden's own address and link; manifest *slices* only,
  never a `HiveManifest`. `Housekeeping` (roadmap step 4.3's own wiring step) is the Queen's own
  `last_sweep_at` bookkeeping for `queen.ticks.housekeeping.run_housekeeping`, defined here (not
  in that ticks module) so a real import of it never has to run that whole sub-package's own
  `__init__` first -- every `queen.ticks` module already imports `QueenDeps` from here at runtime.
  `scratch_root` (roadmap step 5.0b) is the Hive Stand's own `[hive_stand] scratch_root`, resolved;
  `goal_submission.submit_goal` is its one reader.
- `queen.autopilot`: `QueenAction` (now including `WRITE_WAX`/`REJECT_WAX`/`CLEAR_WAX`, roadmap
  step 4.2a), `decide`, `effort_for`, `decide_forage_request`, `decide_wax_proposal` -- the
  deterministic dispatch tables; never imports `hivemind.llm`.
- `queen.awake`: `QueenDecision`, `QueenSources` (its `wax(cells)` returns WRITTEN Cell Wax
  capped per Cell, `cap_wax_for_hot_state`), `decide_awake` (now takes an optional
  `cells_in_play`, roadmap step 4.2a) -- one stateless episode on `ModelSlot.QUEEN`, at the
  `Effort` autopilot chose for the event class.
- `queen.planner`: `PlanSchema`, `PlannedTask`, `PlannedPostcondition`, `plan_goal` -- decomposing
  a goal into a validated `hivemind.brood_chamber.TaskGraphDraft`; every subtask carries at least
  one acceptance postcondition (roadmap step 3.18). `PlannedTask.leaves` (roadmap step 5.0b) is
  a bounded tuple of `waggle.messages.PlannedLeaving`, empty by default; `plan_goal` threads
  `PlanBrief.scratch_root` to the ladder as a validation context so a leaving declared inside
  scratch is refused and retried while planning, never discovered later.
- `queen.placement` (roadmap step 5.7, `docs/adr/0028-placement-policy-real-versus-virtual.md`):
  `Placement` (a union: `ReuseReal`, `ReuseDormant`, `ProvisionVirtual`, each with its own
  `reason`), `PlacementError`, `decide(needs, inventory, forage, policy)` -- the pure, ordered
  pipeline: Night Veil first (always a fresh Virtual Cell), then isolation (`REQUIRED` excludes
  every Real Cell), then each side's own candidates filtered (a `BLOCK` Cell Wax or
  `allow_hive_stand = false`, then fit, then Forage) and ranked (a `CAUTION` note behind a clean
  candidate, a dormant Cell before a fresh provision), only then honouring `prefer`. See
  `queen/placement/README.md` for the full module map.
- `dispatch_ready`, `redispatch`, `resume_paused` (`dispatcher/`, a package since roadmap step
  5.7): place, grant and assign every ready task, never to a Worker directly. `dispatcher.snapshot`
  builds the pure `Inventory`/`ForageView` `queen.placement.decide` reads (the one place this
  dispatch awaits `deps.memory.list_wax`); `dispatcher.acquire.resolve_link` turns whatever
  `decide` returns into a `WardenLink`, acquiring a Virtual Cell through `QueenDeps.
  virtual_provider` first when it names one (re-entering placement once with that backend's own
  headroom zeroed on a failed acquire, ADR-0028 Consequences). A fresh task's own `queen.placed`
  (the placement's own reason), `chamber.assign`/`chamber.start` and `queen.assigned` land before
  either wire message is sent, so a fast sub-bee's own immediate Question can never reach
  `Queen._act` while the chamber still reads ASSIGNED. Roadmap step 4.8's own wiring step: before
  the first grant a Warden ever receives, `_ensure_warden_provisioned` sets its first `Ceilings`
  (`queen.forage.ceilings.set_ceilings`, from the Cell's own capacity report) and writes its
  Cell's `HostingPlan` (`queen.forage.hosting.write_hosting_plan`, now also sending `PlanWritten`
  over the link); `deps.ledger.decisions.ceilings_for(warden_id)` being `None` is what "newly
  attached" means, so every later dispatch to the same Warden is a no-op here. The `TaskAssign` it
  builds carries `task.spec.leaves` unchanged (roadmap step 5.0b). A fresh grant that computes to
  `max_sub_bees < 1` is never sent to the Warden: `_send_grant_and_assign` records `forage.denied`
  (the allocator's own reason plus the free-memory/reserve/seat figures that produced zero) and
  fails the task at once instead (`.claude/phase-4-handoff.md` section 4.2 item 1 -- a grant that
  empty used to be sent anyway, park the task RUNNING with a `GRANT_EXCEEDED` escalation, and time
  out silently). `redispatch` (a RUNNING retry) and `resume_paused` (a `resume_from` resume) both
  funnel through the same `_send_grant_and_assign` and fail the same way.
- `submit_goal` (`goal_submission.py`): plan a goal, mint and persist its task graph, and dispatch
  what's ready -- `Queen.submit_goal`'s own body, pulled into a module-level function (taking
  `QueenDeps`/`WardenLink`s explicitly, never a `Queen`) so `queen.py`, pinned at the codingrules
  5.1 file cap, never grows for a new `PlanBrief` field.
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
- `queen.ticks`: `alarms`, `liveness`, `results`, `forage`, `wax`, `housekeeping` -- the tick
  handlers each
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
  decision plus an effectful trail-and-ledger-and-Waggle edge: `write_hosting_plan` now also
  sends `PlanWritten` to the Cell's own Warden when given one (roadmap step 4.8's own wiring step,
  `dispatcher._ensure_warden_provisioned`'s one caller), mirroring `set_ceilings`'s own send.
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
  `run_housekeeping` (roadmap step 4.3's own wiring step) is the one call `Queen`'s own tick
  makes in place of the bare `run_cluster_tick(...)` it used to call directly: it still runs
  Clustering's own check first, then, once `[memory] sweep_interval_s` is due
  (`hivemind.workers.roles.house_bee.SweepSchedule`), runs one House Bee sweep directly over the
  Queen's own memory store -- demotion, Cell Wax expiry, then compaction on `ModelSlot.RIPENER` --
  never through a `TaskAssign`, since the Queen has no Cell of her own. A missing RIPENER binding
  never raises out of the tick: compaction is skipped for that sweep (demotion and wax expiry
  still run), logged once via `deps.housekeeping.ripener_unbound_warned`. The very first tick only
  seeds `deps.housekeeping.last_sweep_at` rather than sweeping immediately.
- `queen.requeening`, `queen.supersedure`: placeholders, populated in a later roadmap phase.

## Enforcement points (roadmap step 10.3)

The Queen passes six of ADR-0031's points through `QueenDeps.enforcer` (the Guard's `Enforcer`,
built by the composition root); `queen.authority` names what each principal holds there (her
`queen` role default; a Warden's set as `warden_set` computes it from its Cell's access level; a
goal's set, or None for the operator's own local path) and builds her requests.

- `submit_goal(..., capabilities=)` carries the submitter's set to every planned task
  (`TaskSpec.capabilities`); the dispatcher sends it, and the task's network scopes, on Waggle 1.6's
  `task.assign`.
- `placement`: `placement.decide` excludes any candidate the goal's set does not allow; when that
  alone leaves none, `dispatcher.ready` records `queen.decided` with the full reason and one
  `guard.denied` per missing capability, and the task stays PENDING.
- `comb_shield_egress`: `dispatcher.acquire` refuses to provision or resume a Virtual Cell at a
  tier the goal lacks `cell:comb_shield:<tier>` for, before anything is provisioned.
- `grant_issue`: `dispatcher.grants.authorize_grant` removes each binding whose `llm:<slot>` the
  receiving Warden's set or the goal's set does not allow (a `guard.denied` each); a grant left
  with no binding is refused like a zero-bee grant.
- `forage_request`: `ticks.forage` requires `forage:request` and that the requester holds the
  grant (`guard.scope.grant_holder` otherwise); a refusal is `forage.denied` too.
- `warden_spawn`: `attach.attach_warden` (now async) requires `warden:spawn`, records
  `warden.spawned`, and raises `WardenSpawnRefusedError` with nothing attached on a refusal.
- `question_routing`: `questions.block_on_question` refuses a Question from a Warden whose set
  lacks `question:human`, answering it back down its link instead of blocking the task.

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
