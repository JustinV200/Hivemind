# hivemind.wardens

The wardens package is the Warden: the per-Cell supervisor that spawns and supervises Workers on
exactly one Cell. It covers Warden state, its inbox, its Autopilot (which never awaits a model)
and Awake modes, spawning, its local model pool, offline handling and read-only Watch mode; a
Warden never provisions Cells itself.

## Public API (roadmap steps 3.18, 3.19)

- `Warden` (`warden.py`): the `waggle.loop.TickLoop` face. `start()` leases the Warden's Cell (or
  moves to `WATCH` on a refusal); `stop()` sets the stop flag first, then reaps its own heartbeat
  deadline, retires every sub-bee (`wardens.ticks.alarms.retire_sub_bee`: stopped cooperatively,
  falling back to a bounded cancel, its slot freed), reaps every receive task it owns and releases
  the lease -- never returning with a task it started still pending (codingrules section 11).
  Every way a sub-bee ends takes that same `retire_sub_bee` path, so its slot always comes back:
  a claim accepted, an Alarm's RETRY/REBIND/CANCEL_TASK, a quarantine, the lease taken back, the
  Queen's next attempt of the same task superseding it, and a Heartbeat saying the bee has ended
  with nothing more to send (`SubBee.has_ended`: KILLED by a cancel or kill, DONE after a
  stop-handoff, or FAILED once a cancel has reached it). A bee that stopped at a Handoff its own
  Warden ordered (its context past the handoff threshold, or the Warden's Handoff lever) is the one
  ending that is not the task's: a fresh bee resumes the same attempt from that Handoff in its slot
  (`SubBee.awaits_successor`, `wardens.ticks.alarms.resume_from_handoff`). One tick drains the queen link
  and every sub-bee link into `InboxItem`s, orders them with this Warden's own `Attendant`, dispatches each
  through `wardens.autopilot.decide` (falling back to `wardens.awake.decide_awake` for
  `NEEDS_JUDGEMENT`), and sends a `Heartbeat` once the interval elapses -- a send that finds the
  Queen link already closed or dropped is recoverable (`wardens.ticks.heartbeat.send_heartbeat`
  records `warden.offline` and moves on), never an exception out of this Warden's own loop or
  `stop()`: every Warden -> Queen and Warden -> sub-bee send in this package goes through
  `wardens.links.send_guarded` (phase-7 handoff open item 8), not `Warden.stop()` alone. A
  Warden that dials the Queen holds a reconnecting link that queues its results, progress and
  Alarms durably while cut off (`wardens.offline`, ADR-0053).
  Also implements `hivemind.supervision.Supervisor` over its sub-bees. Roadmap step 4.9
  (Clustering): `_settle_after_tick` also settles `ACTIVE <-> CLUSTERED` -- every current sub-bee's
  own task in `warden._clustered_tasks` (populated from a Queen-sent `Intervene(HANDOFF)`/
  `TaskResume`, `hivemind.wardens.state.clustering_update`) moves it to `CLUSTERED`, and it stays
  there once those paused bees have stopped and been retired; any one no longer in that set (a
  `TaskResume`, the Queen's fresh `TaskAssign` resuming the task, or her cancel of it) moves it
  back -- by way of ACTIVE to WATCH when nothing is left, CLUSTERED's only way out -- recording
  `warden.clustered`/`warden.active` (`state.SETTLED_EVENT_KINDS`) each time. Roadmap step 4.8's
  own wiring step: the RECORD handler (`wardens.ticks.dispatch`'s own `_record_routine`) also
  handles a Queen-sent `CeilingsSet`/`PlanWritten`, storing them as `_ceilings`/
  `_hosting_plan` (`wardens.ticks.control.handle_ceilings_set`/`.handle_plan_written`) with no
  fresh trail event -- the Queen already recorded `forage.ceilings_set`/`forage.plan_written`
  before sending either.
- `WardenState`, `TRANSITIONS`, `assert_transition`, `can_transition`, `is_terminal`
  (`state.py`): the one Warden state machine (Appendix C). `settled_state`, `clustering_update`
  (roadmap step 4.9): the pure ACTIVE/WATCH/CLUSTERED decision and clustered-task-set update
  `wardens.ticks.assign.settle_after_tick` and `wardens.ticks.control.forward_control` call,
  split out here to stay within codingrules 5.1's file-size limit.
- `WardenDeps` (`deps.py`): every collaborator one Warden is built with.
- `send_guarded` (`links.py`): the one guarded send every Warden -> Queen and Warden -> sub-bee
  call goes through. It never lets `TransportClosedError`/`ConnectionLostError` escape (a link
  closing under a send used to crash this Warden's whole tick loop, since `Warden` overrides no
  `_recoverable_errors`), and reports instead whether the frame actually went out. Its own module
  sits below `deps.py` and `trail_sync.py`, so both import it at module level.
- `RelaySnapshotter` (`snapshot_relay.py`, roadmap step 5.10): a Virtual Cell Warden's
  `Snapshotter`, relayed to the Queen, who alone can reach the backend. A snapshot freezes the
  whole Cell, this Warden included, so each request is announced first: one Heartbeat declaring
  the relay's own timeout as the Warden's interval (`ticks.heartbeat.announce_freeze`, bound when
  the Warden is built), and the Queen judges the frozen Warden by that longer window. A reply
  resolves only the ask whose request its envelope's `correlation_id` names (carried on
  `InboxItem.correlation_id`), so two proposals in flight at once never take each other's
  snapshot when the Queen answers them out of order.
- `AcceptanceReport`, `run_acceptance` (`acceptance.py`): the Warden-side half of a task's
  acceptance criteria (roadmap 3.18) -- run on the Warden's own session, never the sub-bee's.
  A command criterion runs only where the Warden's Cell may run commands (ADR-0060, through
  `supervision.capping.CommandAdmission`); refused, it fails unrun as the Warden's own
  `guard.denied`, and the FAILED result carries `ResultCause.CELL_RUNS_NO_COMMANDS` (as it does
  when the sub-bee's claim did) so the Queen re-places the task instead of retrying it here.
- `cell_request`, `forage_request`, `tool_request`, `propose_wax` (`requests.py`): the requests a
  Warden sends the Queen when it needs something beyond its grant. `propose_wax` (roadmap step
  4.2a) builds a `CellWaxProposed` about the Warden's own Cell, `origin=BEE` and `proposer` the
  Warden's own id; a Drone (a Worker role) has no request path of its own here (a Worker never
  holds a Waggle link, codingrules section 4) and raises a caution up the chain through
  `hivemind.workers.tools.ask` today, since the Worker tool registry's `ctx` carries no
  fire-and-forget channel a `propose_wax` tool could use -- see `requests.py`'s own module
  docstring for what a future one would need.
- `wardens.autopilot`: `WardenAction`, `SubBeeView`, `decide` -- the deterministic dispatch table;
  never imports `hivemind.llm`.
- `wardens.awake`: `WardenDecision`, `decide_awake` -- one stateless episode on `ModelSlot.WARDEN`.
- `wardens.spawn`: `SubBee`, `WardenCellContext`, `spawn_sub_bee` -- starting a new sub-bee.
  Roadmap step 4.8's own wiring step: each sub-bee's own `call_gate` is a fresh Fanner lane from
  `WardenDeps.lane_for_grant(grant.grant_id, assignment.goal_id)`, so every `llm.call` it makes
  carries its own grant and goal id; `WardenDeps.call_gate` stays this Warden's own unattributed
  lane, used only for its own awake episodes. `spawn.audited_gate.AuditingCappingGate` (roadmap
  step 4.10) is the `CappingGate` `_build_capping_gate` actually builds: it samples a terminal
  proposal for after-the-fact judge review (`hivemind.supervision.capping.audit.sampler.audit_completed`)
  at tiers the table marks ungated in real time.
- `judge.py` (roadmap step 4.10): `ModelJudgeReviewer`, the model-backed `JudgeReviewer`
  (`hivemind.supervision.capping.checks.judge.JudgeReviewer`) a Warden's `CappingGate` calls
  through -- `complete_structured` on `ModelSlot.JUDGE`, a prompt built from the tier's rubric and
  the `JudgeRequest` alone (no proposer transcript, no hot state), through the Warden's own
  `CallGate`. `review` translates a `MalformedOutputError` (the ladder exhausted every rung and
  fallback binding on unparseable output) into `hivemind.supervision.capping.JudgeAnswerError`
  instead of letting it propagate; `ProviderUnavailableError`/`RateLimitedError` are left
  unchanged, since those are outages Clustering handles, not an answer failure.
- `wardens.inbox`: `to_inbox_item`, `warden_attendant` -- the Warden's own Attendant.
- `wardens.local_pool`: `SubBeeSlots` (renamed from `LocalPool` in roadmap step 4.7, since
  codingrules 6.1 now gives `LocalPool` to `hivemind.forage`) -- a bare sub-bee-slot counter
  against a grant -- and, roadmap step 8.3, `wardens.local_pool.hosting`: `ModelServerHost`, which
  starts the OpenAI-compatible model servers `WardenDeps.servers` (`[llm.servers]`) declares on a
  Cell that reports `can_host_model`, offers their sources to the Hive within the Queen's
  ceilings (`forage.source_added`) and stops them before the lease is released. `Warden.start()`
  builds the host once it holds its lease, `Warden.stop()` and `handle_release_lease` stop it, the
  heartbeat tick sweeps for a server that died, and a Queen-sent `forage.source_evict` (autopilot
  `WardenAction.EVICT_SOURCE`, `ticks.control.handle_source_evict`) withdraws a source only when
  this Warden exported its seats. `WardenDeps` gained `servers`, `register_provider` and
  `unregister_provider` for it. Roadmap step 11.8 (a Nuc, Level 2 of the runtime ladder): a
  Queen-sent `swarm.nuc_promote` (autopilot `WardenAction.NUC_PROMOTE`, `ticks.nuc`) starts the
  server the device's own manifest names, with the models the order lists, within the ceilings the
  Queen set (and fails closed without them), or stops every server on DEMOTE; the job answers with
  `swarm.nuc_promoted`. A device Warden that hosts a server (or has a job in flight) keeps its
  lease instead of giving it back when idle, and a model it loads afterwards, within the
  ceilings, is only a new source; one outside them is refused and recorded as a `guard.denied`.
- `wardens.ticks`: `assign`, `results`, `alarms`, `questions`, `control`, `heartbeat`, `honey` --
  one of `Warden`'s own tick handlers each, split out only to stay within codingrules 5.1's size
  limits -- and `dispatch`, whose `act` hands each decided `WardenAction` to the one that does it
  (the `_act` that used to live in `warden.py`; `warden.py`'s own `_act` now keeps only roadmap
  phase 10's isolation orders and the gates a spawn passes first, and calls `act` for everything
  else). `honey` (roadmap step 7.8) relays a sub-bee's `HoneyQuery`/`NectarDeposit` to the Queen
  once its payload names that sub-bee and its own task, remembers each forwarded query's asker
  (`HoneyRelay`, bounded) and relays the Queen's `HoneyResponse` back, correlated to the sub-bee's
  own envelope; a Queen `control.error` about a relayed deposit is logged. Roadmap step 10.6c:
  `heartbeat.raise_stalled_alarms` returns a stalled sub-bee's synthesised `WORKER_STALLED` Alarm
  as an inbox item, which `Warden` dispatches through the same `decide` and `_act` as any other
  Alarm, so a policy row naming QUARANTINE for it reaches the one path below; no tick module
  imports the quarantine package.
  `alarms.handle_alarm_action`/`rebind_sub_bee` also record `alarm.handled`/`alarm.escalated` on
  the Pheromone Trail (`hivemind.supervision.record_alarm_event`); `control.forward_control` turns
  a Queen-sent `Intervene(REBIND)` into a real respawn on the binding the Queen already resolved,
  rather than only relaying it to the sub-bee to checkpoint and stop.
- `wardens.quarantine` (roadmap step 10.6c, ADR-0043's "Quarantine is one intervention"): the
  one quarantine code path, `quarantine_bee(warden, order) -> QuarantineRecord | None`, and
  nothing composed by hand anywhere else. In order: authorise at the `quarantine` point, write the
  checkpoint (a Handoff the Warden composes, carrying the bee's last one forward when it can
  still read it), stop the bee (its role cancelled; a command it has in flight dies with it, since
  a Cell session's exec kills its child's tree when cancelled), withdraw its task's grant and any
  parked assignment, record `warden.intervened` (task, bee, action, suspect episode, checkpoint,
  grant, orderer), taint the bee's memory and its task's from the suspect episode on through
  `hivemind.memory.taint.taint_memory` (`TaintSource.QUARANTINE`, over the memory tables plus
  `WardenDeps.taint_ledgers`, where the Honey Store's Nectar ledger joins in phase 7), hold the
  task (`TaskProgress` at stage PAUSED, which the Queen turns into the Brood Chamber's PAUSED) and
  tell the Queen with a SECURITY Alarm of this Warden's own. Three ways in, one path:
  a Queen-sent `Intervene(QUARANTINE)` and this Warden's own `PolicyAction.QUARANTINE` row for an
  Alarm about its own sub-bee (both `WardenAction.QUARANTINE`, `carry_out`), and
  `Warden.intervene(child, Quarantine(...))` (`quarantine_child`). A repeated order changes
  nothing; a refused one changes nothing but its `guard.denied` row; a Warden's own row that
  cannot go ahead escalates the Alarm instead. `admit_respawn` (`gate.py`) is the only way out:
  a quarantined task's `TaskAssign` spawns only when it resumes from the quarantine's own
  checkpoint and `read_handoff` reads that checkpoint as CLEARED by a judge (`clear_taint`);
  any other respawn is refused at the `quarantine` point (`guard.scope.quarantine_checkpoint`),
  its grant dropped, and the Queen told again that the task is held.
- `wardens.isolation` (roadmap step 10.6a, ADR-0043's "Only the Queen isolates a Cell"): an
  isolated Cell's Warden carries out the Queen's `CellTaintOrder` (`taint.taint_own_memory`: the
  isolation setter over the store the Warden keeps, `TaintSource.ISOLATION`, the order's bees and
  tasks plus itself and the bees it runs now, from the order's instant, caused by her
  `cell.isolated`; an order for another Cell, or from anyone but the Queen, labels nothing), and
  `gate.admit_resume` stands in front of every `TaskAssign` after the quarantine gate: a resume
  from a Handoff the store labels tainted is refused at the `isolation` point
  (`guard.scope.tainted_handoff`), its grant dropped, the refusal shipped and the task reported
  held. The same gate stands in front of every respawn this Warden makes itself (a RETRY, a
  REBIND, a Queen-ordered rebind, a context Handoff's successor: `wardens.ticks.alarms.
  _spawn_into_slot`), so a tainted resume is refused on those paths too and its kept slot freed.
  `WardenAction.TAINT_MEMORY` is the order's autopilot action.
- `wardens.offline` (roadmap steps 11.6a, 11.9): what a Warden does when it is cut off, and how it
  catches the Queen up: `ReconnectingQueenLink` (a `Transport` that redials, re-announces, replays a
  durable outbox and ships the trail), the pure table that routes each send while the link is down,
  `Outage` (the tasks paused at a Handoff) and the freeze and the way back (`reconcile`); see its
  README.
- `wardens.watch` (roadmap step 11.10, ADR-0054): Watch mode. A Warden with no bees working, no
  assignment parked for its grant and a Cell link that is up takes a read-only observation of its
  Cell every `[supervision.watch] interval_s` (`observer`, through a `WatchProbe`: the Hive Stand's
  Warden looks through its own session, `probe.SessionWatchProbe`; a Level 0 device's asks its
  packet, `hivemind.swarm.PollenWatchProbe`), keeps it as `C2` Bee Bread for `retention_s`
  (`store`), and every `patrol_interval_s` runs a Patrol (`patrol`, over a `PatrolPort`; `bridge`
  is the real Warden's): one stateless awake episode (`wardens.awake.decide_patrol`, helped by the
  pure pre-filter in `signals` and the framing and quoted facts in `digest`) that decides exactly
  one of an Alarm (`PATROL_ANOMALY`), a Nectar summary, a Cell Wax proposal or nothing worth
  keeping. `tick.watch_tick` is the one call `Warden._run_tick` makes, after the state has settled,
  and `deps.WatchDeps` is the one optional field `WardenDeps` carries for all of it. The package
  face imports no Warden and no model machinery, because `wardens.deps` imports it; `observer`,
  `patrol`, `bridge` and `tick` are imported by their module paths.

## Enforcement points (roadmap step 10.3)

`WardenDeps` carries the Guard's `enforcer`, the `lease_capability` its composition root named
(`cell:hive_stand` for the Hive Stand's Warden, `cell:virtual` in a Virtual Cell; never read off a
Cell's kind) and the `[llm.slots]` rows (`bindings`) a binding key resolves against.

- `lease_creation` (`ticks.lease.open_lease`, `Warden.start`'s delegate): the lease capability must
  be allowed by the `warden` role; a refusal is `guard.denied` and WATCH, and nothing is leased.
- `slot_binding` (`spawn.binding.authorize_binding`): every binding -- a sub-bee's first, the
  Warden's own REBIND, and a Queen-sent `Intervene(REBIND)` (never checked before) -- must name a
  slot the grant allows and the sub-bee's set holds as `llm:<slot>`; a named binding resolves to
  the slot whose fallback chain names it. A refused spawn reports the task FAILED; a landed
  rebind records `llm.rebound`. Roadmap step 10.3a/b: the check is bound to the Cell's tier and
  states whether every provider the key's fallback chain reaches serves locally
  (`WardenDeps.local_providers`), so a Night Veil task can never be rebound to a hosted slot.
- `question_routing` (`ticks.questions`): a sub-bee's Question goes up only when its set holds
  `question:human`; otherwise the Warden answers it back down with the Guard's reason.
- `quarantine` (`quarantine.authority`, roadmap step 10.6c): whoever orders a quarantine (the
  Queen, as her role set; this Warden itself, as its own) must hold the Cell's lease capability,
  and the order must name a current sub-bee (`guard.scope.sub_bee`); a respawn of a quarantined
  task that is not the one way out is refused here too (`guard.scope.quarantine_checkpoint`).
- A sub-bee's slice (`spawn.attenuate`) now reads the task's `network_scopes` (Waggle 1.6, so a
  Worker can hold `net` at all) and the goal's set (Waggle 1.8) off its `TaskAssign`, and keeps a
  candidate only where the Warden's set and the goal's both allow it.

## A Swarm device's Warden (roadmap step 11.6)

A device the Hive borrows runs its Warden at Level 0 of the runtime ladder: on the Hive Stand, in
the Queen's process, with its hands on the device through `PollenSession`. `WardenDeps.device`
(`DeviceDeps`, `deps.py`) is the one optional field that says so; the Hive Stand's and a Virtual
Cell's Warden leave it `None` and behave exactly as before.

- Release when idle (`ticks.lease.release_idle_lease`, called last by `ticks.assign.
  settle_after_tick`): a Warden that has settled to WATCH with no sub-bee and no assignment parked
  for a grant gives its lease back, so a borrowed machine keeps no Hive scratch between tasks. The
  next `TaskAssign` finds no lease and `handle_assign` leases again (`Warden.start`). A release the
  device cannot confirm leaves the lease ORPHANED and held, retried on the next tick and by `stop()`.
- A lost link (`Warden.cell_lost` / `Warden.cell_found`, `ticks.lease.report_cell_lost`): the
  swarm gate calls `cell_lost` when the device drops. The Warden raises one `CELL_UNREACHABLE`
  Alarm per loss through the ordinary Alarm path (it keeps heartbeating on the Hive Stand, so the
  Queen's liveness would never notice) and holds off the idle release until `cell_found`. With an
  `Outage` in `DeviceDeps` (roadmap 11.9) it also pauses its running sub-bees at a Handoff, so their
  commands stop failing against a Cell that is not there, and when the device is back it gives the
  old lease back, leases again and resumes them from their Handoffs (`wardens.offline`). What else
  becomes of the tasks on the device is the Queen's.
- A session that cannot open right after the lease did (`open_lease`) gives the lease back and
  waits in WATCH, like a refused lease does, rather than ending the run loop.
- A device error while the Warden checks a claim (`ticks.results`, `DEVICE_ERRORS`: a closed
  session, a request the device refused, a link that dropped) never ends its loop: nothing was
  verified, so the task is reported FAILED with the error's class, and the Warden carries on.
  `Warden.stop()` records a lease the Cell cannot release on `warden.stopped` instead of raising.
- A Warden that dials the Queen from the device itself (Level 1, `cli.in_cell` in borrowed mode)
  carries `DeviceDeps.remote` too and goes OFFLINE when its reconnecting link is lost.

## How to test this

Every module is tested against fakes: `hivemind.cell.fake.FakeCellSource`, `waggle.transport.
memory.MemoryTransport.pair`, `hivemind.memory.InMemoryMemoryStore`, `hivemind.pheromone.trail.
memory.MemoryPheromoneTrail`, `waggle.clock.FakeClock`, the shipped `docs/supervision/
default-policy.toml` and `capping-tiers.toml`, and a scriptable `hivemind.llm.FakeLLMProvider` on
`ModelSlot.WARDEN`. `tests/builders/wardens.py`'s `make_warden_deps` builds all of that in one
call; `QueenEnd` wraps the Queen side of the link the same way `tests/builders/workers.py`'s
`WardenEnd` wraps a Warden's own link to a sub-bee, so a test drives a real `Warden` end to end
over real (in-memory) Waggle links, never by calling its private methods directly.

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens -q
```
