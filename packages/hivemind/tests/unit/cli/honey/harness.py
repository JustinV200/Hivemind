"""Test harness for hivemind.cli.honey: run `hive honey` against a real manifest and SQLite file.

Every `hive honey` test drives the real typer app through `CliRunner` against a
`builders.cli.fake_manifest` Hive (every model slot on the scripted `fake` provider, so the
EMBEDDER is a `FakeEmbedding` and short deposits ripen with no model call at all), seeding Nectar
through the real `NectarIntake` and reading results back through the real stores, the way a
separate `hive run` process would have left them. `invoke` keeps structlog's own log lines off
stdout: the CLI configures no logging, and structlog's default prints to stdout, which would
otherwise sit in front of every `--json` document these tests parse.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped. Used
    by every test module under tests/unit/cli/honey/.

Key invariants:
    - None: this module holds test helpers only.

See Also:
    - hivemind.cli.honey for the command group under test.
    - builders.cli.fake_manifest and builders.honey for the manifest and Nectar builders reused.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cli import fake_manifest
from builders.honey import make_honey_identity, make_nectar_submission
from structlog.testing import capture_logs
from typer.testing import CliRunner, Result

from hivemind.cell import HoneyClearance
from hivemind.cli.app import app
from hivemind.cli.stores import open_honey_store, open_trail
from hivemind.honey_store import Honey, Nectar, NectarIntake, ReadFilter
from hivemind.manifest import HoneyStoreSection
from hivemind.pheromone import PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

__all__ = [
    "bind_embedder",
    "db_path",
    "invoke",
    "make_hive",
    "rows",
    "seed_nectar",
    "trail_events",
]

_runner = CliRunner()
_ALL_ROWS = 1_000  # More Honey rows than any one test ripens.
_EVENT_LIMIT = 200  # More trail events than any one test records.
_HOSTED_PROVIDER = '\n[llm.providers.hosted]\nkind = "anthropic"\n'


def make_hive(tmp_path: Path) -> Path:
    """Write a fake-provider Hive Manifest under `tmp_path` and return its path."""
    return fake_manifest(tmp_path)


def db_path(manifest: Path) -> Path:
    """Return the SQLite file `fake_manifest` names as `[hive] db`."""
    return manifest.parent / "data" / "hive.sqlite3"


def invoke(manifest: Path, *args: str) -> Result:
    """Run `hive honey --manifest <manifest> <args...>` with structlog's lines captured."""
    with capture_logs():
        return _runner.invoke(app, ["honey", "--manifest", str(manifest), *args])


def seed_nectar(manifest: Path, content: str, **overrides: object) -> Nectar:
    """Deposit one Nectar through the real intake, declared C1 unless overridden."""
    store = open_honey_store(db_path(manifest))
    clock = SystemClock()
    intake = NectarIntake(
        store, make_honey_identity(clock), clock, HoneyStoreSection(), HoneyClearance.C1
    )
    submission = make_nectar_submission(clock, content=content.encode(), **overrides)
    return asyncio.run(intake.submit(submission)).nectar


def rows(manifest: Path) -> tuple[Honey, ...]:
    """Return every live Honey row in the Hive's store, newest first."""
    store = open_honey_store(db_path(manifest))
    every = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)
    return asyncio.run(store.list_honey(every, scope_prefix=None, limit=_ALL_ROWS, offset=0))


def trail_events(manifest: Path, kind: str) -> list[PheromoneEvent]:
    """Return every event of `kind` on the Hive's trail, oldest first."""
    trail = open_trail(db_path(manifest))
    return list(asyncio.run(trail.query(TrailQuery(kind=kind, limit=_EVENT_LIMIT))))


def bind_embedder(manifest: Path, *, model: str | None = None, hosted: bool = False) -> None:
    """Rebind `[llm.slots.embedder]`: to another fake model, or to a kind that cannot embed."""
    text = manifest.read_text(encoding="utf-8")
    old = '[llm.slots.embedder]\nprovider = "fake"\nmodel = "test-model"'
    provider = "hosted" if hosted else "fake"
    new = f'[llm.slots.embedder]\nprovider = "{provider}"\nmodel = "{model or "test-model"}"'
    text = text.replace(old, new)
    manifest.write_text(text + (_HOSTED_PROVIDER if hosted else ""), encoding="utf-8")
