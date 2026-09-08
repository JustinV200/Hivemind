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

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/cli
```

`test_app.py` drives the typer app with `typer.testing.CliRunner`: `hive --version` exits 0 and
prints a line matching `^hive \S+ \(Python \d+\.\d+\.\d+ on .+\)$`; a bare `hive` exits 0 and
prints help. `test_version.py` checks `collect_version_info` against the installed distribution
version and the running interpreter, and `format_version`'s exact shape.
