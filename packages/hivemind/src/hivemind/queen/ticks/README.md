# hivemind.queen.ticks

The ticks package holds `Queen`'s own tick handlers, split out only so `queen.py` and its `Queen`
class stay within codingrules 5.1's size limits.

## Public API (roadmap steps 3.20, 4.7, 4.2a)

- `alarms.handle_alarm`: `REBIND`/`ESCALATE_TO_HUMAN`/`RETRY_TASK`/`FAIL_TASK` for an escalated
  Alarm; `REBIND` is sent as a real `hivemind.supervision.intervention.Rebind`, converted through
  the same `to_wire` machinery `Queen.intervene` uses.
- `liveness.WardenLiveness`, `.record_heartbeat`, `.check_liveness`: track each attached Warden's
  own pulse, and mark one offline (raising an Alarm at the human) after it misses
  `heartbeat_miss_limit` heartbeats; a recorded Heartbeat is then handed to
  `QueenDeps.on_heartbeat` when one is set (the Hive Entrance's telemetry board). `.renew_grants_on_heartbeat` extends every live grant a
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
  shared by a `TaskResult` and an escalated Alarm alike.
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

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/ticks -q
```
