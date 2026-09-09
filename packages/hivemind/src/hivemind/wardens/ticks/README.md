# hivemind.wardens.ticks

The ticks package holds `Warden`'s own tick handlers: what each `WardenAction` actually does,
split out of `warden.py` only to stay within codingrules 5.1's size limits (Warden's `_act`
dispatch calls into these; none of them is a general-purpose module on its own).

## Public API (roadmap step 3.19)

- `assign`: spawn a sub-bee once its `TaskAssign` and `GrantIssued` have both arrived; park
  otherwise; retry a refused lease once before escalating `CELL_UNREACHABLE`.
- `results`: run acceptance on a sub-bee's claimed `TaskResult`, on the Warden's own session.
- `alarms`: `RETRY`/`REBIND`/`ESCALATE`/`CANCEL_TASK` for an Alarm; `send_alarm_to_queen` for a
  Warden's own self-raised Alarm; `retire_sub_bee`, shared with `results`.
- `questions`: forward a sub-bee's `Question` to the Queen and the Queen's `Answer` back.
- `control`: forward `TaskCancel`/`TaskPause`/`TaskResume`/`Intervene` from the Queen unchanged.
- `heartbeat`: send the Warden's own `Heartbeat`, mirror a sub-bee's reports, watch for a stall,
  and build the `HotStateSources` an awake episode reads.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens -q
```
