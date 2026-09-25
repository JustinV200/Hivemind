"""Tests for hivemind.honey_store.browse.sources: the note models and the browser's deps.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/sources.py (codingrules section 3). Every pydantic
    boundary model gets a round trip and a rejection (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.sources for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from unit.honey_store.browse.harness import open_browse_hive

from hivemind.honey_store.browse.sources import BeeBreadNote, WaxNote
from waggle.ids import new_cell_id


async def test_wax_note_round_trips_and_refuses_a_bad_id(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)
    note = hive.wax_note(new_cell_id(hive.clock))

    assert WaxNote.model_validate_json(note.model_dump_json()) == note
    with pytest.raises(ValidationError):
        note.model_validate({**note.model_dump(), "id": "not-a-wax-id"})


async def test_bee_bread_note_round_trips_and_refuses_an_unknown_field(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)
    note = hive.bee_bread_note()

    assert BeeBreadNote.model_validate_json(note.model_dump_json()) == note
    with pytest.raises(ValidationError):
        BeeBreadNote.model_validate({**note.model_dump(), "extra": True})


async def test_browser_deps_has_no_retriever_unless_given_one(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)

    assert hive.deps().retriever is None
    assert hive.deps(searchable=True).retriever is not None
