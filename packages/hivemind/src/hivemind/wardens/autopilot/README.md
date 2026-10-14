# hivemind.wardens.autopilot

The autopilot package is the Warden's Autopilot: deterministic fallback behaviour that never
awaits a model, so the Hive keeps working when every provider is down. Nothing under this
package may import hivemind.llm.

## Public API (roadmap step 3.19)

- `WardenAction` (`actions.py`): the closed set of moves the dispatch table can pick.
- `SubBeeView`, `decide` (`table.py`): `decide(item, sub_bee, policy)` maps one already-ordered
  `InboxItem` to exactly one `WardenAction`; an `AlarmRaised` goes through `hivemind.supervision.
  policy.decide`, keyed on the Warden's own per-task attempt count (`SubBeeView.attempt`), not
  the wire `attempts` field (see `table.py`'s own docstring for why). Roadmap step 7.8: a
  `HoneyQuery`, `NectarDeposit` or `HoneyResponse`, and a `control.error` whose `failed_kind` is
  `honey.nectar_deposit`, map to `FORWARD_HONEY` -- relayed or logged, never a model call.
  Roadmap step 11.8: a Queen-sent `swarm.nuc_promote` maps to `NUC_PROMOTE` -- the device's
  model server starts or stops as ordered, never a judgement call.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/autopilot -q
uv run --frozen lint-imports  # proves this package never reaches hivemind.llm
```
