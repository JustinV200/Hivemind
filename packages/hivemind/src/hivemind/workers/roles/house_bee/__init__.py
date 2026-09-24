"""Implement HouseBee: the maintenance role that sweeps memory down its tiers and ripens Honey.

A House Bee keeps memory from growing without bound. Its **sweep** (roadmap step 4.3) first
demotes whatever `hivemind.memory.should_demote` says has left hot state's own window into Bee
Bread (the warm tier), expires every WRITTEN Cell Wax note (a Queen-written caution about one
Cell) whose own `expires_at` has passed (`hivemind.memory.cell_wax.expire_wax`, roadmap step
4.2a's own named wax-expiry hook), then folds Bee Bread entries older than that same window, for
closed tasks, into new `SUMMARY` entries through `hivemind.memory.compact` -- never from a previous
summary (docs/adr/0022, "one level"). A fourth phase (roadmap steps 7.6 and 7.9a) deposits aged
Bee Bread and cleared or expired Cell Wax into the Honey Store (the cold tier, the Hive's
searchable knowledge base) as Nectar, its raw material. Its **ripening loop** (`HouseBeeRipening`)
runs beside the Queen, never inside her tick, and turns that Nectar into Honey on the ripener and
embedder slots, draining the operator's proposed notes first; after ripening, the same pass files
judge-reviewed label lowering proposals for Hive Stand Nectar the Ripener read as less sensitive
than its C2 floor, and asks the clearance judge on the JUDGE slot about waiting ones (ADR-0034:
the House Bee proposes and asks, it never lowers a label itself). This package is split by
responsibility (codingrules section 5.2): `sweep.py` holds `run_sweep` and the pure
counts/bundles it works over (`SweepDeps`/`SweepWindow`/`SweepOutcome`), decoupled from the Worker
protocol so the Queen's own housekeeping tick calls it directly; `honey.py` holds the sweep's two
Honey deposit duties and the Cell records they are attributed through; `loop.py` holds
`HouseBeeRipening`; `schedule.py` holds `SweepSchedule`, the pure timer a supervisor checks before
sweeping; `role.py` holds `HouseBee` itself, the `hivemind.workers.base.Worker`-protocol adapter
around one sweep. This face only re-exports (codingrules section 5.4).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles`. Constructed by a Warden's
    spawn logic (roadmap step 3.19, a parallel dispatch) and run once per attempt by
    `hivemind.workers.runtime.WorkerRuntime`; `run_sweep` is also exposed directly so the Queen's
    own housekeeping tick runs it on `SweepSchedule`'s timer without going through a `TaskAssign`
    at all, and `HouseBeeRipening` is run by the composition root beside the Queen. Calls into
    `hivemind.cell`, `hivemind.honey_store`, `hivemind.llm`, `hivemind.memory`,
    `hivemind.workers.base`, `hivemind.workers.context` and waggle only; never `hivemind.wardens`
    or `hivemind.queen` (codingrules section 4: "a Worker never imports its Warden").

Key invariants:
    - `HouseBee.run` never marks a task SUCCEEDED (codingrules section 8.7): every attempt returns
      `claimed=True, handoff=None`.
    - `run_sweep` never summarises a `BeeBreadEntryKind.SUMMARY` entry (docs/adr/0022's "one level
      of summary"), and never a compaction batch larger than `hivemind.memory.bee_bread.entry.
      MAX_REF_IDS`.
    - Nothing here ripens inside the Queen's tick: the sweep only deposits Nectar; model calls
      for ripening happen in `HouseBeeRipening`'s own loop (docs/adr/0031).

See Also:
    - .claude/codingrules.md section 8.7 for "a Worker never marks itself SUCCEEDED."
    - .claude/codingrules.md section 8.9 for "demotion is a duty, not an emergency", extended here
      to compaction.
    - .claude/roadmap.md step 4.3 for the work that populates this package.
    - hivemind.workers.roles.drone for Drone, the sibling role this package's shape mirrors.
    - hivemind.memory.compact and hivemind.memory.demote for this package's two memory-side
      collaborators.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for where ripening runs and why.
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the lowering the
      ripening pass files and reviews.

Public API (roadmap 4.3):
    - HouseBee, HOUSE_BEE_HOT_WINDOW_S: the role itself, and its mirrored hot-window constant
      (hivemind.workers.roles.house_bee.role).
    - run_sweep, SweepDeps, SweepWindow, SweepOutcome, SWEEP_NOTE_LIMIT, SWEEP_DECISION_LIMIT: one
      sweep's own work, decoupled from the Worker protocol (hivemind.workers.roles.house_bee.sweep).
    - SweepSchedule: the pure timer a supervisor checks before running a sweep
      (hivemind.workers.roles.house_bee.schedule).

Public API (roadmap 7.6, 7.9a):
    - HouseBeeHoney, CellRecords, GatheredOn, deposit_aged_bee_bread, deposit_retired_wax,
      bee_or_none, BEE_BREAD_WATERMARK, BEE_BREAD_SOURCE_KEY_PREFIX, WAX_SOURCE_KEY_PREFIX: the
      sweep's Honey deposit duties and what they are attributed through
      (hivemind.workers.roles.house_bee.honey).
    - HouseBeeRipening, RipeningPass, MAX_PROPOSALS_PER_PASS: the ripening loop beside the Queen,
      whose pass also files and reviews label lowerings (ADR-0034)
      (hivemind.workers.roles.house_bee.loop).
"""

from hivemind.workers.roles.house_bee.honey import (
    BEE_BREAD_SOURCE_KEY_PREFIX,
    BEE_BREAD_WATERMARK,
    WAX_SOURCE_KEY_PREFIX,
    CellRecords,
    GatheredOn,
    HouseBeeHoney,
    bee_or_none,
    deposit_aged_bee_bread,
    deposit_retired_wax,
)
from hivemind.workers.roles.house_bee.loop import (
    MAX_PROPOSALS_PER_PASS,
    HouseBeeRipening,
    RipeningPass,
)
from hivemind.workers.roles.house_bee.role import HOUSE_BEE_HOT_WINDOW_S, HouseBee
from hivemind.workers.roles.house_bee.schedule import SweepSchedule
from hivemind.workers.roles.house_bee.sweep import (
    SWEEP_DECISION_LIMIT,
    SWEEP_NOTE_LIMIT,
    SweepDeps,
    SweepOutcome,
    SweepWindow,
    run_sweep,
)

__all__ = [
    "BEE_BREAD_SOURCE_KEY_PREFIX",
    "BEE_BREAD_WATERMARK",
    "HOUSE_BEE_HOT_WINDOW_S",
    "MAX_PROPOSALS_PER_PASS",
    "SWEEP_DECISION_LIMIT",
    "SWEEP_NOTE_LIMIT",
    "WAX_SOURCE_KEY_PREFIX",
    "CellRecords",
    "GatheredOn",
    "HouseBee",
    "HouseBeeHoney",
    "HouseBeeRipening",
    "RipeningPass",
    "SweepDeps",
    "SweepOutcome",
    "SweepSchedule",
    "SweepWindow",
    "bee_or_none",
    "deposit_aged_bee_bread",
    "deposit_retired_wax",
    "run_sweep",
]
