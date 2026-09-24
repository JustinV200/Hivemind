"""Tests for hivemind.cli.honey.render: how listings, documents and slot status are printed.

Most of this module is exercised through the commands (test_query.py, test_browse.py,
test_maintain.py); this covers the pieces a command test cannot pin down on its own: the JSON
kind of each document shape, and both forms of a slot's status line.

Fits into the Hive:
    Mirrors src/hivemind/cli/honey/render.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.honey.render for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from unit.honey_store.browse.harness import open_browse_hive

from hivemind.cli.honey.context import SlotStatus
from hivemind.cli.honey.render import document_json, print_slot
from waggle.ids import new_cell_id


async def test_document_json_names_each_document_kind(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)
    (row,) = await hive.ripen("A row.")
    wax = hive.wax_note(new_cell_id(hive.clock))
    entry = hive.bee_bread_note()

    kinds = [json.loads(document_json("/x", document))["kind"] for document in (row, wax, entry)]

    assert kinds == ["HONEY", "WAX", "BEE_BREAD"]


def test_print_slot_names_the_model_or_the_reason(capsys: pytest.CaptureFixture[str]) -> None:
    print_slot("embedder", SlotStatus(model="test-model", reason=None), "unused")
    print_slot("ripener", SlotStatus(model=None, reason="code: why"), "summaries are heuristic")

    out = capsys.readouterr().out.splitlines()

    assert out == ["embedder: test-model", "ripener: none (code: why); summaries are heuristic"]
