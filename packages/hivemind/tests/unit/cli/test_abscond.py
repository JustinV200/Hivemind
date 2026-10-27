"""Test hivemind.cli.abscond: `hive abscond`, the human's break-glass teardown of the whole Hive.

Roadmap step 13.4. With the operator password, a confirmed scope review and the typed phrase, it
destroys every Virtual Cell, releases the Hive Stand's open lease (its scratch removed), removes a
temp dir no lease owns, evicts the resident Basket rows, removes only the Leaving the human ticked,
and records `guard.absconded` as the human with the confirmation evidence. A wrong password, a
declined review or a wrong phrase each change nothing at all.

Fits into the Hive:
    Mirrors src/hivemind/cli/abscond/ (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.basket import make_blob, make_manifest
from builders.cells import make_leaving
from builders.entrance.stand import PASSWORD, Terminal, set_password, stand_manifest
from builders.forage import make_capacity
from typer.testing import Result

from hivemind.cli.abscond import ABSCONDED_KIND
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.cli.stores import open_basket_store, open_leavings, open_trail
from hivemind.hive import VirtualCellSpec
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import CellEvent, PheromoneEvent, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_event_id, new_lease_id, new_warden_id

_ANSWERS = f"{PASSWORD}\n1\ny\nabscond my hive\n"  # Password, tick Leaving 1, yes, the phrase.


@dataclass(frozen=True)
class _Hive:
    """A Hive Stand with one of everything an Absconding tears down."""

    path: Path
    manifest: HiveManifest
    parts: VirtualCellsParts
    lease_scratch: Path
    temp_dir: Path
    ticked: Path
    kept: Path


async def _seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Hive:
    """Write the manifest, set the password, and seed a Cell, a lease, a temp dir and Leavings."""
    path = stand_manifest(tmp_path, '\n[virtual_cells]\nbackend = "fake"\n')
    await set_password(path)
    manifest = load_manifest(path, {})
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(FakeClock()), FakeClock())
    assert parts is not None
    spec = VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1,
        memory_bytes=512 * 1024**2,
        disk_bytes=1024**3,
        capacity=make_capacity(),
        hive_id=manifest.hive.id,
    )
    await parts.registry.get("fake").provision(spec)
    monkeypatch.setattr("hivemind.cli.abscond.command.build_virtual_cells", lambda *_a, **_k: parts)
    db = manifest.resolve_path(manifest.hive.db)
    scratch = manifest.resolve_path(manifest.hive_stand.scratch_root)
    clock = FakeClock()
    lease_scratch, temp = scratch / new_lease_id(clock), scratch / new_lease_id(clock)
    # Reviewed in path order: the first is the one ticked.
    ticked, kept = tmp_path / "a_ticked.txt", tmp_path / "b_kept.txt"
    # Off this loop: every store opens under its own asyncio.run, as the CLI's do.
    await asyncio.to_thread(_record_lease, db, manifest, lease_scratch.name)
    await asyncio.to_thread(_seed_files, (lease_scratch, temp))
    await asyncio.to_thread(_seed_leavings, db, ticked, kept)
    await asyncio.to_thread(_seed_resident, db)
    return _Hive(path, manifest, parts, lease_scratch, temp, ticked, kept)


def _record_lease(db: Path, manifest: HiveManifest, lease_id: str) -> None:
    """A Hive Stand lease the trail shows open, recorded by the Hive Stand's own node."""
    clock = FakeClock()
    event = CellEvent(
        id=new_event_id(clock),
        hive_id=manifest.hive.id,
        node_id=manifest.hive.node_id,
        at=clock.now(),
        actor="system",
        kind="cell.leased",
        subject_id=new_cell_id(clock),
        payload={
            "lease_id": lease_id,
            "holder": new_warden_id(clock),
            "task_id": None,
            "access_level": "FULL",
            "comb_shield": "MEADOW",
        },
    )
    asyncio.run(open_trail(db).record(event))


def _seed_files(dirs: tuple[Path, ...]) -> None:
    """Give each scratch directory a file, as a lease's work leaves one."""
    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "work.txt").write_text("left behind", encoding="utf-8")


def _seed_leavings(db: Path, ticked: Path, kept: Path) -> None:
    """Two Leavings on one Cell, in review order: the first is ticked, the second kept."""
    store = open_leavings(db)
    cell = new_cell_id(FakeClock())
    for path in (ticked, kept):
        path.write_text("left", encoding="utf-8")
        clock = FakeClock()
        leaving = make_leaving(clock, cell_id=cell, path=path)
        event = CellEvent(
            id=new_event_id(clock),
            hive_id="hive_01HZZZZZZZZZZZZZZZZZZZZZZZ",
            node_id="node_01HZZZZZZZZZZZZZZZZZZZZZZZ",
            at=clock.now(),
            actor="system",
            kind="cell.left",
            subject_id=cell,
            payload={"lease_id": leaving.lease_id, "path": str(path)},
        )
        asyncio.run(store.record_leaving(leaving, event))


def _seed_resident(db: Path) -> None:
    """One blob resident on the Hive's Cell the trail's lease names."""
    store = open_basket_store(db, db.parent / "basket")
    blob = make_blob(b"payload")
    asyncio.run(store.deposit(b"payload", blob, make_manifest(blob)))
    leased = asyncio.run(open_trail(db).query(TrailQuery(kind="cell.leased")))
    cell = CellId(leased[0].subject_id)
    asyncio.run(store.mark_resident(cell, blob.sha256, FakeClock().now()))


async def _abscond(hive: _Hive, answers: str) -> Result:
    """Run `hive abscond` with `answers` on stdin, one per prompt."""
    args = ("abscond", "--reason", "leaving this machine", "--manifest", str(hive.path))
    return await Terminal().hive(*args, "--password-stdin", stdin=answers)


async def _absconded(hive: _Hive) -> tuple[PheromoneEvent, ...]:
    """Every `guard.absconded` on the Hive's trail (opened off this loop, as the CLI opens it)."""
    trail = await asyncio.to_thread(open_trail, hive.manifest.resolve_path(hive.manifest.hive.db))
    return await trail.query(TrailQuery(kind=ABSCONDED_KIND))


async def _cells(hive: _Hive) -> int:
    """How many Virtual Cells the fake backend still holds for this Hive."""
    return len(await hive.parts.registry.get("fake").list_cells(hive.manifest.hive.id))


async def test_the_human_absconds_after_re_auth_review_and_phrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hive = await _seeded(tmp_path, monkeypatch)

    result = await _abscond(hive, _ANSWERS)

    assert result.exit_code == 0, result.output
    assert "[1]" in result.output and "[2]" in result.output  # Both Leavings were reviewed.
    assert await _cells(hive) == 0
    assert not hive.lease_scratch.exists() and not hive.temp_dir.exists()
    assert not hive.ticked.exists() and hive.kept.exists()  # Only the ticked one is removed.
    [event] = await _absconded(hive)
    assert event.actor == "human" and event.payload["reason"] == "leaving this machine"
    assert event.payload["confirmation"] == {
        "reauth": "operator_password",
        "phrase": "typed",
        "scope_review": "confirmed",
        "leavings_ticked": 1,
    }
    counts = (event.payload["containers_destroyed"], event.payload["resident_evicted"])
    assert counts == (1, 1)


@pytest.mark.parametrize(
    "answers",
    [
        "not-the-password\n",  # The re-auth fails: nothing is even read.
        f"{PASSWORD}\n\nn\n",  # The review is declined.
        f"{PASSWORD}\n\ny\nabscond\n",  # The phrase is wrong.
    ],
    ids=["password", "review", "phrase"],
)
async def test_any_refusal_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: str
) -> None:
    hive = await _seeded(tmp_path, monkeypatch)

    result = await _abscond(hive, answers)

    assert result.exit_code == 1
    assert "hive abscond refused" in result.output
    assert await _cells(hive) == 1
    assert hive.lease_scratch.exists() and hive.temp_dir.exists() and hive.ticked.exists()
    assert await _absconded(hive) == ()
