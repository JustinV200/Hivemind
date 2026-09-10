# hivemind.queen.ticks

The ticks package holds `Queen`'s own tick handlers, split out only so `queen.py` and its `Queen`
class stay within codingrules 5.1's size limits.

## Public API (roadmap step 3.20)

- `alarms.handle_alarm`: `REBIND`/`ESCALATE_TO_HUMAN`/`RETRY_TASK`/`FAIL_TASK` for an escalated
  Alarm; `REBIND` is sent as a real `hivemind.supervision.intervention.Rebind`, converted through
  the same `to_wire` machinery `Queen.intervene` uses.
- `liveness.WardenLiveness`, `.record_heartbeat`, `.check_liveness`: track each attached Warden's
  own pulse, and mark one offline (raising an Alarm at the human) after it misses
  `heartbeat_miss_limit` heartbeats.
- `results.complete_task`, `.retry_task`, `.fail_task`: `COMPLETE_TASK`/`RETRY_TASK`/`FAIL_TASK`,
  shared by a `TaskResult` and an escalated Alarm alike.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/ticks -q
```
