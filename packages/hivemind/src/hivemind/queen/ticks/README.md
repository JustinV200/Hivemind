# hivemind.queen.ticks

The ticks package holds `Queen`'s own tick handlers, split out only so `queen.py` and its `Queen`
class stay within codingrules 5.1's size limits.

## Public API (roadmap steps 3.20, 4.7)

- `alarms.handle_alarm`: `REBIND`/`ESCALATE_TO_HUMAN`/`RETRY_TASK`/`FAIL_TASK` for an escalated
  Alarm; `REBIND` is sent as a real `hivemind.supervision.intervention.Rebind`, converted through
  the same `to_wire` machinery `Queen.intervene` uses.
- `liveness.WardenLiveness`, `.record_heartbeat`, `.check_liveness`: track each attached Warden's
  own pulse, and mark one offline (raising an Alarm at the human) after it misses
  `heartbeat_miss_limit` heartbeats. `.renew_grants_on_heartbeat` extends every live grant a
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

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/ticks -q
```
