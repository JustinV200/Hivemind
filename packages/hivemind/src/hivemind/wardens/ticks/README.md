# hivemind.wardens.ticks

The ticks package holds `Warden`'s own tick handlers: what each `WardenAction` actually does,
split out of `warden.py` only to stay within codingrules 5.1's size limits (Warden's `_act`
dispatch calls into these; none of them is a general-purpose module on its own).

## Public API (roadmap step 3.19)

- `assign`: spawn a sub-bee once its `TaskAssign` and `GrantIssued` have both arrived; park
  otherwise; retry a refused lease once before escalating `CELL_UNREACHABLE`.
- `results`: run acceptance on a sub-bee's claimed `TaskResult`, on the Warden's own session.
- `trail_ship`: ship this Warden's own trail segment right before a `TaskResult` goes to the
  Queen, so a Cell paused or destroyed on that result never takes the task's rows with it;
  called by `results` and `alarms`.
- `alarms`: `RETRY`/`REBIND`/`ESCALATE`/`CANCEL_TASK` for an Alarm; `send_alarm_to_queen` for a
  Warden's own self-raised Alarm; `retire_sub_bee`, the one way a sub-bee leaves its Warden (its
  runtime stopped, its link closed, its slot freed), shared with `results`, `control`,
  `heartbeat`, `Warden.stop` and the quarantine path.
- `lease` (roadmap step 10.3): `open_lease`, `Warden.start`'s delegate, behind the
  `lease_creation` point.
- `questions`: forward a sub-bee's `Question` to the Queen and the Queen's `Answer` back; since
  roadmap step 10.3 only when the sub-bee holds `question:human` (the `question_routing` point).
- `control`: forward `TaskCancel`/`TaskPause`/`TaskResume`/`Intervene` from the Queen unchanged;
  a cancel marks the bee's row so any terminal state it reports next ends it, a cancel for a bee
  that has already ended retires it at once, and `RELEASE_LEASE` retires every sub-bee.
- `heartbeat`: send the Warden's own `Heartbeat`, mirror a sub-bee's reports (retiring one whose
  Heartbeat says it has ended with nothing more to send), watch for a stall,
  and build the `HotStateSources` an awake episode reads -- including `wax(cells)` (roadmap step
  4.2a), WRITTEN Cell Wax for the given Cells, capped per Cell the same way
  `hivemind.queen.awake.episode.QueenSources.wax` is.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens -q
```
