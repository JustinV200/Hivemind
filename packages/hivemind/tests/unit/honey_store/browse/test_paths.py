"""Tests for hivemind.honey_store.browse.paths: the Honey browser's path grammar.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/paths.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.paths for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.honey_store.browse.errors import BrowsePathError
from hivemind.honey_store.browse.paths import (
    MAX_PATH_CHARS,
    PathKind,
    bee_bread_entry_path,
    index_path,
    parse_path,
    wax_folder_path,
    wax_note_path,
)
from hivemind.honey_store.scope import folder_for_scope

_CELL = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"
_TASK = "task_01ARZ3NDEKTSV4RRFFQ69G5FAV"
_BEE = "worker_01ARZ3NDEKTSV4RRFFQ69G5FAV"
_HONEY = "honey_01ARZ3NDEKTSV4RRFFQ69G5FAV"
_WAX = "wax_01ARZ3NDEKTSV4RRFFQ69G5FAV"


@pytest.mark.parametrize("raw", ["/", "", "//", "///"])
def test_parse_path_names_the_root_however_it_is_spelled(raw: str) -> None:
    parsed = parse_path(raw)

    assert parsed.kind is PathKind.ROOT
    assert parsed.path == "/"
    assert parsed.is_folder


def test_parse_path_reads_the_hive_folder_and_one_of_its_rows() -> None:
    folder = parse_path("/hive/")
    row = parse_path(f"hive/{_HONEY}")

    assert (folder.kind, folder.path, folder.scope) == (PathKind.SCOPE, "/hive", "hive")
    assert (row.kind, row.path, row.scope, row.item_id) == (
        PathKind.HONEY,
        f"/hive/{_HONEY}",
        "hive",
        _HONEY,
    )
    assert not row.is_folder


@pytest.mark.parametrize(
    ("folder", "scope_kind"), [("cells", "cell"), ("bees", "bee"), ("tasks", "task")]
)
def test_parse_path_reads_each_index_folder(folder: str, scope_kind: str) -> None:
    parsed = parse_path(f"/{folder}")

    assert parsed.kind is PathKind.SCOPE_INDEX
    assert parsed.scope_kind == scope_kind
    assert parsed.path == index_path(scope_kind) == f"/{folder}"


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        (f"/cells/{_CELL}", f"cell:{_CELL}"),
        (f"/bees/{_BEE}", f"bee:{_BEE}"),
        (f"/tasks/{_TASK}", f"task:{_TASK}"),
    ],
)
def test_parse_path_scope_folders_round_trip_through_folder_for_scope(
    path: str, scope: str
) -> None:
    parsed = parse_path(path)
    row = parse_path(f"{path}/{_HONEY}")

    assert (parsed.kind, parsed.scope) == (PathKind.SCOPE, scope)
    assert folder_for_scope(scope) == parsed.path
    assert (row.kind, row.scope, row.item_id) == (PathKind.HONEY, scope, _HONEY)


def test_parse_path_reads_a_cells_wax_folder_and_one_note() -> None:
    folder = parse_path(f"/cells/{_CELL}/wax")
    note = parse_path(f"/cells/{_CELL}/wax/{_WAX}")

    assert (folder.kind, folder.scope) == (PathKind.WAX_FOLDER, f"cell:{_CELL}")
    assert folder.path == wax_folder_path(f"cell:{_CELL}")
    assert (note.kind, note.item_id) == (PathKind.WAX_NOTE, _WAX)
    assert note.path == wax_note_path(f"cell:{_CELL}", _WAX)


def test_parse_path_treats_wax_as_a_row_id_outside_a_cell() -> None:
    parsed = parse_path(f"/tasks/{_TASK}/wax")

    assert (parsed.kind, parsed.item_id) == (PathKind.HONEY, "wax")


def test_parse_path_reads_bee_bread_and_one_entry() -> None:
    folder = parse_path("/bee-bread")
    entry = parse_path("/bee-bread/event_01ARZ3NDEKTSV4RRFFQ69G5FAV")

    assert folder.kind is PathKind.BEE_BREAD
    assert (entry.kind, entry.item_id) == (
        PathKind.BEE_BREAD_ENTRY,
        "event_01ARZ3NDEKTSV4RRFFQ69G5FAV",
    )
    assert entry.path == bee_bread_entry_path("event_01ARZ3NDEKTSV4RRFFQ69G5FAV")


@pytest.mark.parametrize(
    "raw",
    [
        "/nectar",  # No such top-level folder.
        "/hive/a/b",  # Deeper than /hive goes.
        f"/cells/{_CELL}/wax/{_WAX}/more",  # Deeper than a wax note.
        f"/tasks/{_TASK}/{_HONEY}/more",  # Deeper than a row.
        "/bee-bread/a/b",  # Deeper than an entry.
        "/cells/bad*id",  # A glob character is never a scope id.
        "/hive/../cells",  # No relative navigation.
        "/./hive",
    ],
)
def test_parse_path_refuses_what_the_tree_does_not_hold(raw: str) -> None:
    with pytest.raises(BrowsePathError):
        parse_path(raw)


def test_parse_path_refuses_an_oversized_path() -> None:
    with pytest.raises(BrowsePathError, match="longer than"):
        parse_path("/hive/" + "x" * MAX_PATH_CHARS)
