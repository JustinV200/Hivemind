# hivemind.queen

The queen package is the Queen herself: the Hive's single orchestrator and only global view. It
covers her inbox, Autopilot and Awake modes, planning, placement (Real vs Virtual Cell), the
dispatcher, and the human inbox. She never provisions Cells and never spawns a Worker directly:
every assignment goes to a Warden, over Waggle.

## Public API (roadmap step 3.20)

- `Queen` (`queen.py`): the `waggle.loop.TickLoop` face. `attach_warden(link)` records an
  already-built `WardenLink` before `run()`; the Queen never creates Wardens or Cells herself. One
  tick drains everything queued on every attached Warden's own link (one reader task per link,
  `queen.inbox.LinkReaders`, at most one bounded queue's worth per link) into `InboxItem`s, orders
  them with her own Attendant (`queen.inbox`, human messages heavy but not absolute), dispatches
  each through `queen.autopilot.decide` (falling back to `queen.awake.decide_awake` for
  `NEEDS_JUDGEMENT`), checks every attached Warden's liveness and places whatever the Brood
  Chamber now says is ready -- both unconditionally, every tick, never gated behind an inbox item;
  a tick runs at least once per heartbeat interval even when nothing arrives (her own quiet-interval
  timer), so every Warden falling silent at once still draws its one Alarm each.
  Liveness is the Warden's, not her attention's: it is judged against the newest Heartbeat each
  link delivered, handled or not, at the cadence that Heartbeat declared (never below the
  manifest's), and a Heartbeat already older than the miss limit never brings a Warden back
  online (`queen.ticks.liveness`), so a stall of her own tick (a Virtual Cell provision, a long
  awake episode) or a Warden slower than the manifest (a Virtual Cell's, every 15 s) raises no
  false `CELL_UNREACHABLE`, and a stale backlog at most the one Alarm of one outage. Also implements
  `hivemind.supervision.Supervisor` over her attached Wardens. `_recoverable_errors` names
  `InvalidTransitionError`: a chamber transition failing on a stale status backs a tick off and
  records it (`queen.decided`), rather than ending `run()` and taking the whole Hive down.
  `stop()` sets the stop flag first, awaits every Virtual Cell still being provisioned or
  released beside her tick (never cancelling one), then reaps every attached Warden's own reader
  task, so none is ever left pending once `run()` ends (codingrules section 11).
- `QueenDeps`, `WardenLink`, `MemoryBudget`, `Housekeeping` (`deps.py`): every collaborator one
  Queen is built with, and one attached Warden's own address and link; manifest *slices* only,
  never a `HiveManifest`. `Housekeeping` (roadmap step 4.3's own wiring step) is the Queen's own
  `last_sweep_at` bookkeeping for `queen.ticks.housekeeping.run_housekeeping`, defined here (not
  in that ticks module) so a real import of it never has to run that whole sub-package's own
  `__init__` first -- every `queen.ticks` module already imports `QueenDeps` from here at runtime.
  `WardenLink.send`/`send_guarded` (phase-7 handoff open item 8) are the only way any Queen-side
  code sends on a Warden's own link: they never let `TransportClosedError`/`ConnectionLostError`
  escape (a Warden's link closing under a send used to crash the whole tick loop), and report
  whether the frame actually went out so a caller with durable state at stake -- the dispatcher's
  own grant-then-assign chief among them -- can react.
  `scratch_root` (roadmap step 5.0b) is the Hive Stand's own `[hive_stand] scratch_root`, resolved;
  `goal_submission.submit_goal` is its one reader.
- `queen.autopilot`: `QueenAction` (now including `WRITE_WAX`/`REJECT_WAX`/`CLEAR_WAX`, roadmap
  step 4.2a), `decide`, `effort_for`, `decide_forage_request`, `decide_wax_proposal` -- the
  deterministic dispatch tables; never imports `hivemind.llm`. `decide`'s own `_decide_task_
  result` maps a SUCCEEDED `TaskResult` whose `scout_report.feasible` is False to `FAIL_TASK`,
  never `COMPLETE_TASK` or `RETRY_TASK` (roadmap step 6.10): acceptance passed (the Scout wrote
  its report), but the Scout itself recommends against the work. A current FAILED result carrying
  `ResultCause.CELL_RUNS_NO_COMMANDS` maps to `REQUEUE_TASK` while its task does not yet need
  commands (ADR-0060): re-placed once where commands run, never retried on the Cell refusing them.
  The planner sets `needs.commands` itself on any task whose acceptance runs a command.
  `decide` reads how far a task has got as an `AttemptTally` (`made`: her own dispatches, which
  her ceiling and policy rungs count; `latest`: the highest attempt number dispatched or reported,
  so a FAILED report about an attempt a later one replaced changes nothing; `nurse_left`).
- `queen.awake`: `QueenDecision`, `QueenSources` (its `wax(cells)` returns WRITTEN Cell Wax
  capped per Cell, `cap_wax_for_hot_state`), `decide_awake` (now takes an optional
  `cells_in_play`, roadmap step 4.2a) -- one stateless episode on `ModelSlot.QUEEN`, at the
  `Effort` autopilot chose for the event class.
- `queen.planner`: `PlanSchema`, `PlannedTask`, `PlannedPostcondition`, `plan_goal` -- decomposing
  a goal into a validated `hivemind.brood_chamber.TaskGraphDraft`; every subtask carries at least
  one acceptance postcondition (roadmap step 3.18). `PlannedTask.leaves` (roadmap step 5.0b) is
  a bounded tuple of `waggle.messages.PlannedLeaving`, empty by default; `plan_goal` threads
  `PlanBrief.scratch_root` to the ladder as a validation context so a leaving declared inside
  scratch is refused and retried while planning, never discovered later. `PlannedTask.role`
  (roadmap steps 6.9/6.10) names DRONE, FORAGER or SCOUT; a FORAGER needs `needs.exoskeleton` and
  a SCOUT's acceptance is exactly one `FILE_EXISTS` on its own report file, both ladder-retryable
  like every other rule here. See `queen/planner/README.md` for the full rule.
- `queen.placement` (roadmap step 5.7, `docs/adr/0028-placement-policy-real-versus-virtual.md`):
  `Placement` (a union: `ReuseReal`, `ReuseDormant`, `ProvisionVirtual`, each with its own
  `reason`), `PlacementError`, `decide(needs, inventory, forage, policy)` -- the pure, ordered
  pipeline: Night Veil first (always a fresh Virtual Cell), then isolation (`REQUIRED` excludes
  every Real Cell), then each side's own candidates filtered (a `BLOCK` Cell Wax or
  `allow_hive_stand = false`, then fit, then Forage) and ranked (a `CAUTION` note behind a clean
  candidate, a dormant Cell before a fresh provision), only then honouring `prefer`. See
  `queen/placement/README.md` for the full module map.
- `dispatch_ready`, `redispatch`, `resume_paused`, `requeue_for_commands`, `requeue_unfinished`
  (`dispatcher/`, a package since roadmap step 5.7): place, grant and assign every ready task, never
  to a Worker directly. `dispatcher.snapshot` builds the pure `Inventory`/`ForageView`
  `queen.placement.decide` reads (the one place this dispatch awaits `deps.memory.list_wax`);
  `dispatcher.acquire.resolve_link` turns whatever `decide` returns into a `WardenLink`, acquiring a
  Virtual Cell through `QueenDeps. virtual_provider` first when it names one (re-entering placement
  once with that backend's own headroom zeroed on a failed acquire, ADR-0028 Consequences). A fresh
  task's own `queen.placed` (the placement's own reason), `chamber.assign`/`chamber.start` and
  `queen.assigned` land before either wire message is sent, so a fast sub-bee's own immediate
  Question can never reach `Queen._act` while the chamber still reads ASSIGNED. Roadmap step 4.8's
  own wiring step: before the first grant a Warden ever receives, `_ensure_warden_provisioned` sets
  its first `Ceilings` (`queen.forage.ceilings.set_ceilings`, from the Cell's own capacity report)
  and writes its Cell's `HostingPlan` (`queen.forage.hosting.write_hosting_plan`, now also sending
  `PlanWritten` over the link); `deps.ledger.decisions.ceilings_for(warden_id)` being `None` is what
  "newly attached" means, so every later dispatch to the same Warden is a no-op here. A send of
  either that the link refuses leaves the Warden owed (`DecisionBook.owe`): every dispatch pass
  sends both again, before any assignment, while it stays attached (`queen.forage.deliveries.
  redeliver_owed`), and every re-attach sends what is in force again (`forage.resend`). The
  `TaskAssign` it builds carries `task.spec.leaves` unchanged (roadmap step 5.0b) and
  `task.spec.role` (roadmap steps 6.9/6.10) as its own `role`, plus `recon`: the
  `waggle.messages.task.ScoutReport`s of the task's SUCCEEDED Scout dependencies, at most
  `MAX_RECON_REPORTS`, newest completion first (`_recon_for`), and `honey`, the Queen's pre-check
  (roadmap step 7.9). Grants are sized against the role's own `[forage.roles]` footprint when the
  manifest set one, else the Drone's (`forager`/`scout` are never required manifest keys). Every
  grant is sized by `dispatcher.sizing.size_grant`; a fresh dispatch's from the Cell's capacity as
  it stands when its link carries a live reader (`WardenLink.live_capacity`: the Hive Stand's,
  re-reading its load and free memory on every pass), every other from the link's own Cell as probed
  (a Virtual Cell's is fixed by its spec). A grant that runs no bee is never sent to the Warden
  (`.claude/phase-4-handoff.md` section 4.2 item 1 -- a grant that empty used to be sent anyway,
  park the task RUNNING with a `GRANT_EXCEEDED` escalation, and time out silently), and
  `dispatcher.zero_grant` decides what happens instead. A lasting shortfall (the Guard removed every
  binding, the Cell's own cap is zero, its whole memory or all its cores could never hold one bee,
  no allowed source offers a seat past the reserve, or any shortfall on a Cell whose capacity is
  fixed) records `forage.denied` with the figures and fails the task at once. A passing one (the
  Hive Stand's free cores or free memory right now, or every allowed model seat busy) is sized
  before the chamber moves, so the task stays PENDING, one `forage.denied` with `deferred = true`
  says so, every later pass tries again quietly, and the task fails with the figures only once
  `[forage] zero_grant_patience_s` has passed; busy seats, like a goal whose other running tasks
  hold its whole `max_sub_bees_per_goal`, are waited out before any Cell is chosen. One dispatch
  pass tries every ready task once, and a waiting one never holds up the rest. `redispatch` (a
  RUNNING retry) and `resume_paused` (a `resume_from` resume) fail at once on any zero: a RUNNING
  task has no queue to wait in, which is also why they size their grants from the link's own Cell as
  probed, as before, so a busy moment never fails running work. The dispatcher lifecycle fix: a pass
  never awaits a Virtual Cell's provision; `dispatcher.provisions` acquires it beside the tick (at
  most `ProvisionLane.limit` at once, collected by a later pass, awaited by `Queen.stop`), and a
  Cell acquired for a task that was cancelled or whose grant was denied is released through
  `QueenDeps.on_task_finished`. Every release, a finished task's included, runs beside the tick too
  (`dispatcher.releases`, `ReleaseLane`: at most 4 at once, the rest waiting their turn; two of one
  Cell in order; collected by every pass; awaited, never cancelled, by `Queen.stop` before the
  shutdown retires what is left), and placement offers a Cell being released to no task. A task a
  pass finds ASSIGNED was stranded between `chamber.assign` and `chamber.start` (a stop or a
  failure; nothing was sent to its Warden yet), so every pass first gives it back to PENDING through
  `chamber.unassign` and places it afresh as a new attempt. A stopping Hive does the same for
  everything it leaves on its own Cells: `requeue_unfinished` (`dispatcher.ready.unfinished`),
  awaited by `run_hive` once every Cell is gone, gives each task still ASSIGNED, RUNNING or BLOCKED
  on a Warden this Queen placed work on (`DispatchBook.placed_on`) back to PENDING, a BLOCKED one's
  question withdrawn first, so the next start places it afresh; a task on a Night Veil Cell is
  cancelled instead (teardown-only), and a PAUSED one keeps its hold. Placement reads each attached
  Cell's live capacity less the grants in force on it, and each backend's headroom less the Cells
  being provisioned on it; every pass first returns an ended task's grants to the pool
  (`queen.forage.grants.release_finished`, `forage.revoked` with cause RELEASED). The dispatcher's
  lock, waits and lane live in one `QueenDeps.dispatch` (`DispatchBook`). A fresh Virtual Cell's
  grant is sized from its spec before it is provisioned
  (`dispatcher.zero_grant.ready_to_provision`): a task whose grant there could run no bee (seats
  still busy once their patience is spent, a cap the grant's margin zeroes) waits, or is cancelled
  with `forage.denied`, instead of having a Cell made only to deny its grant and release it. A task
  no Cell can take records `queen.decided` once per cause (`DispatchBook.unplaced`), again only when
  the cause changes or it waits afresh, never on every pass. A Virtual backend whose provision fails
  is held back for a while (`dispatcher.backoff`, `DispatchBook.backoff`): placement skips it for 1
  s after one failed round, twice as long after each further round in a row, at most 5 minutes, then
  tries it one provision at a time, and a provision on it that succeeds ends the run. One
  `queen.decided` (reason `backend_held_back`) says when a run begins and one (`backend_restored`)
  when it ends, so a backend that is down costs a bounded few
  `cell.provisioning`/`cell.provision_failed` rows, not a pair on every pass. A grant or assignment
  that never reaches its Warden's link (a closed or dropped transport, `WardenLink.send` returning
  False) revokes the grant (`HOLDER_OFFLINE`) and fails the task at once, rather than leaving it
  RUNNING with no Worker.
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
  wire `CellWaxProposed` shape (`origin=HUMAN`, `proposer=None`) `queen.ticks.wax` judges either
  way.
- `queen.chat` (roadmap step 10.5, ADR-0040): the human end of her inbox, reached by the Hive
  Entrance through `ChatDoor`. When a device is revoked, `withdraw.refuse_unplanned` refuses its
  goal requests not planned yet (RECEIVED, AWAITING_CONFIRMATION, PLANNING) and
  `withdraw.stop_goal` stops a goal's unfinished tasks, telling each placed one's Warden with a
  `TaskCancel` before recording it CANCELLED (a goal whose Warden cannot be told is left running,
  and named so). Every goal-request edge is moved from the row as it stands, under
  `QueenDeps.intake_lock`, so a refusal and a plan landing beside her tick never overwrite each
  other; a plan that lands after its request was refused is stopped at once. At a Night Veil
  Cell's teardown `ChatLog.purge_night_veil` reduces every chat line naming the Cell or its ids
  to its skeleton in place, so no stream cursor moves.
- `QueenDeps.on_heartbeat`: handed every Heartbeat once she has recorded it, its one way out (a
  Heartbeat never reaches the trail); the Hive Entrance's telemetry board in `hive serve`.
- `record_event` (`trail/record.py`): the one place a `queen.*` trail event is built.
- The Night Veil boundary (codingrules section 12, `hivemind.pheromone.retention`): whenever a
  Virtual side exists, `QueenDeps.trail` is the `VeiledTrail` the composition root builds, so a
  record about a Night Veil Cell or one of its tasks reaches the durable trail only as its skeleton
  and whole only in the Cell's ephemeral segment. The Queen routes her own records to it by id:
  `attach_warden` files a Night Veil Cell's Warden under the Cell (`segments_of`), `submit_goal`
  expects every task of a Night Veil goal, and `LifecycleVirtualCellProvider.acquire` binds a task
  to the Night Veil Cell it now runs on. `TrailSegmentReceiver` (`trail/sync.py`) offers every
  shipped segment to the ephemeral segments first, so a Night Veil Cell's own trail never merges
  into the durable one. It routes by the Cell a chunk names, so the Cell gate hands it only chunks
  naming the Cell their link proved (`cell_gate.trust.refusals`, reason `another_cell`): a Night
  Veil Cell cannot ship its segment onto the durable trail under a MEADOW Cell's id, nor a MEADOW
  Cell bury its own in a Night Veil Cell's segment. Nothing the Queen keeps elsewhere may outlive
  the Cell either: she refuses Cell Wax about a Night Veil Cell (`ticks/wax.py`, recorded as
  `queen.decided` with the reason alone), placement never offers an attached Night Veil Cell to any
  task (`dispatcher/snapshot.py`: it holds only the task it was provisioned for), and her Forage
  ledger forgets every row keyed to the Cell or its Wardens at its teardown
  (`ForageLedger.forget_cell`, the ledger's Night Veil side channel, with `rows_about` as its member
  source). Isolating a Night Veil Cell writes no BLOCK wax and labels none of its rows in the Hive's
  tables, and reads the Cell's own records (its standing isolation, its bees' pause answers, its
  tasks and bees for the taint order) through `hivemind.pheromone.query_cell`, since they live only
  in its segment; the human's lift reads them there too.
- `queen.inbox`: `queen_attendant`, `to_inbox_item`, `ModelTieBreaker` -- the Queen's own
  Attendant, with an optional model-backed tie-breaker on `ModelSlot.ATTENDANT`. A
  `CellWaxProposed` classifies as a routine `WAGGLE_MESSAGE` (roadmap step 4.2a: "scores low"),
  never its own `InboxKind`. `TaskClaims` (`claims.py`): a Warden speaks only for the tasks placed
  on its own Cell; a result, progress report, Alarm, question or answer about a live task placed
  elsewhere, or nowhere, is refused before `decide` as `guard.task_refused` about the sending Cell.
- `queen.attach`: the one path every Warden joins by (`attach_warden`, `serve_warden` for a
  gate's proved link, `FanoutTransport`). A link relays a snapshot or rollback request only about
  the Cell its handshake proved; any other is answered with an error and recorded as
  `guard.relay_refused` (`refusals`, the `relay_forgery` dire pattern). While the Queen freezes a
  Cell for a snapshot or rollback, `frozen_cell` holds its Warden's liveness
  (`ticks.liveness.frozen_by_the_hive`), so the freeze is never judged a stall.
- `queen.ticks`: `alarms`, `liveness`, `results`, `forage`, `wax`, `housekeeping` -- the tick
  handlers each
  `QueenAction` (or, for `forage`/`wax`, each received `ForageRequest`/`CellWaxProposed`) calls
  into, split out only to stay within codingrules 5.1's size limits. `alarms.handle_alarm` records
  `alarm.handled`/`alarm.escalated` (`hivemind.supervision.record_alarm_event`) and, for a REBIND,
  fills `Intervene.binding` with the fallback key it resolved, since a Warden has no other way to
  learn it; `Queen`'s own COMPLETE_TASK handling records `alarm.resolved` once a rebound or
  retried attempt actually succeeds. `results.AttemptBook` is the Queen's per-task count of her
  own dispatches and the highest attempt number any report carried; a retry numbers past it, so
  a Warden's own respawns never make an attempt number repeat.
  `liveness.handle_infrastructure_item` (roadmap steps 4.7, 4.2a) is where a Heartbeat, a
  ForageRequest and a CellWaxProposed all land, ahead of `queen.autopilot.table.decide`: a
  Heartbeat renews the sending Warden's own live grants
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
  `deliveries` (`redeliver_owed`) sends an attached Warden its ceilings and plan in force again
  when the first send found its link closed, until the link takes them.
- `queen.cluster` (roadmap step 4.9, docs/adr/0024-clustering-protocol.md): Clustering, the
  pause-and-preserve protocol for a provider outage. `protocol.cluster`/`.resume` find every bee
  bound to a provider through `deps.ledger.live_grants()` filtered against `deps.map` (the
  cheapest honest source, module docstring of `protocol.py`), send each Warden an
  `Intervene(HANDOFF)` then a `TaskPause`, move the task to `PAUSED` in the Brood Chamber and
  record one `queen.clustered`; `resume` finds a Handoff through Bee Bread
  (`MemoryStore.list_bee_bread_by_task`), or the newer one a remote Warden's bee deposited as Nectar
  (`swarm_gate.recall`), and re-assigns through `hivemind.queen.dispatcher.
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
- `queen.quarantine` (roadmap step 10.6c, ADR-0043): the Queen's side of the one quarantine.
  `order_quarantine(deps, wardens, lever, alarm_id=None) -> bool` sends `Intervene(QUARANTINE)`
  (`hivemind.supervision.to_intervene`) to the Warden the Brood Chamber places the lever's task
  on, and records `queen.decided` (action `QUARANTINE_BEE`); it returns False, sending nothing,
  when the task is unplaced or its Warden detached. `lever_from_alarm(payload)` scopes one from an
  Alarm (its bee, task and trail event), or None when the Alarm names no task or no event, which
  the Queen's `QUARANTINE_BEE` handling (`queen.ticks.alarms`) then escalates to the human
  instead. `hold_task(deps, progress)` is `PAUSE_TASK`: the Warden that quarantined a bee reports
  its task at stage PAUSED, and the Queen withdraws a question the bee left it blocked on, then
  moves it RUNNING -> PAUSED (the Brood Chamber is hers alone to write). `Queen.intervene(warden,
  Quarantine(...))` carries a whole lever too. The Warden's SECURITY Alarm about it goes to the
  human by the shipped policy; the way out is a `resume_paused` from the quarantine's checkpoint
  once a judge has cleared it, which the Warden's gate admits and nothing else does. Roadmap step
  10.6a orchestrates it: `resume_cleared(deps, wardens)` (`release.py`), run on every tick, resumes
  each PAUSED task whose `warden.intervened` checkpoint a `memory.taint_cleared` names, when that
  verdict is newer than the task's last pause and its Cell is not isolated.
- `queen.guard_requests` (roadmap step 10.6a, ADR-0043, `docs/guard/isolation.md`): the Queen's
  side of a Guard request. `QueenGuardDoor`/`GuardDoor` (the running `Queen` *is* the Guard Bee's
  `hivemind.guard.GuardRequestDoor`, handed over as `Hive.guard_door`) file a request into her own
  durable table (`GuardRequestStore`: `InMemoryGuardRequestStore`, `SqliteGuardRequestStore` on the
  `[hive] db` file) before returning, and wake her; `report_to_human` shows a CRITICAL report as a
  SECURITY Alarm, once per report id (`show.show_alert`, the one showing path). `guard_items`
  turns every undecided row into a `GUARD_REQUEST` inbox item (a fixed 100 times the Guard
  principal's multiplier: above every Alarm and human message). `GuardDeps` is her one
  `QueenDeps.guard` field: the table, `[guard] dire_patterns`, the `CellEgress` seam and the pause
  bound. At a Night Veil Cell's teardown `GuardRequestStore.purge_night_veil` deletes every
  request naming it, but never one still holding goals off a Cell. The `alarm.escalated` row
  of a Night Veil Cell's SECURITY Alarm carries the Cell's id, so the veil keeps it in that Cell's
  segment.
  `decision.decide_guard_item` decides each item on her tick: the dire-pattern rule
  (`queen.autopilot.guard`, no model), one awake episode with the report's facts, or the fallback
  (isolate); records `queen.decided` before any effect; carries it out (`act`: the one isolation
  path, the Hive Stand's fallback of quarantine orders plus a `PlacementHold` placement reads as a
  block for the held goals, a quarantine order, or nothing); stamps the row.
- `queen.isolation` (roadmap step 10.6a): the one Cell isolation path. `isolate_cell(site, order)`
  checks `EnforcementPoint.ISOLATION` (`authority`: the Queen is refused the Hive Stand's own
  lease), writes the `BLOCK` Cell Wax, revokes the Warden's grants (`access`), checkpoints and
  pauses every bee on the Cell with a bounded wait for their `worker.paused` (`pause`), cuts a
  Virtual Cell's egress, records `cell.isolated` (`record`, where a Cell's `OPEN`/`ISOLATED` state
  lives), taints its memory from the first cited event (`taint`: the Hive's tables, then a
  `CellTaintOrder` for the store the Cell's Warden keeps, resent by `resend_taint_order` whenever
  that Warden attaches while the isolation stands) and raises a CRITICAL SECURITY Alarm (`site`).
  `lift_isolation` is the human's lift (holds released, wax cleared, egress restored,
  `cell.isolation_lifted`; taint and paused tasks stay). `IsolationDoor` gives the running `Queen`
  both as methods for the Hive Entrance's `POST /v1/cells/{cell_id}/isolate` and `/lift`. Her own
  `ISOLATE` policy row (`QueenAction.ISOLATE_CELL`, `ticks.alarms`) isolates an Alarm's Cell the
  same way, and only the Cell the sending Warden's link proved: an Alarm naming another is refused
  as `guard.task_refused` (`inbox.TaskClaims.refuse_cell`).
- `queen.requeening`: Requeening (roadmap 13.1 and 13.2, ADR-0062). `begin_requeening` before
  her loop starts and `settle_requeening` on her tick restore a Queen whose process died: orphaned
  tasks placed afresh from their Handoffs, the Hive Stand's orphaned lease given back, Virtual
  Cells' Wardens waited for and taken back, one `queen.requeened` of counts; her handled Alarms
  and questions survive a restart, and her periodic snapshot bounds what recovery reads.
- `queen.supersedure`: a placeholder, populated by roadmap step 13.2a.

## Enforcement points (roadmap step 10.3)

The Queen passes seven of ADR-0039's points through `QueenDeps.enforcer` (the Guard's `Enforcer`,
built by the composition root); `queen.authority` names what each principal holds there (her
`queen` role default; a Warden's set as `warden_set` computes it from its Cell's access level; a
goal's set, or None for the operator's own local path) and builds her requests.

- `submit_goal(..., capabilities=)` carries the submitter's set to every planned task
  (`TaskSpec.capabilities`); the dispatcher sends it on Waggle 1.8's `task.assign`, beside the
  task's network scopes (Waggle 1.6).
- `placement`: `placement.decide` excludes any candidate the goal's set does not allow; when that
  alone leaves none, `dispatcher.ready` records `queen.decided` with the full reason and one
  `guard.denied` per missing capability, then cancels the task with that reason: a goal's set is
  fixed for its whole life, so no later pass could place it.
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
- `isolation` (roadmap step 10.6a): `isolation.authority.authorize_isolation` requires the Cell's
  own capability (`cell:hive_stand`, `cell:virtual`, `cell:real:<id>`) of the Queen's or the
  operator's set, and refuses the Queen the Hive Stand's own lease (`guard.scope.hive_stand`).

Roadmap steps 10.3a-d add the tiers' floors (`hivemind.guard.policy.floors`):

- A task asking for or bound to NIGHT_VEIL meets the floors at `placement` before any Cell is
  chosen (`dispatcher.night_veil.check_night_veil_placement`): only a human's durable goal
  request naming the tier initiates it (the request `QueenDeps.goal_requests` holds, cited by
  `TaskSpec.goal_request_id`), and the control link configured for its Cell must be a `.onion`
  host through a loopback `socks5h` proxy. A floor's refusal, or an incomplete Night Veil tier
  profile (`placement.policy.check_night_veil`), is a final `PlacementError`: the task is
  cancelled once, with the reason on `queen.decided` and `task.cancelled`.
- `comb_shield_egress` runs the floors for every task, the operator's own included, with the
  Cell's tier and the Night Veil facts on the context (`dispatcher.night_veil.tier_context`).
- Every grant check states whether a binding is local to its Cell (a source that Cell serves, or
  a provider in `QueenDeps.in_process_providers`), so a Night Veil grant keeps local bindings only.
- `chamber.assign` binds the task to its Cell's tier (`Task.bound_tier`); `authority.task_context`
  carries it at every later check, and `queen.placed` names the goal request a placement stands on.
- A goal whose request named NIGHT_VEIL is refused when its ceiling or its planned needs ask for a
  location capability (`planner.location`, `NightVeilLocationError`, one `guard.denied` each); the
  intake settles it REFUSED like any other planning failure.

## The swarm gate (roadmap steps 11.6, 11.6a, 11.9)

`swarm_gate/` is the Queen's side of the Swarm: the devices an operator enrolled (existing machines
the Hive borrows as Real Cells and leaves exactly as found, each running a Pollen Packet that dials
the Hive Stand). The swarm listener (`hivemind.swarm`, Layer 3) admits and routes them and tells its
hub who is connected; it supervises nothing, and Layer 3 cannot import the Queen. So the part that
turns a connected device into a Cell the Queen can place work on lives here, and serves it
according to its rung of the runtime ladder (ADR-0052).

- `SwarmGate` (`gate.py`) owns the listener and is the hub's `HubListener`. `start(queen, deps)`
  binds the Wardens, the hold and the colonizer to the Queen, binds the door for device Wardens on
  the listener and opens it; `run()` is a `TaskGroup` holding the gate's loop, every Level 0
  Warden's run task and every colonization flow; `stop()` ends the loop, cancels the flows (each
  puts back the tasks it paused), stops every Warden (each releases its lease over the packet's
  link, so the device is left as found) and only then closes the listener. `on_online` and
  `on_offline` run in the task that reads a device's link, so they only note what happened and wake
  the loop; the loop does the work, every `[swarm] heartbeat_interval_s` and at once when woken.
- `DeviceWardens` (`wardens.py`) is the table of Level 0 Wardens, one per device, built by an
  injected `DeviceWardenFactory` (`hivemind.cli.compose.swarm`). `serve` attaches a Warden to the
  Queen (her `warden_spawn` point), starts it (it leases the device) and runs it; `stop` reverses
  that in an order that leaves the device as found. A device already served is never served
  twice, so one that drops and returns keeps its Warden. A Warden whose run loop ends on a device
  error ends only itself, and the next pass replaces it.
- `DeviceWardenDoor` (`attach.py`) is the other half: once a device is colonized its own Warden
  dials the swarm listener, and the listener hands the verified connection here. The door admits it
  only if it is the node's own Warden naming the node's own Cell, and only if the gate wants one (a
  colonized device's, or the Level 0 device's whose colonization is waiting for it); then the
  connection goes through the same attach path a Virtual Cell's Warden does
  (`hivemind.queen.attach.serve_warden`): the same fan-out, `warden.spawned` or
  `warden.reconnected`, trail segment (signed with the node's key, required), refusals. A device
  Warden's entry stays with the Queen when its connection ends: a device comes back. A returning
  Warden sends again the frames of its last stretch right behind its handshake (ADR-0053), so
  `serve_warden` notes the attach (`LinkHooks.on_returning`) before it reads anything, and the
  reconnection grace is open for the copies the replay window drops.
- `DeviceReconciler` (`reconcile.py`) is the rule, applied per node and level-triggered (each pass
  reads the registry and the hub afresh, so a missed or repeated notice costs nothing): a revoked
  node has its Warden stopped (releasing its lease while the link is up), its link closed, its key
  distrusted and its tasks taken back; a node a colonization owns is left to the flow; a reachable
  Level 0 node gets a Warden, keeps it, or (its terms changed, or its loop died) has its tasks
  paused and taken back and a Warden rebuilt; a node above Level 0 is reachable while its Warden's
  link is open, is sent a changed access level or tier at once and has its Warden restarted under
  it (`Colonizer.renew`, `colonize/renew/`), and, when it is a Nuc whose Warden connected afresh,
  has its model server proved or its rung lowered (`Colonizer.restore`, `colonize/nuc/restore.py`);
  a pending `desired_level` is handed to the colonizer, and a device with none to the Queen's own
  rung decisions (`rungs/`); anything not reachable goes to the hold.
- `UnreachableDevices` (`unreachable/`) holds a lost device's tasks (`swarm.node_held`; the
  dispatcher neither fails nor moves them, `DispatchBook.held`), tells a Level 0 Warden its Cell was
  lost (one `CELL_UNREACHABLE` Alarm), marks a device whose own Warden is gone UNREACHABLE, and asks
  the diagnosis seam for a reason: the Hive installs `HiveStandDiagnosis`
  (`unreachable/hive_stand.py`, ADR-0056), which creates one task, without the planner, for the Hive
  Stand's own Warden (a low-tempo Drone the Queen asked for, pinned to the Hive Stand) to check the
  device's reachability from this end; its outcome is deposited as Nectar when it completes, and a
  task nobody started is cancelled when the hold ends. When `[swarm] unreachable_grace_s` ends it
  decides by the rung (`decide.py`): a Level 0 or Level 1 device's tasks are taken back
  (`takeback.py`: paused, their newest Handoff noted, unassigned, placed afresh) and its Warden let
  go; the newest Handoff is the newest she can hold (`recall.py`): a colonized device's own store is
  lost with it, so a Handoff only the Honey Store has, as the HANDOFF Nectar its bee deposited, is
  filed in her memory first, unless it is tainted, written under a standing isolation, above the
  task's clearance or a Night Veil Cell's. A Nuc's tasks keep waiting, up to `[supervision]
  max_offline_s`. A device back inside the window cancels the hold. Every decision is a
  `queen.decided`.
- `Colonizer` (`colonize/`) carries out the rung an operator asked for, one at a time
  (`request.py` decides, `guard.py` asks the Guard's `NUC_PROMOTION` point for every change). A
  promotion (`promote.py`): PREPARE the runtime, pause the Level 0 Warden's tasks into Handoffs,
  stop that Warden, START the device's, wait for it to attach (`arrivals.py`), then move the rung
  (`swarm.promoted`), record `warden.migrated` and resume each task from its Handoff, carried
  inline. Any failure sends WITHDRAW, rebuilds the Level 0 Warden, resumes the tasks there and
  records `swarm.demoted` with the reason. A withdrawal (`withdraw.py`) only while the device's
  Warden is idle. The Nuc rungs are `colonize/nuc/`: `facts.py` decides (`swarm.nuc.decide_nuc`)
  and sets the Warden's ceilings, `promote.py` orders the device's Warden to start its declared
  model server (`swarm.nuc_promote`), waits for `swarm.nuc_promoted`, moves the rung and writes the
  device's hosting plan with its own models first (undoing all of it on a failure), `demote.py`
  stops the server and tidies the Hive's map, and `desk.py` keeps what device Wardens declared and
  the orders waiting for their answers, which the gate's door consumes before the Queen's reader
  sees them. `Colonizer.launch` starts the same flows for a step the Queen chose herself.
- `RungEvaluator` (`rungs/`) is the Queen's own decision to move a device nobody asked to move.
  Each pass `begin_pass` settles the moves she launched before (`keeper.py`: a failed one backs
  off, doubling to 32 times `retry_after_s`; a raised rung is marked as hers) and reads the Hive
  once (`readings.py`: the unfinished tasks, the Hive Stand's reported capacity, the pool's
  seats), and `evaluate` asks the pure `decide_rung` (`decide.py`) and launches the step through
  the colonizer, behind the same Guard check, one move at a time for each pressure. The figures
  are defined once, in `pressure.py`: the relief pressure is the larger share of the Hive Stand's
  cores or memory that Level 0 Wardens' running sub-bees take, and the seat pressure is the share
  of the pool's usable seats live grants hold; each bears only on a device with a sub-bee running.
  A task that sets `TaskNeeds.survives_link_loss` asks survival of the device it is placed on or
  pinned to (`needs.py`). She takes back only a rung she raised, once its reasons have been gone
  for `lower_after_s` and the device is idle; the operator's `ceiling` is never climbed past, and a
  device with a `desired_level` is never evaluated (ADR-0052).

A Level 0 Warden runs here, in the Queen's own process, over an in-memory link, with its hands on
the device through `PollenSession`; it gives its lease back whenever it settles with nothing to do
(`WardenDeps.device`, `DeviceDeps`), and leases again for the next task.

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
