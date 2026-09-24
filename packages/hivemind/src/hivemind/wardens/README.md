# hivemind.wardens

The wardens package is the Warden: the per-Cell supervisor that spawns and supervises Workers on
exactly one Cell. It covers Warden state, its inbox, its Autopilot (which never awaits a model)
and Awake modes, spawning, its local model pool, offline handling and read-only Watch mode; a
Warden never provisions Cells itself.

## Public API (roadmap steps 3.18, 3.19)

- `Warden` (`warden.py`): the `waggle.loop.TickLoop` face. `start()` leases the Warden's Cell (or
  moves to `WATCH` on a refusal); `stop()` sets the stop flag first, then reaps its own heartbeat
  deadline, stops every sub-bee (cooperatively, via `wardens.spawn.stop_sub_bee`, falling back to
  a bounded cancel), reaps every receive task it owns and releases the lease -- never returning
  with a task it started still pending (codingrules section 11). One tick drains the queen link
  and every sub-bee link into `InboxItem`s, orders them with this Warden's own `Attendant`, dispatches each
  through `wardens.autopilot.decide` (falling back to `wardens.awake.decide_awake` for
  `NEEDS_JUDGEMENT`), and sends a `Heartbeat` once the interval elapses -- a send that finds the
  Queen link already closed or dropped is recoverable (`wardens.ticks.heartbeat.send_heartbeat`
  records `warden.offline` and moves on), never an exception out of this Warden's own loop or
  `stop()`: every Warden -> Queen and Warden -> sub-bee send in this package goes through
  `wardens.links.send_guarded` (phase-7 handoff open item 8), not `Warden.stop()` alone.
  Also implements `hivemind.supervision.Supervisor` over its sub-bees. Roadmap step 4.9
  (Clustering): `_settle_after_tick` also settles `ACTIVE <-> CLUSTERED` -- every current sub-bee's
  own task in `warden._clustered_tasks` (populated from a Queen-sent `Intervene(HANDOFF)`/
  `TaskResume`, `hivemind.wardens.state.clustering_update`) moves it to `CLUSTERED`; any one no
  longer in that set moves it back, recording `warden.clustered`/`warden.active`
  (`state.SETTLED_EVENT_KINDS`) each time. Roadmap step 4.8's own wiring step: the RECORD
  handler (`wardens.ticks.dispatch`'s own `_record_routine`) also handles a Queen-sent
  `CeilingsSet`/`PlanWritten`, storing them as `_ceilings`/
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
- `wardens.ticks`: `assign`, `results`, `alarms`, `questions`, `control`, `heartbeat`, `honey` --
  one of `Warden`'s own tick handlers each, split out only to stay within codingrules 5.1's size
  limits -- and `dispatch`, whose `act` hands each decided `WardenAction` to the one that does it
  (the `_act` that used to live in `warden.py`). `honey` (roadmap step 7.8) relays a sub-bee's
  `HoneyQuery`/`NectarDeposit` to the Queen once its payload names that sub-bee and its own task,
  remembers each forwarded query's asker (`HoneyRelay`, bounded) and relays the Queen's
  `HoneyResponse` back, correlated to the sub-bee's own envelope; a Queen `control.error` about a
  relayed deposit is logged.
  `alarms.handle_alarm_action`/`rebind_sub_bee` also record `alarm.handled`/`alarm.escalated` on
  the Pheromone Trail (`hivemind.supervision.record_alarm_event`); `control.forward_control` turns
  a Queen-sent `Intervene(REBIND)` into a real respawn on the binding the Queen already resolved,
  rather than only relaying it to the sub-bee to checkpoint and stop.
- `wardens.offline`, `wardens.watch`: placeholders; populated in phase 11.

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
