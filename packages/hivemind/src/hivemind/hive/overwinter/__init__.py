"""The Overwintering pool: keep a bounded set of paused Virtual Cells around for fast reuse.

Roadmap step 5.9 (ADR-0029): `policy.decide_release` is the pure rule from a released Virtual
Cell's own facts, the pool's current occupancy and the manifest's `[virtual_cells.overwinter]`
bounds to `OVERWINTER` or `TEARDOWN`; `pool.OverwinterPool` is the effectful half that scrubs,
pauses, resumes, evicts and lists what the pool actually holds. Night Veil Cells are excluded
outright, at both layers: the policy's own first rule, and `hivemind.hive.cell_state.
assert_dormant_allowed` as a second guard inside `OverwinterPool.admit` itself.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by `hivemind.hive.lifecycle`
    (roadmap step 5.6, a concurrent dispatch) on every Virtual Cell release and placement's own
    `ReuseDormant` outcome, and by `hivemind.workers.roles.undertaker.sweep.sweep_orphans` (dormant
    eviction). Calls into hivemind.cell, hivemind.hive.backends, hivemind.hive.cell_state,
    hivemind.hive.errors, hivemind.hive.models, hivemind.manifest.schema.placement,
    hivemind.pheromone and waggle.

Key invariants:
    - `policy.decide_release` performs no I/O (codingrules section 8.3); every backend call and
      every trail write happens in `pool.OverwinterPool` instead.
    - A `CombShieldLevel.NIGHT_VEIL` Cell can never reach `OVERWINTER`
      (`policy._never_night_veil`) or `VirtualCellStatus.DORMANT`
      (`hivemind.hive.cell_state.assert_dormant_allowed`, called by `OverwinterPool.admit`).

See Also:
    - docs/adr/0029-overwintering-policy.md for the design this package implements.
    - .claude/roadmap.md step 5.9 for this package's own build instructions.
    - hivemind.workers.roles.undertaker for the sweep that evicts expired dormant Cells.

Public API:
    - OverwinterDecision, ReleaseOutcome, PoolView, OverwinterConfig, ReleaseDecision,
      decide_release: the pure Overwintering rule (hivemind.hive.overwinter.policy).
    - DormantCell, PooledCandidate, Scrubber, OverwinterPool: bookkeeping and selection for
      dormant Cells; every backend call and trail write these used to make now lives on
      hivemind.hive.lifecycle.CellLifecycle instead (hivemind.hive.overwinter.pool).
"""

from hivemind.hive.overwinter.policy import (
    OverwinterConfig,
    OverwinterDecision,
    PoolView,
    ReleaseDecision,
    ReleaseOutcome,
    decide_release,
)
from hivemind.hive.overwinter.pool import DormantCell, OverwinterPool, PooledCandidate, Scrubber

__all__ = [
    "DormantCell",
    "OverwinterConfig",
    "OverwinterDecision",
    "OverwinterPool",
    "PoolView",
    "PooledCandidate",
    "ReleaseDecision",
    "ReleaseOutcome",
    "Scrubber",
    "decide_release",
]
