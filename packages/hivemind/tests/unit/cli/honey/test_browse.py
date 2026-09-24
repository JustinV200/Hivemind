"""Tests for hivemind.cli.honey.browse: `hive honey ls`, `cat` and `propose`.

Fits into the Hive:
    Mirrors src/hivemind/cli/honey/browse.py (codingrules section 3). Drives the real typer app
    against a real fake-provider manifest and SQLite file (`harness`), with Cell Wax and Bee Bread
    written through the real memory tier the way a running Hive would have left them.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.honey.browse for the module under test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from unit.cli.honey.harness import db_path, invoke, make_hive, rows, seed_nectar

from hivemind.cell import HoneyClearance
from hivemind.cli.stores import open_honey_store, open_memory
from hivemind.manifest import load_manifest
from hivemind.memory import (
    MemoryContext,
    MemoryIdentity,
    WaxProposalInput,
    WaxSeverity,
    WaxState,
    deposit_transcript,
    propose_wax,
    write_wax,
)
from waggle.clock import SystemClock
from waggle.ids import CellId, new_cell_id, new_task_id
from waggle.messages.cell.wax import WaxDecision, WaxOrigin

_WAX_TEXT = "The Wi-Fi on this Cell drops every hour."


def _memory_context(manifest: Path) -> MemoryContext:
    """Build a system MemoryContext over the Hive's own memory store."""
    loaded = load_manifest(manifest, environ={})
    identity = MemoryIdentity(hive_id=loaded.hive.id, node_id=loaded.hive.node_id, actor="system")
    return MemoryContext(
        store=open_memory(db_path(manifest)), identity=identity, clock=SystemClock()
    )


def _write_wax(manifest: Path, cell: CellId) -> str:
    """Propose and write one CAUTION about `cell`, as the Queen would; return its id."""
    ctx = _memory_context(manifest)
    inputs = WaxProposalInput(
        cell_id=cell,
        severity=WaxSeverity.CAUTION,
        text=_WAX_TEXT,
        reason="Seen twice.",
        clearance=HoneyClearance.C1,
        origin=WaxOrigin.HUMAN,
    )

    async def propose_and_write() -> str:
        wax = await propose_wax(inputs, 4_000, ctx)
        return (await write_wax(wax, WaxDecision.AUTOPILOT, "own Cell", ctx)).id

    return asyncio.run(propose_and_write())


def test_ls_root_lists_the_five_folders(tmp_path: Path) -> None:
    result = invoke(make_hive(tmp_path), "ls")

    assert result.exit_code == 0, result.output
    for folder in ("/hive", "/cells", "/bees", "/tasks", "/bee-bread"):
        assert folder in result.stdout


def test_ls_hive_lists_rows_with_label_and_provenance_as_json(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    seed_nectar(manifest, "The staging config lives at /etc/widgets/staging.toml.")
    assert invoke(manifest, "ripen", "--now").exit_code == 0
    (row,) = rows(manifest)

    result = invoke(manifest, "ls", "/hive", "--json")

    assert result.exit_code == 0, result.output
    listing = json.loads(result.stdout)
    (entry,) = listing["entries"]
    assert (entry["path"], entry["clearance"], entry["detail"]) == (row.path, "C1", "SUMMARY")
    assert entry["provenance"]["task_id"] == row.task_id


def test_ls_pages_with_limit_and_offset(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    for index in range(3):
        seed_nectar(manifest, f"Finding number {index} about the widget factory.")
    assert invoke(manifest, "ripen", "--now").exit_code == 0

    first = invoke(manifest, "ls", "/hive", "--limit", "2")
    last = json.loads(
        invoke(manifest, "ls", "/hive", "--limit", "2", "--offset", "2", "--json").stdout
    )

    assert "more entries exist" in first.stdout
    assert (len(last["entries"]), last["is_truncated"]) == (1, False)


def test_ls_shows_a_cells_live_wax_and_finds_the_cell_in_cells(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    cell = new_cell_id(SystemClock())
    wax_id = _write_wax(manifest, cell)

    cells = invoke(manifest, "ls", "/cells")
    wax = invoke(manifest, "ls", f"/cells/{cell}/wax")
    note = invoke(manifest, "cat", f"/cells/{cell}/wax/{wax_id}")

    assert f"/cells/{cell}" in cells.stdout and "1 wax" in cells.stdout
    assert f"/cells/{cell}/wax/{wax_id}" in wax.stdout
    assert "CAUTION" in wax.stdout
    assert note.exit_code == 0, note.output
    assert _WAX_TEXT in note.stdout


def test_ls_and_cat_read_recent_bee_bread(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    task = new_task_id(SystemClock())
    entry = asyncio.run(
        deposit_transcript(
            "make build\nlinker ok\n", task, HoneyClearance.C1, _memory_context(manifest)
        )
    )

    listing = invoke(manifest, "ls", "/bee-bread")
    document = invoke(manifest, "cat", f"/bee-bread/{entry.id}", "--json")

    assert f"/bee-bread/{entry.id}" in listing.stdout
    payload = json.loads(document.stdout)
    assert (payload["kind"], payload["document"]["payload"]) == (
        "BEE_BREAD",
        "make build\nlinker ok\n",
    )


def test_cat_prints_a_honey_row_in_full(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    seed_nectar(manifest, "The staging config lives at /etc/widgets/staging.toml.")
    assert invoke(manifest, "ripen", "--now").exit_code == 0
    (row,) = rows(manifest)

    text = invoke(manifest, "cat", row.path)
    as_json = json.loads(invoke(manifest, "cat", row.path, "--json").stdout)

    assert text.exit_code == 0, text.output
    for line in ("clearance: C1", "scope: hive", "kind: FINDING", "embedding model: test-model"):
        assert line in text.stdout
    assert "/etc/widgets/staging.toml" in text.stdout
    assert (as_json["kind"], as_json["document"]["id"]) == ("HONEY", row.id)


def test_cat_exits_1_for_nothing_visible_and_2_for_a_folder(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)

    missing = invoke(manifest, "cat", "/hive/honey_01ARZ3NDEKTSV4RRFFQ69G5FAV")
    folder = invoke(manifest, "cat", "/hive")
    nowhere = invoke(manifest, "ls", "/nectar")

    assert missing.exit_code == 1
    assert "browse_not_found" in missing.stderr
    assert (folder.exit_code, nowhere.exit_code) == (2, 2)
    assert "folder" in folder.stderr


def test_propose_from_hive_queues_a_note_for_the_house_bee(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)

    result = invoke(manifest, "propose", "/hive", "Build tip", "Run make clean first.")

    assert result.exit_code == 0, result.output
    assert "queued note: honeynote_" in result.stdout
    (proposal,) = asyncio.run(open_honey_store(db_path(manifest)).pending_proposals(10))
    assert (proposal.scope, proposal.title) == ("hive", "Build tip")


def test_propose_from_a_cell_folder_files_proposed_cell_wax(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    cell = new_cell_id(SystemClock())

    result = invoke(manifest, "propose", f"/cells/{cell}", "Disk", "The /data disk fills up.")

    assert result.exit_code == 0, result.output
    assert "proposed Cell Wax: wax_" in result.stdout
    store = open_memory(db_path(manifest))
    (wax,) = asyncio.run(store.list_wax(cell, frozenset({WaxState.PROPOSED}), HoneyClearance.C2))
    assert (wax.text, wax.clearance, wax.origin) == (
        "The /data disk fills up.",
        HoneyClearance.C2,
        WaxOrigin.HUMAN,
    )
    assert asyncio.run(open_honey_store(db_path(manifest)).pending_proposals(10)) == ()


def test_propose_refuses_empty_text_with_exit_2(tmp_path: Path) -> None:
    result = invoke(make_hive(tmp_path), "propose", "/hive", "Title", "   ")

    assert result.exit_code == 2
    assert "text" in result.stderr
