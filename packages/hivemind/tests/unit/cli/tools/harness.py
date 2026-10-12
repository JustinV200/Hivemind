"""Test harness for hivemind.cli.tools: run `hive tools` against a real manifest and SQLite file.

Every `hive tools` test drives the real typer app through `CliRunner` against a
`builders.cli.fake_manifest` Hive, seeding the Comb Registry through the real
`SqliteCombRegistry.promote` with the Royal Jelly builders (a spec, a real package, a passing
report), the way a separate process would have left it, and reading results back through the real
registry and trail. `invoke` keeps structlog's own log lines off stdout: the CLI configures no
logging, and structlog's default prints to stdout.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped. Used
    by every test module under tests/unit/cli/tools/.

Key invariants:
    - None: this module holds test helpers only.

See Also:
    - hivemind.cli.tools for the command group under test.
    - builders.royal_jelly for the spec, package and report builders reused.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cli import fake_manifest
from builders.royal_jelly import make_offer, make_tool_spec
from structlog.testing import capture_logs
from typer.testing import CliRunner, Result

from hivemind.cli.app import app
from hivemind.cli.stores import open_comb_registry, open_trail
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.royal_jelly import (
    CombRegistry,
    CombToolRow,
    QueenPrincipal,
    RegistryChange,
    ToolSpec,
    WardenPrincipal,
)
from waggle.messages.tool.authoring import ToolScope

__all__ = [
    "db_path",
    "invoke",
    "make_hive",
    "promote",
    "registry_of",
    "rows",
    "trail_events",
]

_runner = CliRunner()
_EVENT_LIMIT = 200  # More trail events than any one test records.


def make_hive(tmp_path: Path) -> Path:
    """Write a fake-provider Hive Manifest under `tmp_path` and return its path."""
    return fake_manifest(tmp_path)


def db_path(manifest: Path) -> Path:
    """Return the SQLite file `fake_manifest` names as `[hive] db`."""
    return manifest.parent / "data" / "hive.sqlite3"


def invoke(manifest: Path, *args: str) -> Result:
    """Run `hive tools --manifest <manifest> <args...>` with structlog's lines captured."""
    with capture_logs():
        return _runner.invoke(app, ["tools", "--manifest", str(manifest), *args])


def registry_of(manifest: Path) -> CombRegistry:
    """Open the Hive's Comb Registry on its own connection."""
    return open_comb_registry(db_path(manifest))


def promote(manifest: Path, **spec_overrides: object) -> tuple[ToolSpec, RegistryChange]:
    """Promote one echo tool through the real registry: HIVE by the Queen, CELL by its Warden."""
    spec = make_tool_spec(**spec_overrides)
    report, offer = make_offer(spec)
    principal = (
        WardenPrincipal(spec.requested_by, spec.cell_id)
        if spec.scope is ToolScope.CELL and spec.cell_id is not None
        else QueenPrincipal()
    )
    change = asyncio.run(
        registry_of(manifest).promote(spec, report, offer, principal, "Seeded for a test.")
    )
    return spec, change


def rows(manifest: Path) -> tuple[CombToolRow, ...]:
    """Return every stored version of every tool, retired ones included."""
    return asyncio.run(registry_of(manifest).list_tools())


def trail_events(manifest: Path, kind: str) -> list[PheromoneEvent]:
    """Return every event of `kind` on the Hive's trail, oldest first."""
    trail = open_trail(db_path(manifest))
    return list(asyncio.run(trail.query(TrailQuery(kind=kind, limit=_EVENT_LIMIT))))
