# hivemind.queen.ticks

The ticks package holds `Queen`'s own tick handlers, split out only so `queen.py` and its `Queen`
class stay within codingrules 5.1's size limits.

## Public API (roadmap steps 3.20, 4.7, 4.2a, 7.8)

- `alarms.handle_alarm`: `REBIND`/`ESCALATE_TO_HUMAN`/`RETRY_TASK`/`FAIL_TASK` for an escalated
  Alarm; `REBIND` is sent as a real `hivemind.supervision.intervention.Rebind`, converted through
  the same `to_wire` machinery `Queen.intervene` uses. `FAIL_TASK` also sends the Alarm's Warden a
  `TaskCancel` (bounded by `CANCEL_SEND_TIMEOUT_S`), so a task failed for good keeps no bee.
- `liveness.WardenLiveness`, `.record_heartbeat`, `.check_liveness`: track each attached Warden's
  own pulse, and mark one offline (raising an Alarm at the human) after it misses
  `heartbeat_miss_limit` heartbeats of its own cadence -- the interval its newest Heartbeat
  declared (`Heartbeat.interval_s`: 15 s for a Virtual Cell's in-Cell Warden, whatever a Swarm
  device keeps), never less than the manifest's `heartbeat_interval_s`; a recorded Heartbeat is
  then handed to
  `QueenDeps.on_heartbeat` when one is set (the Hive Entrance's telemetry board). A Warden whose
  Cell the Hive itself holds paused -- Overwintered, read from the lifecycle's own dormant list
  (`QueenDeps.dormant_cell_source`, or the static `dormant_cells`) -- is never judged while the
  hold lasts, and gets a whole window from the moment it resumes; a snapshot's shorter freeze is
  announced by the Warden itself, as a longer declared interval. `.renew_grants_on_heartbeat` extends every live grant a
  Heartbeat's own Warden holds; `.check_liveness`'s own sweep also calls
  `hivemind.queen.forage.grants.sweep_expired` every tick, unconditionally, so a grant whose lease
  lapses returns to the pool the same tick it expires. `.handle_infrastructure_item` is the one
  entry point `hivemind.queen.queen.Queen`'s tick calls for a Heartbeat or a ForageRequest, ahead
  of `hivemind.queen.autopilot.table.decide` (whose own fallback for an unrecognised payload is
  `NEEDS_JUDGEMENT`, exactly what neither of these two should reach).
- `forage.handle_forage_request`, `.handle_forage_request_for_item`: answer one Warden's
  `ForageRequest` -- a fresh `GrantIssued` plus `ForageReply(GRANTED)` on a grant within headroom,
  `ForageReply(DENIED)` otherwise (contested or not) -- and record `forage.requested` and
  `forage.granted`/`forage.denied` on the trail.
- `results.complete_task`, `.retry_task`, `.fail_task`: `COMPLETE_TASK`/`RETRY_TASK`/`FAIL_TASK`,
  shared by a `TaskResult` and an escalated Alarm alike. `complete_task` carries a `TaskResult`'s
  `scout_report` onto the SUCCEEDED outcome unchanged (roadmap step 6.10); `fail_task` takes an
  optional `scout_report` for the FAILED outcome, `fail_reason` builds the infeasible-Scout
  wording (or echoes the result's own reason otherwise), and `fail_task_from_result` is the one
  call `hivemind.queen.queen._act_on_task_result` makes for a `TaskResult`-shaped `FAIL_TASK`,
  wiring both together. For an infeasible Scout it also cancels every task that depends on the
  Scout, directly or not, with the Scout's reason ("Held back by Scout <id>. ..."), so the goal
  ends instead of waiting on tasks that can never become ready (a `hive run` would otherwise sit
  out its whole timeout). Any other failed task's dependents stay PENDING, as before.
- `honey.handle_honey_item` (roadmap step 7.8): the third entry point `handle_infrastructure_item`
  reaches ahead of `decide`. A `NectarDeposit` chunk goes to the Honey Store's intake with the
  sending Warden's own Cell record as its source (a refusal answers `control.error` with the
  refusal's stable code); a `HoneyQuery` is answered with a `HoneyResponse` on the same link,
  searched as the asking bee (a Worker's task, goal, Cell and itself; a Warden's Cell and itself)
  under its task's clearance and its Cell's Comb Shield tier. With no Honey Store wired
  (`QueenDeps.honey is None`) a query is answered empty with that reason and a deposit is refused.
- `wax.handle_wax_item`, `.handle_wax_proposed` (roadmap step 4.2a): the other entry point
  `handle_infrastructure_item` reaches ahead of `decide`, for a `CellWaxProposed`. Records the
  proposal (`hivemind.memory.cell_wax.propose_wax`), judges it with
  `hivemind.queen.autopilot.wax.decide_wax_proposal`, writes it at once within the per-Cell cap
  (`decided_by=AUTOPILOT`, no awake episode) or runs one awake episode with
  `cells_in_play = {the Cell in question}` and applies its `WRITE_WAX`/`REJECT_WAX` decision
  (anything else, including `CLEAR_WAX`, falls back to a rejection). Sends a `CellWaxWritten` back
  to the proposing Warden's own link once written, when it is still attached.
- `guard_bee.run_guard_bee` (roadmap step 10.6): one Guard Bee round on the Queen's tick, called
  by `housekeeping.run_housekeeping` beside the House Bee's sweep, when `QueenDeps.guard_bee` is
  set (the Guard Bee decides whether its interval is due). A failed round is one `GuardBeeError`,
  logged, and the tick carries on. A package of one module, because this directory already holds
  the ten modules codingrules 5.6 allows.
- `human.chat`, `human.intake` (roadmap step 10.5, ADR-0040): the human end of the tick. `chat`
  drains the human's waiting messages into the inbox (`human_items`), writes a `REPLY` decision's
  words back (`reply`) and stamps each message handled once decided (`mark_handled`); `intake`
  settles, holds or plans every durable goal request, one plan at a time beside the tick
  (`drain_goal_requests`, `stop_planning`). A sub-package, for the same ten-module limit.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/ticks -q
```
