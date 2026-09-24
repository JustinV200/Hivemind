# hivemind.workers.roles

The roles package holds one module (or package) per Worker role: Forager, Scout, GuardBee,
Undertaker, Drone and HouseBee, each implementing the shared Worker protocol, except the Guard Bee,
which runs in the Queen's process rather than on a Cell (see `guard_bee/`).

## Modules

- `drone/` -- `Drone` (roadmap step 3.16): a generic, disposable role that assembles its own
  hot-state prompt and runs a bounded `hivemind.llm.run_tool_loop` over the tools its capabilities
  allow, until the model stops calling tools, the round cap is reached, or its own telemetry says
  to checkpoint. Split by responsibility: `sources.py` (`DroneSources`, what one attempt knows for
  hot-state packing), `prompt.py` (assembling that hot state and building the `LLMRequest`;
  `brief_for` also lists `TaskAssign.leaves`, roadmap step 5.0b, beside the acceptance criteria --
  a Drone reads what the plan declared must stay but cannot widen the set, only raise a Question),
  `outcome.py` (`HandoffRequestedError`, the tool-executor adapter that cooperates with pause, cancel
  and handoff, and the two ways one attempt ends); `Drone` itself lives in the package's own
  `__init__.py`. See `hivemind.workers.roles.drone`'s own docstring for the full shape.
- `house_bee/` -- `HouseBee` (roadmap step 4.3): a maintenance role whose one duty is a **sweep**:
  demote whatever `hivemind.memory.should_demote` says has aged out of hot state into Bee Bread,
  then fold Bee Bread entries older than that same window, for closed tasks, into a new summary
  through `hivemind.memory.compact` (never from a previous summary). Split by responsibility:
  `sweep.py` (`run_sweep`, `SweepDeps`/`SweepWindow`/`SweepOutcome`, decoupled from the Worker
  protocol so a future timer-driven supervisor can call it directly), `schedule.py`
  (`SweepSchedule`, the pure timer a supervisor checks first), `role.py` (`HouseBee`, the
  Worker-protocol adapter around one sweep). Roadmap step 4.3's own wiring step:
  `hivemind.queen.ticks.housekeeping.run_housekeeping` is the other caller `run_sweep` was always
  meant for -- the Queen runs a sweep directly on her own tick timer, over her own memory store,
  never through a `TaskAssign` (she has no Cell of her own to spawn a Worker on). `SweepDeps.bound`
  /`.gate` are now optional (`None` when the caller could not resolve a `ModelSlot.RIPENER`
  binding): `_compact_closed_tasks` skips compaction alone for that sweep rather than failing the
  whole thing, since demotion and Cell Wax expiry need no model call. See `hivemind.workers.roles.
  house_bee`'s own docstring for the full shape.

- `undertaker/` -- `Undertaker` (roadmap step 5.8): the cleanup role. `role.py` (`Undertaker`, the
  `Worker`-protocol adapter, autopilot-only -- it never awaits a model or imports `hivemind.llm`)
  offers `destroy_virtual(cell_id)` (via the injected `CellBackend`, then the injected
  `GrantRevoker`, `WaxRetirer` and `LeavingsRemover` Protocols) and `release_real(lease)` (via
  `RealCellLease.release()`, already idempotent, then the `GrantRevoker` alone -- a Real Cell's
  release never retires wax or touches a ledgered path). Both operations retry every effectful
  step with exponential backoff (`RetryPolicy`). `sweep.py` (`sweep_orphans`, `SweepDeps`,
  `SweepReport`, `orphan_virtual_cells`/`orphan_real_leases` as the pure decision halves) is the
  Queen-startup sweep for orphans of both kinds, plus expired dormant Cells via
  `hivemind.hive.lifecycle.CellLifecycle.evict_expired`. `schedule.py`
  (`UndertakerSweepSchedule`) is the pure timer a future periodic sweep checks, mirroring
  `house_bee/schedule.py`. `CellKind` matters to exactly two callers in the whole codebase
  (codingrules section 8.7): `queen.placement` and this package's own `role.py`
  (`scripts/check_no_kind_branches.py` allowlists `hivemind/workers/roles/undertaker/`, the
  package path, not a single file). See `hivemind.workers.roles.undertaker`'s own docstring for
  the full shape, including the ten-line adapter `LeavingsRemover` documents for
  `hivemind.cell.leavings.LeavingsStore` once that (unmerged, another-branch) module lands.

- `guard_bee/` -- `GuardBee` (roadmap step 10.6, ADR-0035): the security watcher. It runs in the
  Queen's process on her tick (`hivemind.queen.ticks.guard_bee`), beside the House Bee's sweep,
  reads the central trail against rules shipped as data (`rules.toml`, overridden by
  `[guard.bee.rules]`), and turns every finding into a `GuardReport`, a `guard.alert` and a C2
  deposit. It narrows the whole Hive alone (a Capping tier's audit-rate raise, an Entrance reduce
  order) and files anything aimed at one Cell or bee as a request through the Queen's
  `GuardRequestDoor`, above a confidence floor, coalesced and capped per hour. Rules that ask for
  judgement get one awake episode on the judge slot, in a lane beside the tick. Its windows are
  rebuilt from the trail on every start, its own `guard.alert` events restoring what it already
  reported. It is not a `Worker`-protocol implementation: a Worker is handed a Cell and a session,
  exactly what the Guard Bee must never hold. `build_guard_bee` is what a composition root calls;
  see the package's own `README.md` and `docs/guard/guard-bee.md`.

## Public API (roadmap 3.16, extended by 4.3, 5.8 and 10.6)

See the `Public API:` section of `__init__.py` for the full, current list.

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/workers/roles
```

`tests/builders/guard_bee/` builds a Guard Bee over fakes and seeds the trails its rules count.

`tests/unit/workers/roles/` mirrors this package module for module. `tests/builders/workers.py`'s
`make_context` now builds a real `hivemind.supervision.capping.CappingGate`, so a Drone test's
tool calls exercise the real gate, not a stub; `builders.llm.FakeLLMProvider` (via `make_bound`)
scripts what the model says -- the same fake `house_bee`'s tests script `compact`'s own structured
call through.
