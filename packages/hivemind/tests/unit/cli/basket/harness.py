"""Test harness for hivemind.cli.basket: run `hive basket` against a real manifest and SQLite file.

Every `hive basket` test drives the real typer app through `CliRunner` against a
`builders.cli.fake_manifest` Hive (every model slot on the scripted `fake` provider), so a deposit
runs the real door: the real scanner, the real Hive key, the real Honey Store behind the Honey
port. Results are read back through the real Basket store and trail, the way a separate process
would find them. `invoke` keeps structlog's own log lines off stdout: the CLI configures no
logging, and structlog's default prints to stdout.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped. Used
    by every test module under tests/unit/cli/basket/.

Key invariants:
    - None: this module holds test helpers only.

See Also:
    - hivemind.cli.basket for the command group under test.
    - builders.cli.fake_manifest for the manifest reused.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path

from builders.cli import fake_manifest
from structlog.testing import capture_logs
from typer.testing import CliRunner, Result

from hivemind.basket import Blob, SqliteBasketStore
from hivemind.cell import hive_stand_cell_id
from hivemind.cli.app import app
from hivemind.cli.stores import open_basket_store, open_trail
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from waggle.ids import CellId

__all__ = [
    "append_toml",
    "corrupt_signature",
    "db_path",
    "deposit",
    "hive_stand",
    "invoke",
    "make_hive",
    "mark_tainted",
    "store_of",
    "stored",
    "trail_events",
]

_runner = CliRunner()
_EVENT_LIMIT = 200  # More trail events than any one test records.
_SHA256 = re.compile(r"[0-9a-f]{64}")


def make_hive(tmp_path: Path) -> Path:
    """Write a fake-provider Hive Manifest under `tmp_path` and return its path."""
    return fake_manifest(tmp_path)


def db_path(manifest: Path) -> Path:
    """Return the SQLite file `fake_manifest` names as `[hive] db`."""
    return manifest.parent / "data" / "hive.sqlite3"


def invoke(manifest: Path, *args: str) -> Result:
    """Run `hive basket --manifest <manifest> <args...>` with structlog's lines captured."""
    with capture_logs():
        return _runner.invoke(app, ["basket", "--manifest", str(manifest), *args])


def deposit(manifest: Path, content: str = "a note worth keeping", *extra: str) -> str:
    """Deposit `content` as a text file through the CLI; return the printed sha256."""
    source = manifest.parent / "deposit.txt"
    source.write_text(content, encoding="utf-8")
    result = invoke(
        manifest,
        *("deposit", str(source), "--title", "A note", "--summary", "What it is for."),
        *("--kind", "text", "--media-type", "text/plain", *extra),
    )
    assert result.exit_code == 0, result.output
    match = _SHA256.search(result.stdout)
    assert match is not None, result.stdout
    return match.group(0)


def store_of(manifest: Path) -> SqliteBasketStore:
    """Open the Hive's Basket store where the CLI keeps its byte tree (`basket/` beside the db)."""
    return open_basket_store(db_path(manifest), db_path(manifest).parent / "basket")


def stored(manifest: Path, sha256: str) -> Blob:
    """Read one blob's description back from the real store."""
    return asyncio.run(store_of(manifest).get(sha256))


def hive_stand(manifest: Path) -> CellId:
    """Return the Hive Stand's Cell id for this manifest's node."""
    return hive_stand_cell_id(load_manifest(manifest, {}).hive.node_id)


def trail_events(manifest: Path, kind: str) -> list[PheromoneEvent]:
    """Return every event of `kind` on the Hive's trail, oldest first."""
    trail = open_trail(db_path(manifest))
    return list(asyncio.run(trail.query(TrailQuery(kind=kind, limit=_EVENT_LIMIT))))


def append_toml(manifest: Path, toml: str) -> None:
    """Append a table to the manifest (`[basket]` overrides), as an operator's edit."""
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(f"{text}\n{toml}\n", encoding="utf-8")


def corrupt_signature(manifest: Path, sha256: str) -> None:
    """Replace a blob's manifest signature with another well-formed one: a tampered manifest."""
    _update(
        manifest, "UPDATE basket_blobs SET manifest_signature = ? WHERE sha256 = ?", "AAAA", sha256
    )


def mark_tainted(manifest: Path, sha256: str) -> None:
    """Flag a blob tainted, a state a deposit from the Hive Stand never makes."""
    _update(manifest, "UPDATE basket_blobs SET tainted = ? WHERE sha256 = ?", 1, sha256)


def _update(manifest: Path, statement: str, value: str | int, sha256: str) -> None:
    """Run one fixed UPDATE against the Hive's database to stage a state the CLI cannot make."""
    with sqlite3.connect(db_path(manifest)) as connection:
        connection.execute(statement, (value, sha256))
