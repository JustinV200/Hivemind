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

- `stores.py` -- the CLI's own store composition root, until the Hive Manifest lands (phase 3):
  `open_trail(db) -> SqlitePheromoneTrail` and `open_chamber(db, identity) -> BroodChamber`, each
  a sync function running its own `asyncio.run` internally; `DEFAULT_DB` (`hive.sqlite3`) and
  `DbOption`, the shared `--db` typer option annotation both groups below attach.
- `tasks.py` -- `hive tasks submit FILE --hive-id --node-id [--actor human] [--db]` (reads a
  `TaskGraphDraft` JSON file, calls `BroodChamber.submit`, prints one `key<TAB>id<TAB>status` line
  per task in graph order; a bad file, bad JSON, a bad id or a cycle exits 2), `hive tasks list
  [--status] [--goal] [--db]` (a fixed-width table: id, status, attempt, title), `hive tasks show
  ID [--db]` (prints `Task.model_dump_json(indent=2)`; an unknown id exits 1).
- `trail.py` -- `hive trail tail [-n 50] [--follow] [--interval 1.0] [--db]` (recent events, oldest
  first, as `<at ISO>  <kind>  <subject_id>  <actor>  <payload compact JSON>`; `--follow` keeps
  printing new ones through `hivemind.pheromone.follow` until Ctrl-C, exiting 0), `hive trail
  export NODE_ID OUT [--db]` (writes a `TrailSegment` JSON file), `hive trail merge SEGMENT [--db]`
  (reads one back; prints how many events were actually inserted, 0 on a re-merge).

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
- `capping.py` -- `hive capping queue --trail hive.sqlite3 [--json]` (every Capping proposal whose
  latest `capping.*` event is not terminal, newest latest-event first: proposal id, task id, tier,
  state, age) and `hive capping show PROPOSAL_ID --trail hive.sqlite3 [--json]` (that proposal's
  `capping.*` events, in order, with their payload fields). A v0 `CappingGate`'s proposal table
  lives only in the Queen process's own memory, so both commands reconstruct what they show from
  the Pheromone Trail every gate transition already writes (codingrules section 12).

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
