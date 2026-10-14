"""Test harness for hivemind.cli.swarm: run `hive swarm` against a real manifest and SQLite file.

Every `hive swarm` test drives the real typer app through `CliRunner` against a `swarm_manifest`
Hive (every model slot on the scripted `fake` provider, `[swarm] enabled = true`), so a command
runs the real desk, the real registry and the real trail on a real database file. Devices are put
into the registry the one way a registry admits one, by redeeming an invite (`enrol`), and results
are read back through the same stores a separate process (the running Hive) would open. `invoke`
keeps structlog's own log lines off standard output: the CLI configures no logging, and structlog's
default prints to standard output.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped. Used
    by every test module under tests/unit/cli/swarm/.

Key invariants:
    - None: this module holds test helpers only.

See Also:
    - hivemind.cli.swarm for the command group under test.
    - builders.swarm.manifest.swarm_manifest for the manifest reused.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.swarm import enrol_node, make_device_party, make_swarm_node, swarm_manifest
from structlog.testing import capture_logs
from typer.testing import CliRunner, Result

from hivemind.cli.app import app
from hivemind.cli.stores import open_swarm_registry, open_trail
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.swarm.models import SwarmInvite, SwarmNode
from waggle.clock import FakeClock

__all__ = [
    "db_path",
    "enrol",
    "invites",
    "invoke",
    "invoke_cells",
    "make_hive",
    "stored",
    "trail_events",
]

_runner = CliRunner()
_EVENT_LIMIT = 200  # More trail events than any one test records.
_CLOCK = FakeClock()  # Mints the ids and times of enrolled test devices; never read as the time.


def make_hive(tmp_path: Path, **swarm: bool | int | float | str) -> Path:
    """Write a fake-provider Hive Manifest with `[swarm] enabled = true` and return its path."""
    return swarm_manifest(tmp_path, **swarm)


def db_path(manifest: Path) -> Path:
    """Return the SQLite file `fake_manifest` names as `[hive] db`."""
    return manifest.parent / "data" / "hive.sqlite3"


def invoke(manifest: Path, *args: str) -> Result:
    """Run `hive swarm --manifest <manifest> <args...>` with structlog's lines captured."""
    with capture_logs():
        return _runner.invoke(app, ["swarm", "--manifest", str(manifest), *args])


def invoke_cells(manifest: Path, *args: str) -> Result:
    """Run `hive cells list --manifest <manifest> <args...>` with structlog's lines captured."""
    with capture_logs():
        return _runner.invoke(app, ["cells", "list", "--manifest", str(manifest), *args])


def enrol(manifest: Path, **overrides: object) -> SwarmNode:
    """Enrol a fresh device into the Hive's registry, with these terms, and return it."""
    registry = open_swarm_registry(db_path(manifest))
    node = make_swarm_node(make_device_party(_CLOCK), _CLOCK, **overrides)
    return asyncio.run(enrol_node(registry, node, _CLOCK))


def stored(manifest: Path, node: SwarmNode) -> SwarmNode:
    """Read one device back from the real registry, as a separate process would find it."""
    found = asyncio.run(open_swarm_registry(db_path(manifest)).node(node.device_id))
    assert found is not None
    return found


def invites(manifest: Path) -> tuple[SwarmInvite, ...]:
    """Read every invite back from the real registry, in the order they were minted."""
    return asyncio.run(open_swarm_registry(db_path(manifest)).invites())


def trail_events(manifest: Path, kind: str) -> list[PheromoneEvent]:
    """Return every event of `kind` on the Hive's trail, oldest first."""
    trail = open_trail(db_path(manifest))
    return list(asyncio.run(trail.query(TrailQuery(kind=kind, limit=_EVENT_LIMIT))))
