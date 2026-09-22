# hivemind.hive.overwinter

The overwinter package decides which idle Virtual Cells to keep dormant (rather than destroy) so
the next task with the same image starts in seconds; Night Veil Cells are never eligible.

## Modules

- `policy.py` -- `decide_release(cell, spec, outcome, pool, config) -> ReleaseDecision`: a pure
  function (roadmap step 5.9, ADR-0029) from one released Virtual Cell's own facts
  (`ReleaseOutcome`), the pool's current occupancy (`PoolView`) and the manifest's
  `[virtual_cells.overwinter]` bounds (`OverwinterConfig`, built from `hivemind.manifest.schema.
  placement.VirtualCellsOverwinterSection`) to `OVERWINTER` or `TEARDOWN`, with a reason. Every
  rule (Night Veil, single-use, whole-Cell rollback, BLOCK wax, backend can pause, room per image,
  room in total, disk budget) is its own small function in `_RULES`, walked in order; the first
  veto wins.
- `pool.py` -- `OverwinterPool(clock, config)`: bookkeeping and selection only. `admit(cell, spec,
  *, scrub)` scrubs (an injected `Scrubber`) and records a released Cell dormant, but does not
  pause it; `claim(image)`/`claim_by_id(cell_id)` select and remove a dormant entry, but do not
  resume it; `evict_expired(now)` returns the ids past their own `dormant_until`, but does not
  destroy them; `view()` reports occupancy for `decide_release`'s own `PoolView`;
  `dormant_candidates()` reports every dormant Cell as a `PooledCandidate` -- not
  `hivemind.queen.placement.inventory.DormantCandidate` directly, since `hive` (Layer 3) may never
  import `queen` (Layer 6); a Layer-6 caller converts one row at a time. `admit_all_idle(...)` is
  the hook a long Clustering outage calls in a batch; wiring a trigger for it in `queen/cluster/`
  belongs to a different dispatch. Every actual backend call (`pause`/`resume`/`destroy`) and
  every `cell.*` trail event now lives on `hivemind.hive.lifecycle.CellLifecycle`, which calls into
  this pool immediately around each one -- see that module's own docstring for the reconciliation.

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/hive/overwinter
```

`tests/unit/hive/overwinter/` mirrors this package module for module, using
`hivemind.hive.backends.fake.FakeCellBackend` and a fake `Scrubber`/`PheromoneTrail` for the pool,
and `hypothesis` for the Night Veil rule (`decide_release` never returns `OVERWINTER` for a
NIGHT_VEIL Cell, whatever else holds).
