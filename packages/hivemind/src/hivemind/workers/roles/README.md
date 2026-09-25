# hivemind.workers.roles

The roles package holds one module (or package) per Worker role: Forager, Scout, GuardBee,
Undertaker, Drone and HouseBee, each implementing the shared Worker protocol, except the Guard Bee,
which runs in the Queen's process rather than on a Cell (see `guard_bee/`). `worker_for(role)`
(`selection.py`) is the one place a `waggle.messages.task.WorkerRole` becomes a fresh instance of
the role that implements it, for both composition roots' `WardenDeps.worker_factory` to share; it
raises `UnsupportedWorkerRoleError` for GUARD_BEE, HOUSE_BEE and UNDERTAKER, none of which is
spawned through a `TaskAssign` (see `selection.py`'s own docstring).

## Modules

- `bounded_loop/` (roadmap step 6.9) -- the tool-loop machinery the Drone, Forager and Scout all
  share, factored out of the Drone once the Forager needed the identical see/act loop: assemble a
  prompt from hot state, hand the model a tool registry, loop tool calls to a stop, cooperate with
  pause/cancel/handoff between calls. A `RoleProfile` (`profile.py`) is the one small knob set that
  makes each role different: its `WorkerRole`, its `Principal.role` string, its system prompt, its
  round cap, how it builds its own tool registry, and an optional per-tool-call hook (the
  Forager's Nectar deposit is the one shipped so far). `run_bounded_loop` (`runner.py`) is the one
  function every role's `run` delegates to; `sources.py` (`RoleSources`), `executor.py`
  (`HandoffRequestedError`, `LoopStoppedError` -- a tool ending the loop itself with an
  already-built outcome, the Scout's own `report_findings` -- and `LoopExecutor`), `fields.py` and
  `outcome.py` are the pieces `hivemind.workers.roles.drone` used to own alone. `prompt.py`'s
  `brief_for` also renders `TaskAssign.recon` (roadmap step 6.10) when it is non-empty: each Scout
  dependency's summary, targets, suggested steps and risks, delimited and labelled as untrusted
  model prose (codingrules 15) -- never `findings` itself, which the design's own enumeration
  leaves out. See `hivemind.workers.roles.bounded_loop`'s own docstring for the full shape.
- `drone/` -- `Drone` (roadmap step 3.16): a generic, disposable role built on `bounded_loop`,
  with no restriction on its tools and no per-call hook. Every name this package used to define
  itself (`DroneSources`, `_RecordingExecutor`, `build_claimed_outcome`, `build_handoff_outcome`,
  every Handoff-field function, `brief_for`, `select_counter`) now lives in `bounded_loop`, moved
  unchanged (roadmap step 6.9): this package's own modules are thin subclasses or re-exports at
  their original names and import paths, so nothing here, including this role's own prompt
  snapshot, changed behaviour. `Drone` itself lives in the package's own `role.py`, re-exported
  through `__init__.py` (a face only re-exports, codingrules 5.4). See `hivemind.workers.roles.
  drone`'s own docstring for the full shape.
- `forager/` (roadmap step 6.9) -- `Forager`: a bounded see/act role over a Cell's Exoskeleton,
  built on `bounded_loop` with the Drone's own tool selection (`hivemind.workers.tools.
  build_registry`) but its own system prompt, a slightly larger round cap, and one
  `on_tool_result` hook: `nectar.py`'s `deposit_nectar`, which deposits every successful
  `browser_read`/`browser_snapshot` result as a Bee Bread `TOOL_RESULT` entry (Nectar, until phase
  7's Honey Store exists) -- clearance kept, the page's current URL named, the text scrubbed for
  credential shapes (`hivemind.exoskeleton.recorder.redact.scrub_text`) and capped, never a frame.
  Without an attached Exoskeleton, `Forager.run` raises `ForagerRequiresExoskeletonError` before
  ever calling the model: a task placed for a Forager should always carry one (the Queen-side
  planner rule), so this is a placement bug, not a routine condition, and `WorkerOutcome`'s own
  claimed-xor-handoff shape has no room for "cannot proceed, do not retry in place" -- the same
  refusal shape `hivemind.workers.tools.exoskeleton.act.attached` already uses.
- `scout/` (roadmap step 6.10) -- `Scout`: a strictly budgeted (`SCOUT_MAX_ROUNDS`, about six),
  read-only role built on `bounded_loop` with its own narrow tool registry
  (`tools.scout_build_registry`: reads, a GET-only `http_request`, and its own `report_findings`
  -- never a write, a click, a keystroke or a command). Calling `report_findings` validates its
  arguments as a `waggle.messages.task.ScoutReport`, writes it to `SCOUT_REPORT_FILE` through the
  same capped path `write_file` uses, and ends the loop at once with a claimed outcome carrying the
  report (`hivemind.workers.roles.bounded_loop.executor.LoopStoppedError`). Running out of rounds
  (or the model simply stopping) without ever filing one is not left unreported: `Scout.run`
  builds a conservative `feasible=False` report explaining why, writes it the same way, and
  returns it -- so the Warden's FILE_EXISTS acceptance still passes and the Queen still gets a
  report to hold its dependents back on.
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

- `guard_bee/` -- `GuardBee` (roadmap step 10.6, ADR-0043): the security watcher. It runs in the
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

## Public API (roadmap 3.16, extended by 4.3, 5.8, 6.9, 6.10 and 10.6)

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
call through. `make_gui_context` (also in `tests/builders/workers.py`) gives a Forager or a Scout
an attached Exoskeleton over fakes, including `hivemind.exoskeleton.browser.fake.FakeBrowser`
serving `hivemind.exoskeleton.browser.fake.login_site()`, the fixture login site every Forager,
Scout and browser test drives; `tests/unit/workers/roles/test_scout_then_forager.py` runs a Scout
and a Forager back to back, at the Worker level, to prove the recon actually reaches the brief.
