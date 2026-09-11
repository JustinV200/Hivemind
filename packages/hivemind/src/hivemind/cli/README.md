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
- `run.py` -- `hive run "goal text" --manifest hive.toml [--clearance C1] [--timeout 300]
  [--json]`: the one command that calls `build_hive`/`run_hive`/`run_goal`. Streams trail events as
  they arrive (unless `--json`), then a one-line summary; exits 0 on success, 1 when the goal
  failed, 2 on a timeout or a bad manifest. Registered on the root app with `app.command("run")`,
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
      kind, access level, Comb Shield tier, capabilities, capacity).
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
