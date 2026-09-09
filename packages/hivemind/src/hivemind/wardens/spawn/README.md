# hivemind.wardens.spawn

The spawn package is how a Warden starts a new Worker on its Cell and hands it the CellSession
it needs.

## Public API (roadmap step 3.19)

- `SubBee` (`sub_bee.py`): the Warden's own bookkeeping row for one spawned Worker -- its
  mirrored `WorkerState`, its current binding, its last Handoff and its owned runtime task/link.
- `WardenCellContext`, `spawn_sub_bee` (`spawn.py`): attenuate capabilities, carve a grant slice,
  resolve the assignment's slot to a live model, build the sub-bee's own `CappingGate` and
  `WorkerContext`, start its `WorkerRuntime`, and send it its first `TaskAssign`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/spawn -q
```
