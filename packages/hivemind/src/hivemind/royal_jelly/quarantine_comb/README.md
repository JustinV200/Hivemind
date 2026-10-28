# hivemind.royal_jelly.quarantine_comb

The Quarantine Comb is the proof a scaffolded tool needs before the Comb Registry will promote it
(ADR-0047). It runs a tool package through six gates in a sandbox and returns a `QuarantineReport`.

## Shape

```text
QuarantineComb.run(package, spec, sandbox, limits, path) -> QuarantineReport
  unpack (PackageError -> failed static gate)
  static            pure: AST scan, banned lists, files, spec round trip, entry point, tests exist
  -- put files under <root>/pkg, run `python -m ...quarantine_comb.runner pkg report.json <limits>` --
  tests             the runner ran test_tool.py with unittest
  schema            schema compiles, sample validates, sample + extra property is refused
  fuzz              schema-derived inputs: a JSON value or a clean raise, never a hang
  resources         CPU, peak memory, wall time and output under QuarantineLimits
  capabilities      every recorded session call is covered by spec.required_capabilities
```

The first failing gate ends the run; a failing report lists the gates that ran, the failing one
last. A passing report lists all six. `sandbox.cleanup()` always runs. Nothing is written to the
trail.

## Modules

- `sandbox.py` the `Sandbox` protocol; `sandbox_cell.py` a fresh `comb-<id>` directory below a
  Virtual Cell session's scratch (refuses a borrowed Cell); `sandbox_subprocess.py` development
  only: a temp directory and the local interpreter (the one place that starts a process); the
  manifest refuses `sandbox = "subprocess"` unless `[hive] env = "dev"`.
- `gates/` the six gates, `banned.py` (what a tool may not import or call), `scan.py` (the AST walk).
- `runner/` runs inside the sandbox: `main.py` (orchestration), `phases.py` (load, tests, schema,
  fuzz), `hooks.py` (the audit hook and its attribution rule), `session.py` (the recording
  session), `inputs.py` (schema-derived inputs), `usage.py` (limits, deadline, metering),
  `report.py` (the JSON document the gates read).
- `limits.py` the pure `setrlimit` plan; `path.py` `decide_path`; `errors.py`.

## What a generated test sees

`test_tool.py` is run with `unittest`. The package directory is on `sys.path`, so `from tool import
run` works. A module-level `SESSION` (a `CellSession` over the sandbox scratch) is set on the test
module before its code runs and again after, so tests call `asyncio.run(run(SESSION, {...}))`.
`HIVEMIND_COMB_SCRATCH` names the scratch directory and `tempfile` writes there. No network, no
process creation, no file access outside the package and scratch.

## How to test this

```text
uv run --frozen pytest -q -p no:cacheprovider packages/hivemind/tests/unit/royal_jelly/quarantine_comb \
    packages/hivemind/tests/contracts/test_sandbox_contract.py
```

The end-to-end tests start a real runner in `SandboxSubprocess` (a few seconds each, run
concurrently). Builders: `make_tool_files`, `make_malicious_files`, `make_runner_report`,
`StubSandbox` in `tests/builders/royal_jelly.py`.
