# hivemind.cli

The cli package is the hive command-line interface: one file per command group, each a thin
typer layer that calls into a subsystem's public API and never contains logic of its own.

## Public API

- `main() -> None`: the console-script entry point (`hive = "hivemind.cli.app:main"` in
  `packages/hivemind/pyproject.toml`). Builds the typer application in `app.py` (the composition
  root) and runs it.
- `app.py` also exposes `app`, the `typer.Typer` instance, for `typer.testing.CliRunner`.
- `version.py`: `collect_version_info() -> VersionInfo` and `format_version(info) -> str`, used
  by the root `--version` option.

## Command groups (phase 2 step 2.9)

- `stores.py` -- the CLI's own store composition root: `open_trail(db) -> SqlitePheromoneTrail`
  and `open_chamber(db, identity) -> BroodChamber`, each a sync function running its own
  `asyncio.run` internally, plus `resolve_db(manifest_path, db)`, which decides *which* file they
  open. Every command group takes `--manifest`, so an operator never has to remember which flag a
  given command wants; `DbOption`'s `--db` is optional and stays as the escape hatch for a
  database no manifest names (merging another node's segment into it) or a Hive with no manifest
  file to hand, and wins outright when it is given.
- `tasks.py` -- `hive tasks submit FILE --hive-id --node-id [--actor human] [--manifest]` (reads a
  `TaskGraphDraft` JSON file, calls `BroodChamber.submit`, prints one `key<TAB>id<TAB>status` line
  per task in graph order; a bad file, bad JSON, a bad id or a cycle exits 2), `hive tasks list
  [--status] [--goal] [--manifest] [--db]` (a fixed-width table: id, status, attempt, title),
  `hive tasks show ID [--manifest] [--db]` (prints `Task.model_dump_json(indent=2)`; an unknown id
  exits 1). `submit` is the one store command with no `--db`: it already carries the five
  parameters codingrules 5.1 allows, and writing a task graph means writing into a Hive, which is
  the thing a manifest names.
- `trail.py` -- `hive trail tail [-n 50] [--follow] [--interval 1.0] [--manifest] [--db]` (recent
  events, oldest first, as `<at ISO>  <kind>  <subject_id>  <actor>  <payload compact JSON>`;
  `--follow` keeps printing new ones through `hivemind.pheromone.follow` until Ctrl-C, exiting 0),
  `hive trail export NODE_ID OUT [--manifest] [--db]` (writes a `TrailSegment` JSON file), `hive
  trail merge SEGMENT [--manifest] [--db]` (reads one back; prints how many events were actually
  inserted, 0 on a re-merge).

## Command groups (phase 3 step 3.21, first half)

- `stores.py` also holds the manifest-aware composition helpers every later command that needs a
  model builds on: `slot_bindings(manifest) -> tuple[SlotBinding, ...]` and
  `provider_configs(manifest) -> Mapping[str, ProviderConfig]` convert a loaded `HiveManifest`'s
  `[llm.slots]`/`[llm.providers]` tables into `hivemind.llm`'s own inputs (`hivemind.llm` may not
  import `hivemind.manifest`, codingrules section 4); `build_forage_map(manifest, clock) ->
  ForageMap` does the same for `[forage.map]`; `build_registry(manifest, environ, clock) ->
  ProviderRegistry` composes all three with `hivemind.llm.registry.default_factories()` into one
  ready registry. `tests/builders/llm.py`'s `bindings_from_manifest`/
  `provider_configs_from_manifest` now call straight through to `slot_bindings`/`provider_configs`.
- `llm.py` -- `hive llm providers --manifest hive.toml [--json]` (one row per
  `[llm.providers.<name>]`: kind, base URL (or `(vendor default)`), seats, whether an API key is
  set in the environment, and a live health probe; `[llm] offline = true` refusing a provider's
  construction shows `refused: offline` instead of failing the command), `hive llm slots
  --manifest hive.toml [--json]` (one row per `hivemind.forage.slots.ModelSlot`: binding key,
  provider, model, effort, context window, per-million-token price or `-`, and the fallback chain
  as `a -> b -> c`), `hive llm test SLOT --manifest hive.toml [--prompt "..."]` (resolves SLOT,
  sends one small completion the way `hivemind.llm.ladders.gate.DirectCallGate` would, and prints
  latency, usage and the reply's first line; exits 1 with the raised `LLMError`'s own message on
  failure).
- `capping.py` -- `hive capping queue [--manifest] [--db] [--json]` (every Capping proposal whose
  latest `capping.*` event is not terminal, newest latest-event first: proposal id, task id, tier,
  state, age) and `hive capping show PROPOSAL_ID [--manifest] [--db] [--json]` (that proposal's
  `capping.*` events, in order, with their payload fields). A v0 `CappingGate`'s proposal table
  lives only in the Queen process's own memory, so both commands reconstruct what they show from
  the Pheromone Trail every gate transition already writes (codingrules section 12).

## Command groups (phase 3 step 3.21, second half)

- `stores.py` also holds the composition helpers every command shares: `DEFAULT_MANIFEST`
  (`hive.toml`), `ManifestOption`/`JsonOption` (the `--manifest`/`--json` typer option annotations
  every command group attaches, `hive llm` included), `load_manifest_or_exit(path) -> HiveManifest` (loads a
  manifest or exits 2 with the raised `ManifestError`'s own message), and `build_registry`'s own
  signature grew two keyword-only parameters (`factories`, `forage_map`) so `hivemind.cli.compose.
  build_hive` can share one `ForageMap` across the registry, the Fanner and `QueenDeps`, and
  substitute a responder-installing `"fake"` factory for a test.
- `compose/` -- the composition root that turns a loaded `HiveManifest` into a running Hive
  (codingrules section 8.2: "exactly one place per entry point"). `build_hive(manifest, *,
  environ, clock, stores=None, responders=None) -> Hive` builds every store, the LLM registry, the
  Fanner, the Hive Stand source, one Queen<->Warden Waggle link and both kernels; `run_hive(hive)
  -> AsyncIterator[None]` is an `asynccontextmanager` that leases the Cell and runs the Queen and
  Warden as an `asyncio.TaskGroup`; on exit it stops both cooperatively (their own `stop()`) and
  awaits their `run()` tasks before the TaskGroup block itself ends, so an exception from the
  caller's own `async with` body can never have the TaskGroup cancel a tick still in flight, then
  closes the link; `run_goal
  (hive, goal, *, clearance, timeout_s, on_event=None) -> GoalReport` submits a goal and polls
  until every task is terminal or `timeout_s` elapses, calling `hivemind.queen.
  sync_answers_from_chamber` and streaming trail events to `on_event` each poll. Split into
  `links.py` (the one Waggle link) and `deps.py` (every manifest-slice-to-deps conversion) to stay
  within codingrules section 5.1's 300-line budget; see its own `__init__.py` for the full split.
  Roadmap step 10.3: `deps.build_enforcer` builds the one Guard `Enforcer` (over `[guard]`'s
  policy) the Queen and the Hive Stand's Warden share (`HiveParts.enforcer`), the Warden's lease
  needs `HIVE_STAND_LEASE` (`cell:hive_stand`), and `run_hive` attaches the Hive Stand's Warden to
  the Queen first (her awaited `warden_spawn` point), so a freshly built `Hive.queen` has no Warden
  attached yet. `in_cell/deps.py` does the same for a Virtual Cell's Warden: the shipped policy,
  recording to the Cell's own trail, with `VIRTUAL_CELL_LEASE` (`cell:virtual`). Roadmap step
  10.3a: that policy names the Hive Stand as the Cell reaches it (`in_cell/hive_stand.py`: the
  Queen URL's host by name, and its addresses resolved once at start, never an onion's); the
  Cell's tier comes from `HIVEMIND_COMB_SHIELD` (`in_cell/config.py`, held to its link), and a
  Night Veil Cell dials only through its Tor SOCKS proxy and attests that link on `CellReady`.
- `run.py` -- `hive run "goal text" --manifest hive.toml [--clearance C1] [--timeout 300]
  [--json] [--comb-shield night_veil]`: the one command that calls `build_hive`/`run_hive`/
  `run_goal`. With `--comb-shield` (roadmap step 10.3c) the goal is asked for as a durable goal
  request instead (origin HUMAN, the tier named, no device: `compose/request.py`,
  `run_requested_goal`), the one way Night Veil work is initiated, and followed once the Queen has
  planned it; a request she refuses prints `hive run refused: <reason>` (exit 1), one still
  unplanned at the timeout exits 2. `RunCommand` carries `--json` and `--comb-shield` on the
  command itself, keeping `run_command` within five parameters. Streams trail events as
  they arrive (unless `--json`), then a one-line summary; exits 0 on success, 1 when the goal
  failed, 2 on a timeout or a bad manifest. The streamed view begins at the goal's own
  submission, never earlier (a store that already holds other runs does not replay them), and
  with `[hive_stand] keep_scratch = true` (development only) the last line names the lease
  directory the run's files were left in. Registered on the root app with
  `app.command("run", cls=RunCommand)`,
  not `app.add_typer` -- unlike every other group in this package, it has no subcommand of its own
  (`hive run "goal"`, not `hive run run "goal"`), and the pinned typer version does not collapse a
  single-command `add_typer` sub-app onto its parent's own name (verified empirically; see this
  module's own docstring).
- `readback/` -- three commands grouped into a sub-package (codingrules section 5.6: `cli/` itself
  stays within the ten-module limit) because they share one shape: each reconstructs what it shows
  from a stored artifact this Hive already writes for another reason, never a live link into a
  running `hive run`'s Queen process, which v0 has none of.
    - `cells.py` -- `hive cells list --manifest hive.toml [--json]`: builds only the Hive Stand's
      own `HiveStandSource` (never a full Hive) and lists every Cell it reports (id, name, source,
      kind, access level, Comb Shield tier, capabilities, capacity). Nests `leavings.py`'s own
      Typer app as `hive cells leavings` (roadmap step 5.0a).
    - `leavings.py` -- `hive cells leavings list [CELL] --manifest hive.toml [--include-removed]
      [--json]`: every `hivemind.cell.leavings.Leaving` recorded for CELL (active rows only,
      unless `--include-removed`), never `Leaving.prior`; CELL is optional (every `hive run`
      mints a fresh Cell id for the Hive Stand, so an operator cannot always name one) -- omitting
      it lists every active Leaving across every Cell instead (`LeavingsStore.list_all_leavings`),
      with a CELL column in the table and in `--json`. `hive cells leavings remove [CELL]
      [--path PATH] --manifest hive.toml`: with CELL and no `--path`, replays every active row's
      `prior` bytes back (or unlinks, when `prior` is `None`) onto the Hive Stand's real
      filesystem, then marks each row removed and records one `cell.leaving_removed` event per
      row (a row already removed is simply absent from the next run's own list, so a second
      `remove` is a no-op, not an error); with `--path`, removes exactly the one active Leaving at
      that exact path, scoped to CELL when given or searched across every Cell otherwise
      (refusing with a clear message if the same path is active on more than one Cell); with
      neither CELL nor `--path`, refuses with a usage error (exit 2) rather than removing
      everything everywhere.
    - `inbox.py` -- `hive inbox --manifest hive.toml [--json]` lists pending questions
      (`chamber.pending_questions`) and Alarms still escalated to the human, reconstructed from the
      trail's own `alarm.escalated`/`alarm.resolved` events; `hive inbox answer QUESTION_ID "text"
      [--option N] --manifest hive.toml` records the answer through `chamber.answer` and also
      leaves a `hivemind.memory.Note` (keyed by `hivemind.queen.answer_note_author`) a running
      `hive run`'s own `sync_answers_from_chamber` polls for and forwards on its next poll, since
      the Brood Chamber's own public API has no way to read an already-`ANSWERED` question's text
      back out.
    - `wardens.py` -- `hive wardens list --manifest hive.toml [--json]`: every Warden id ever seen
      in a `warden.*` event (state = its latest kind), the grants issued to it
      (`forage.granted`'s own `warden_id` payload field), and every sub-bee id seen in a `worker.*`
      event. v0 has exactly one Warden and a `worker.*` event carries no `warden_id` of its own, so
      every sub-bee is listed under every Warden this command has ever seen -- exact for the
      one-Warden Hive this phase ships, flagged as a limitation a multi-Warden phase must close.

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/cli
```

`test_app.py` drives the typer app with `typer.testing.CliRunner`: `hive --version` exits 0 and
prints a line matching `^hive \S+ \(Python \d+\.\d+\.\d+ on .+\)$`; a bare `hive` exits 0 and
prints help. `test_version.py` checks `collect_version_info` against the installed distribution
version and the running interpreter, and `format_version`'s exact shape. `test_stores.py`,
`test_tasks.py` and `test_trail.py` drive `hive tasks`/`hive trail` the same way, each against a
real `tmp_path` SQLite file; `test_trail.py`'s `--follow` test monkeypatches
`hivemind.cli.trail.open_trail` and `hivemind.cli.trail.follow` so Ctrl-C is simulated
deterministically instead of depending on real signal delivery or wall-clock timing.
`test_stores.py` also covers the four manifest-conversion helpers against
`docs/manifests/{minimal,local,full}.toml`. `test_llm.py` drives `hive llm` against a hand-built
`kind = "fake"` manifest, fully offline; its `test` command tests monkeypatch
`hivemind.cli.llm.build_registry` to hand back a pre-scripted `FakeLLMProvider` for the success
case, and rely on an unscripted one's own `ProviderUnavailableError` for the failure case.
`test_capping.py` seeds a `MemoryPheromoneTrail` directly through `PheromoneTrail.record` (the
same event shapes `hivemind.supervision.capping.gate.CappingGate` itself writes) and monkeypatches
`hivemind.cli.capping.open_trail` to hand it to the CLI.

`tests/builders/cli.py`'s `fake_manifest(tmp_path, *, capabilities="full", clock=None) -> Path`
writes a real Hive Manifest TOML with every `hivemind.forage.slots.ModelSlot` bound to one
`kind = "fake"` provider, so `build_hive`'s own `responders` argument can script every call the
Queen, a Warden and a Drone make; its sibling `pump_until_done(clock, coro, *, limit=2_000)` drives
a `FakeClock`-backed coroutine to completion, yielding the event loop repeatedly between advances
so a whole message cascade (Warden tick -> spawn -> sub-bee tick -> tool loop round -> Capping
gate) settles before the next `clock.sleep()` in the chain is even registered.

`test_compose.py` builds a `Hive` over `fake_manifest` and a scripted three-haiku responder (write
three files, one tool call round each), and drives it through `run_hive`/`run_goal` with
`pump_until_done`: the key test asserts the goal succeeds and that the trail's own event kinds
carry `cell.leased`, `queen.planned`, `queen.assigned`, `forage.granted`, `worker.spawned`, every
`capping.*` stage, `task.succeeded` and `cell.released`, each in that relative order. `test_run.py`
drives `hive run` itself through `CliRunner`, monkeypatching `hivemind.cli.run.build_hive` to
inject a scripted responder (mirroring `test_llm.py`'s own pattern); `hive run` is one of the few
places a real `SystemClock` and a real sleep are expected, so these tests run in real time (the
goal itself finishes in well under a second). `test_cells.py`, `test_inbox.py` and
`test_wardens.py` seed a `fake_manifest`'s own SQLite file directly through `hivemind.cli.stores`'
`open_chamber`/`open_trail`/`open_memory` (or, for `hive wardens list`, trail events built by
hand) the same way a running `hive run` process would have left them, since each command's own job
is reading state a *different* process wrote.

## Command groups (phase 4 step 4.11)

- `memory/` (a package, not a flat module: `show.py`/`pins.py`/`compact.py`/`wax.py` plus a shared
  `context.py`, codingrules section 5.1's 300-line limit) -- `hive memory show <bee> [--manifest]
  [--db] [--clearance C1]` (builds a `hivemind.memory.HotStateSources` view over the Brood
  Chamber, the memory store and the trail, and prints what the real `hivemind.memory.assemble`
  packs for that principal: PINS/HOT_STATE sections, token count, and included/dropped counts,
  plus the bee's latest Handoff if the trail shows one). `hive memory pins add "<text>"
  [--clearance C0|C1|C2]`, `pins list [--clearance]`, `pins remove <id>` (thin calls onto
  `hivemind.memory.add_pin`/`MemoryStore.list_pins`/`.remove_pin`). `hive memory compact <bee>
  [--manifest] [--db]` runs one real `hivemind.memory.compact` call on `ModelSlot.RIPENER`,
  through `hivemind.cli.stores.build_registry`, over the closed tasks placed under Warden id
  `<bee>`'s Bee Bread entries (a Task carries no bee-shaped field this phase beyond `warden_id`;
  see the module's own docstring). `hive memory wax <cell> list|propose|clear`: CELL,
  `--manifest`/`--db` are read once on the `wax` group's own callback and shared through `ctx.obj`
  (codingrules section 5.1's parameter cap: `propose` already carries its own four flags), so they
  must come right after `wax` and before the subcommand name (`hive memory wax <cell> --manifest
  hive.toml propose "<text>"`, not `... propose "<text>" --manifest hive.toml`). `list` reads
  every note on that Cell through `MemoryStore.list_wax`; `propose "<text>" [--severity
  NOTE|CAUTION|BLOCK] [--expires-in SECONDS]` writes a PROPOSED note through `hivemind.memory.
  propose_wax`, leaving it for a running Queen's next tick to judge (never WRITTEN directly);
  `clear <id>` is an explicit operator override (there is no operator-facing clear *request* path
  in v0) that calls `hivemind.memory.clear_wax` itself, recording `memory.wax_cleared` with
  `WaxOrigin.HUMAN` -- its own `--help` says so.
- `forage.py` -- `--manifest`/`--db` are read once on this group's own callback and shared through
  `ctx.obj` by every subcommand below (codingrules section 5.1's parameter cap), so they must be
  given right after `forage` and before the subcommand name (`hive forage --manifest hive.toml
  status`, not `hive forage status --manifest hive.toml`). `status [--json]` restores a
  `hivemind.queen.forage.ForageLedger` from the durable ledger store and prints its headroom, the
  Royal Reserve, every Cell's latest reported capacity and every Warden's reported local pool
  plus every source still inside a provider's rate-limit window, rebuilt from the trail's own
  `llm.throttled` events (the live figure is only in a running Queen's in-memory Forage map; the
  printed note says where the rows come from). `grants [--json]` lists every live grant: holder,
  state, max sub-bees, seats, spend budget and spent. `grant <warden> --sub-bees N [--seats N]
  [--spend USD]` writes a grown or shrunk revision of that Warden's own already-live grant through
  `hivemind.queen.forage.grants.revise` (a Warden's first grant is always issued by a running
  Queen's own dispatcher, never by this command, so `grant` refuses cleanly when none exists yet)
  and records the same `forage.granted` event shape `hivemind.queen.dispatcher` writes for a
  task-driven grant; it never starts a Queen, and takes effect the moment one next starts, or the
  next time the holding Warden's own heartbeat renews.
- `readback/cluster.py` -- `hive cluster [--provider NAME]` and `hive wake [provider]` each append
  one durable `hivemind.queen.cluster.ClusterOrder` row (`hivemind.cli.stores.
  open_cluster_orders`) the running Queen's own `run_cluster_tick` polls every tick, and print its
  id; naming no provider means "every currently-bound one" for `cluster`, "every currently
  clustered one" for `wake`. `cluster` takes `--provider` as an option (not the roadmap's own
  bare `[provider]` positional) because a Click `Group` always resolves a leftover token against
  its own optional positional `Argument` before ever trying it as a subcommand name, so a bare
  `hive cluster status` would otherwise silently be parsed as "cluster the provider literally
  named status" instead of dispatching to `status` below (verified empirically; `hive wake` keeps
  the positional form since it is a plain command with no subcommands of its own, registered on
  the root app matching `hive run`'s own shape). `hive cluster status [--manifest] [--db]` prints
  every still-pending order plus, reconstructed from the trail's `queen.clustered`/`queen.resumed`
  events (there is no `OrderStore` method to list a handled order by itself), every order a
  running Queen's tick has already acted on. Lives in `hivemind.cli.readback` rather than a flat
  `hivemind.cli.cluster` module: `hivemind.cli` was already at codingrules section 5.6's
  ten-module limit, and this command's own shape (mostly a read, one small durable write) matches
  `readback.inbox`'s own `answer` command exactly.
- `capping/` (split from the earlier flat `capping.py` into `queue.py`/`sample.py`/`__init__.py`,
  codingrules section 5.1's 300-line limit) also gains `hive capping audit sample [--tier T]
  [--rate R] --fake-judge` (roadmap step 4.11's third piece): samples every terminal
  (VERIFIED/ROLLED_BACK) proposal found on the
  trail at its tier's own `audit_rate` (or `--rate`, overriding it) and reviews each one through
  `hivemind.supervision.capping.audit_completed`, printing per-tier `AuditRates`. The trail never
  carries a proposal's own action content (codingrules section 12), so the reviewed `Proposal` is
  reconstructed from only what the trail kept (task id, Cell id, tier) plus clearly-labelled
  placeholders for the rest -- a real audit of the bee's actual diff or command needs a live link
  this v0 CLI does not have, flagged in this dispatch's own report. `--fake-judge` is required
  for that same reason: `hivemind.wardens.judge.ModelJudgeReviewer` exists (a Warden's live gate
  uses it), but a model judge reviewing a placeholder action would only ever grade the
  placeholder, so every sampled proposal is reviewed by `hivemind.supervision.capping.
  FakeJudgeReviewer`, scripted with one APPROVE verdict per proposal. `hive capping audit rates` prints the same `AuditRates`,
  reconstructed from every past `capping.audited` event, with no fresh sampling.
- `docs/manifests/full.toml` gained the phase 4 fields no earlier step had added yet: `[memory]
  hot_window_s`, `sweep_interval_s`, `wax_text_cap_chars`; `[forage] measurement_drift_threshold`;
  `[forage.map.local_llama] vram_bytes_required` (every hosted source above it has none: no local
  footprint to weigh).

`test_memory.py`, `test_forage.py` and `test_cluster.py` follow the same `fake_manifest` + real
SQLite file pattern as `test_cells.py`/`test_inbox.py`/`test_wardens.py`: each seeds its own store
directly (through `hivemind.cli.stores`' `open_chamber`/`open_memory`/`open_ledger`/
`open_cluster_orders`/`open_trail`), drives the command through `CliRunner`, and asserts the
written row or trail event a running `hive run` process would have left behind. `test_capping.py`
gained the same `--fake-judge` sampling case, seeded the same way `test_queue_excludes_terminal_
proposals...` already seeds a `capping.*` sequence, with `[supervision] capping_tiers_file`/tier
lookups exercised against the shipped default table (`--tier`/`--rate` keep the assertion
independent of that table's own numbers).

`test_leavings.py` (roadmap step 5.0a) follows the same pattern for `hive cells leavings
list|remove`: seeds a `Leaving` directly through `hivemind.cli.stores.open_leavings`, at a real
file under the test's own `tmp_path`, then drives `list`/`remove` through `CliRunner` and asserts
the printed row (never `prior`), the filesystem after `remove` (unlinked or restored), and that a
second `remove` is a no-op. `stores.py` gained `open_leavings(db) -> SqliteLeavingsStore` beside
its other store functions, and `hivemind.cli.compose.deps.HiveStores`/`open_default_stores` gained
a fourth `leavings` store alongside `trail`/`chamber`/`memory`. It also covers the CELL-optional
defect fix: `list`/`remove` with no CELL spanning every Cell (two `_seed_leaving` calls, each its
own fresh Cell, since `make_leaving`'s default `cell_id` is minted per call), `remove --path`
scoped or unscoped, and the refusals (`remove` with neither CELL nor `--path`; `--path` active on
more than one Cell).

## Command groups (phase 5 step 5.13)

- `readback/virtual.py` (plus `readback/virtual_offline.py` and `readback/virtual_abscond.py`,
  split out to stay within codingrules section 5.1's 300-line budget) -- six commands merged
  straight into `hive cells` with no `virtual`/prefix of their own (`readback/cells.py`'s own
  `app.add_typer(virtual_app)`, given no name, folds a nameless sub-app's commands in at the
  parent's own level -- verified empirically). Real/Virtual is always decided by "is it in a
  registered backend's own labelled list", never `cell.kind` (ADR-0026's "backend labels are the
  source of truth"):
    - `hive cells inspect CELL_ID [--manifest]`: a Virtual Cell's own backend/image/status/labels/
      created_at (from `CellBackend.list_cells`, no Queen needed), or a Real Cell's own row plus
      every lease the trail still shows open for it.
    - `hive cells destroy CELL_ID [--manifest]`: Virtual only (refuses the Hive Stand's own Real
      Cell id with a clear message); goes through a plain `Undertaker` built over the manifest's
      own backend (`hivemind.cli.compose.virtual_cells.build_virtual_cells`) and a `ForageLedger`-
      backed `GrantRevoker`; idempotent (an unknown id is a clean no-op, `CellBackend.destroy`'s
      own contract), printing the `cell.destroyed` trail event id.
    - `hive cells release LEASE_ID [--manifest]`: writes a durable
      `hivemind.queen.cluster.ClusterOrder(kind=RELEASE, lease_id=...)` row, exactly like `hive
      cluster`/`hive wake` (docs/adr/0024), for a running Queen's own tick
      (`hivemind.queen.cluster.tick.run_release_tick`, roadmap step 5.13's own addition to the
      Clustering order table) to drain -- which can revoke the lease's Cell's live grants but not
      itself kill the Warden's session, a documented gap (`run_release_tick`'s own module
      docstring) needing a new Queen -> Warden wire message this dispatch does not add. Reports
      the lease orphaned and points at `abscond` when no Queen looks to be running, a heuristic
      read off the trail's own `warden.*` history (v0 has no live link into a running Queen).
    - `hive cells snapshot CELL_ID` / `rollback CELL_ID SNAPSHOT_ID [--manifest]`: through
      `hivemind.hive.snapshot.snapshotter_for` (roadmap step 5.10, a concurrent dispatch that
      landed during this one's own gate window), imported lazily inside `virtual_offline.
      build_snapshotter`. One cross-process limitation is documented there, not closed by this
      dispatch: `DockerSnapshotter`/`QemuSnapshotter` each keep their own snapshot-id-to-image
      mapping in one process's memory, so a `rollback` invocation cannot see what a separate,
      earlier `snapshot` invocation recorded.
    - `hive cells abscond [--yes] [--manifest]`: from backend labels and the trail alone (no
      lifecycle table). Destroys every Virtual Cell every registered backend lists for this Hive's
      id; for every Real Cell lease the trail shows still open, writes a RELEASE order when a
      Queen looks to be running, or reconstructs and releases its `RealCellLease` directly when
      its own scratch directory still exists (`virtual_offline.reconstruct_lease`) -- a lease
      neither running Queen nor still-present scratch can help is left untouched and counted, the
      "minimal safe thing" this dispatch's own report names. Revokes every grant the pass touched
      (a before/after count on the restored ledger) and prints a summary: containers destroyed,
      leases released/deferred/left untouched, grants revoked, and whether a fresh re-read now
      finds everything gone (`left_as_found`). Confirms interactively unless `--yes`.
  - `queen/cluster/orders.py` grew a third `OrderKind.RELEASE` (naming `lease_id`, never
    `provider`) on the same table CLUSTER/WAKE already use, plus its own
    `0002_add_release_lease_id.sql` migration -- the roadmap step's own "choose the smaller
    change" over a sibling order table. `queen/cluster/tick.py`'s `_drain_orders` now filters to
    `{CLUSTER, WAKE}` explicitly (a RELEASE row used to fall into its own `else` branch, back when
    "not CLUSTER" safely meant "WAKE"); `run_release_tick` is the new RELEASE-only drain, hooked
    into `queen/ticks/housekeeping.py`'s `run_housekeeping` right after `run_cluster_tick`.
  - `workers/roles/undertaker/role.py` gained `NullWaxRetirer` (mirrors `NullLeavingsRemover`, for
    a caller with no live Cell Wax store to wire in) and `destroy_virtual` now returns the
    `cell.destroyed` trail event's own `EventId`, so a caller (this dispatch's own `destroy`
    command) can hand it back as a receipt without a second trail query.

`test_virtual.py` drives all six commands through `CliRunner` against a real `fake_manifest` (with
`[virtual_cells] backend = "fake"` appended, since `builders.cli.fake_manifest` itself is not this
dispatch's file to grow a knob on) and a real SQLite file; `hivemind.cli.readback.virtual.
build_virtual_cells`/`build_hive_stand_source` are monkeypatched to hand back one pre-seeded
`VirtualCellsParts`/`FakeCellSource` per test (mirroring `test_llm.py`'s own `build_registry`
monkeypatch), since a `FakeCellBackend` provisioned through its own real `provision()` call has no
way to be reached back from outside the CLI process otherwise, and `HiveStandSource` mints a fresh
random Cell id on every construction. `queen/cluster/test_orders.py` and `test_tick.py` gained
RELEASE-order and `run_release_tick` cases; `workers/roles/undertaker/test_role.py` gained
`NullWaxRetirer` and `destroy_virtual`'s own returned event id.

## Command groups (phase 10 step 10.5)

- `serve.py` -- `hive serve --manifest hive.toml`: runs the Queen, her Hive Stand Warden and the
  Hive Entrance (the HTTP door) in one event loop until SIGINT or SIGTERM, which it owns. Before
  anything listens it checks `[entrance]` against this host (`gather_facts`, `plan_exposure`: a
  mode whose prerequisites do not hold exits 2 naming the rule) and binds the loopback listener
  (a port it cannot bind exits 1). It prints where the loopback listener answers; the remote one
  starts only while exposed and not reduced. A bare command like `run`. The composition lives in
  `compose/entrance.py`: `build_served_hive` is `build_hive`, and `serve_hive` binds the Hive's
  `human_channel` relay (the Queen's `HumanChannel`) to the Entrance's push channel, opens its tables on
  the Hive's own database file, loads its keys from the secret store (the Hive's Ed25519 key,
  the Web Push keys with `HIVEMIND_ENTRANCE_VAPID_SUBJECT` or else `public_url` as the contact, and
  in a remote mode the Hive's certificate authority), builds the Entrance and runs it inside
  `run_hive`, stopping it (every socket, both listeners) before the Queen. `Hive` gained the
  Hive's one `enforcer` and the Queen's `human_channel` relay, both additive.

How to test it: `tests/unit/cli/test_serve.py` (the exit codes through `CliRunner`),
`tests/unit/cli/compose/test_entrance.py` (loopback serves; lan without mutual TLS, vpn without TLS
and tunnel without a command refuse; an unbindable loopback port refuses) and
`tests/e2e/test_hive_serve.py` (a program enrolled, approved, running a goal and reading the
Queen's reply over real sockets and the Hive's SQLite file).
