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
- `pool.py` -- `OverwinterPool(backend, clock, config, trail, identity)`: the effectful half.
  `admit(cell, spec, *, scrub)` scrubs (an injected `Scrubber`) and pauses a released Cell,
  recording it dormant; `claim(image)` resumes the oldest dormant Cell for that image; `evict_
  expired(now)` destroys every Cell past its own `dormant_until` (the Undertaker's sweep calls
  this); `view()` reports occupancy for `decide_release`'s own `PoolView`; `dormant_candidates()`
  reports every dormant Cell as a `PooledCandidate` -- not `hivemind.queen.placement.inventory.
  DormantCandidate` directly, since `hive` (Layer 3) may never import `queen` (Layer 6); a Layer-6
  caller converts one row at a time. `admit_all_idle(...)` is the hook a long Clustering outage
  calls in a batch; wiring a trigger for it in `queen/cluster/` belongs to a different dispatch.

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/hive/overwinter
```

`tests/unit/hive/overwinter/` mirrors this package module for module, using
`hivemind.hive.backends.fake.FakeCellBackend` and a fake `Scrubber`/`PheromoneTrail` for the pool,
and `hypothesis` for the Night Veil rule (`decide_release` never returns `OVERWINTER` for a
NIGHT_VEIL Cell, whatever else holds).
