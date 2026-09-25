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
  stop-handoff, or FAILED once a cancel has reached it). One tick drains the queen link
  and every sub-bee link into `InboxItem`s, orders them with this Warden's own `Attendant`, dispatches each
  through `wardens.autopilot.decide` (falling back to `wardens.awake.decide_awake` for
  `NEEDS_JUDGEMENT`), and sends a `Heartbeat` once the interval elapses -- a send that finds the
  Queen link already closed is recoverable (`wardens.ticks.heartbeat.send_heartbeat` records
  `warden.offline` and moves on), never an exception out of this Warden's own loop or `stop()`.
  Also implements `hivemind.supervision.Supervisor` over its sub-bees. Roadmap step 4.9
  (Clustering): `_settle_after_tick` also settles `ACTIVE <-> CLUSTERED` -- every current sub-bee's
  own task in `warden._clustered_tasks` (populated from a Queen-sent `Intervene(HANDOFF)`/
  `TaskResume`, `hivemind.wardens.state.clustering_update`) moves it to `CLUSTERED`; any one no
  longer in that set (a `TaskResume`, or the Queen's fresh `TaskAssign` resuming the task) moves
  it back, recording
  `warden.clustered`/`warden.active`
  (`state.SETTLED_EVENT_KINDS`) each time. Roadmap step 4.8's own wiring step: `_record_routine`
  also handles a Queen-sent `CeilingsSet`/`PlanWritten`, storing them as `_ceilings`/
  `_hosting_plan` (`wardens.ticks.control.handle_ceilings_set`/`.handle_plan_written`) with no
  fresh trail event -- the Queen already recorded `forage.ceilings_set`/`forage.plan_written`
  before sending either.
- `WardenState`, `TRANSITIONS`, `assert_transition`, `can_transition`, `is_terminal`
  (`state.py`): the one Warden state machine (Appendix C). `settled_state`, `clustering_update`
  (roadmap step 4.9): the pure ACTIVE/WATCH/CLUSTERED decision and clustered-task-set update
  `warden.py`'s own `_settle_after_tick`/`_act` call, split out here to stay within codingrules
  5.1's file-size limit.
- `WardenDeps` (`deps.py`): every collaborator one Warden is built with.
- `AcceptanceReport`, `run_acceptance` (`acceptance.py`): the Warden-side half of a task's
  acceptance criteria (roadmap 3.18) -- run on the Warden's own session, never the sub-bee's.
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
  proposal for after-the-fact judge review (`hivemind.supervision.capping.audit.audit_completed`)
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
  against a grant.
- `wardens.ticks`: `assign`, `results`, `alarms`, `questions`, `control`, `heartbeat` -- one of
  `Warden`'s own tick handlers each, split out only to stay within codingrules 5.1's size limits.
  Roadmap step 10.6c: `heartbeat.raise_stalled_alarms` returns a stalled sub-bee's synthesised
  `WORKER_STALLED` Alarm as an inbox item, which `Warden` dispatches through the same `decide`
  and `_act` as any other Alarm, so a policy row naming QUARANTINE for it reaches the one path
  below; no tick module imports the quarantine package.
  `alarms.handle_alarm_action`/`rebind_sub_bee` also record `alarm.handled`/`alarm.escalated` on
  the Pheromone Trail (`hivemind.supervision.record_alarm_event`); `control.forward_control` turns
  a Queen-sent `Intervene(REBIND)` into a real respawn on the binding the Queen already resolved,
  rather than only relaying it to the sub-bee to checkpoint and stop.
- `wardens.quarantine` (roadmap step 10.6c, ADR-0035's "Quarantine is one intervention"): the
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
- `wardens.isolation` (roadmap step 10.6a, ADR-0035's "Only the Queen isolates a Cell"): an
  isolated Cell's Warden carries out the Queen's `CellTaintOrder` (`taint.taint_own_memory`: the
  isolation setter over the store the Warden keeps, `TaintSource.ISOLATION`, the order's bees and
  tasks plus itself and the bees it runs now, from the order's instant, caused by her
  `cell.isolated`; an order for another Cell, or from anyone but the Queen, labels nothing), and
  `gate.admit_resume` stands in front of every `TaskAssign` after the quarantine gate: a resume
  from a Handoff the store labels tainted is refused at the `isolation` point
  (`guard.scope.tainted_handoff`), its grant dropped, the refusal shipped and the task reported
  held. `WardenAction.TAINT_MEMORY` is the order's autopilot action.
- `wardens.offline`, `wardens.watch`: placeholders; populated in phase 11.

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
- A sub-bee's slice (`spawn.attenuate`) now reads the task's `network_scopes` (so a Worker can hold
  `net` at all) and the goal's set off Waggle 1.6's `TaskAssign`, and keeps a candidate only where
  the Warden's set and the goal's both allow it.

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
