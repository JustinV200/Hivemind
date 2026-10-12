"""Test hivemind.entrance.reads.honey: a device as a reader of Honey, and what it gathers for it.

``honey_viewer`` turns each ``observe:honey:<scope>`` a device holds into the ``honey:read``
scope that matches the same scopes, and its highest ``honey:clearance`` rung into a ceiling
(``C1`` when it holds none); a grant a glob would read differently from a prefix is dropped, never
widened, and a capability of another family grants nothing. Over a real SQLite Honey Store the
reads then cut the root, a folder, an item and a search to that reader.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/honey.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from builders.entrance.honey import WORDS, Seeded, seed_honey

from hivemind.cell import HoneyClearance
from hivemind.entrance.errors import (
    HoneyFolderNotFoundError,
    HoneyItemNotFoundError,
    HoneyUnavailableError,
)
from hivemind.entrance.gate import HiveReads, HoneyReads
from hivemind.entrance.reads.honey import (
    CAP_NOTE,
    HoneyViewer,
    honey_viewer,
    locate_folder,
    locate_item,
    read_folder,
    read_item,
    read_search,
    require_honey,
    scope_of,
)
from hivemind.guard import CapabilitySet
from hivemind.honey_store import is_readable, readable_globs
from hivemind.honey_store.scope import cell_scope, task_scope
from hivemind.observation import HoneySearchQuery, HoneyTreeQuery

_DEVICE = "device_01H0000000000000000000000A"


def _viewer(*held: str) -> HoneyViewer:
    """Build the viewer of a device that holds `held`."""
    return honey_viewer(_DEVICE, CapabilitySet.parse(*held))


@pytest.fixture
async def seeded(tmp_path: Path) -> Seeded:
    """Seed one Honey Store for a test."""
    return await seed_honey(tmp_path)


def test_each_observe_honey_scope_becomes_the_honey_read_scope_that_matches_the_same() -> None:
    viewer = _viewer(
        "observe", "observe:honey:hive", "observe:honey:cell:*", "observe:honey:task:t1"
    )

    assert readable_globs(viewer.reader.capabilities) == ("cell:*", "hive", "task:t1")
    assert is_readable("cell:cell_anything", viewer.reader.capabilities)
    assert not is_readable("bee:worker_x", viewer.reader.capabilities)
    assert viewer.reader.requester == _DEVICE and not viewer.reader.is_night_veil


def test_a_grant_a_glob_would_read_differently_from_a_prefix_is_dropped_not_widened() -> None:
    viewer = _viewer(
        "observe:honey:cell:a?b", "observe:honey:[x]", "observe:honey:ce*ll", "observe:honey:hive"
    )

    assert readable_globs(viewer.reader.capabilities) == ("hive",)


def test_only_observe_honey_grants_a_scope() -> None:
    viewer = _viewer("observe", "honey:read:*", "observe:thoughts", "entrance:submit")

    assert readable_globs(viewer.reader.capabilities) == ()
    assert not viewer.shows("/hive") and not viewer.shows("/cells")


@pytest.mark.parametrize(
    ("held", "ceiling"),
    [
        ((), HoneyClearance.C1),
        (("honey:clearance:c0",), HoneyClearance.C0),
        (("honey:clearance:c1",), HoneyClearance.C1),
        (("honey:clearance:c2",), HoneyClearance.C2),
        (("honey:clearance:c0", "honey:clearance:c1"), HoneyClearance.C1),
    ],
)
def test_the_ceiling_is_the_highest_clearance_rung_held_c1_without_any(
    held: tuple[str, ...], ceiling: HoneyClearance
) -> None:
    assert _viewer("observe:honey:*", *held).ceiling is ceiling


@pytest.mark.parametrize(
    ("held", "shown"),
    [
        (("observe:honey:*",), ["/hive", "/cells", "/bees", "/tasks", "/bee-bread"]),
        (("observe:honey:hive",), ["/hive", "/bee-bread"]),
        (("observe:honey:cell:*",), ["/cells"]),
        (("observe:honey:cell:cell_x",), ["/cells"]),
        (("observe:honey:bee:*", "observe:honey:task:*"), ["/bees", "/tasks", "/bee-bread"]),
        (("observe:honey:c*",), ["/cells"]),
        ((), []),
    ],
)
def test_the_root_shows_a_folder_only_when_some_scope_under_it_is_reachable(
    held: tuple[str, ...], shown: list[str]
) -> None:
    viewer = _viewer(*held)

    folders = ["/hive", "/cells", "/bees", "/tasks", "/bee-bread"]
    assert [folder for folder in folders if viewer.shows(folder)] == shown


def test_a_folder_path_must_name_a_folder_of_the_tree() -> None:
    assert locate_folder("/cells/cell_x/wax").scope == "cell:cell_x"
    assert locate_folder("hive/").path == "/hive"
    for bad in ("/nope", "/hive/honey_x", "/a/../b", "/cells/bad.id"):
        with pytest.raises(HoneyFolderNotFoundError):
            locate_folder(bad)


def test_a_hive_without_honey_refuses_the_reads(seeded: Seeded) -> None:
    with pytest.raises(HoneyUnavailableError):
        require_honey(_reads(None))
    assert require_honey(_reads(seeded.browser)) is seeded.browser


async def test_the_root_pages_over_the_folders_the_viewer_reaches(seeded: Seeded) -> None:
    viewer = _viewer("observe:honey:*")

    first = await read_folder(seeded.browser, viewer, locate_folder("/"), HoneyTreeQuery(limit=2))
    second = await read_folder(
        seeded.browser,
        viewer,
        locate_folder("/"),
        HoneyTreeQuery(limit=2, cursor=first.next_cursor),
    )

    assert [f.path for f in first.folders] == ["/hive", "/cells"] and first.next_cursor
    assert [f.path for f in second.folders] == ["/bees", "/tasks"] and second.next_cursor


async def test_a_folder_holds_exactly_what_the_viewers_scopes_and_ceiling_allow(
    seeded: Seeded,
) -> None:
    plain = _viewer(f"observe:honey:{cell_scope(seeded.cell)}", "observe:honey:hive")
    royal = _viewer("observe:honey:*", "honey:clearance:c2")

    mine = await read_folder(seeded.browser, plain, locate_folder("/hive"), HoneyTreeQuery())
    theirs = await read_folder(seeded.browser, royal, locate_folder("/hive"), HoneyTreeQuery())
    tasks = await read_folder(seeded.browser, royal, locate_folder("/tasks"), HoneyTreeQuery())

    assert [item.id for item in mine.items] == [seeded.shared.id]
    assert {item.id for item in theirs.items} == {seeded.shared.id, seeded.shared_note.id}
    assert [folder.path for folder in tasks.folders] == [f"/tasks/{seeded.task}"]


async def test_an_item_is_located_for_its_scope_and_label_then_read_as_the_viewer(
    seeded: Seeded,
) -> None:
    ids = (seeded.shared_note.id, seeded.wax.id, seeded.bread.id)
    found = [await locate_item(seeded.browser, _DEVICE, item) for item in ids]
    plain, royal = _viewer("observe:honey:*"), _viewer("observe:honey:*", "honey:clearance:c2")

    assert [scope_of(document) for document in found] == [
        ("hive", HoneyClearance.C2),
        (cell_scope(seeded.cell), HoneyClearance.C1),
        (task_scope(seeded.task), HoneyClearance.C1),
    ]
    # The privileged read finds even what the viewer may not see; the viewer's own read does not.
    with pytest.raises(HoneyItemNotFoundError):
        await read_item(seeded.browser, plain, seeded.shared_note.id)
    assert (await read_item(seeded.browser, royal, seeded.shared_note.id)).clearance.value == "C2"
    with pytest.raises(HoneyItemNotFoundError):
        await locate_item(seeded.browser, _DEVICE, "honey_0000000000000000000000000A")


async def test_a_search_covers_only_the_viewers_scopes_and_hides_what_it_withheld(
    seeded: Seeded,
) -> None:
    plain = _viewer("observe:honey:hive")
    royal = _viewer("observe:honey:*", "honey:clearance:c2")
    query = HoneySearchQuery(q=WORDS, limit=50)

    mine = await read_search(seeded.browser, plain, locate_folder("/"), query)
    theirs = await read_search(seeded.browser, royal, locate_folder("/"), query)
    nothing = await read_search(seeded.browser, plain, locate_folder("/cells"), query)

    assert [hit.id for hit in mine.hits] == [seeded.shared.id]
    assert len(theirs.hits) == 5 and theirs.note == ""
    assert nothing.hits == [] and "No folder here holds Honey" in nothing.note
    with pytest.raises(HoneyFolderNotFoundError):
        await read_search(seeded.browser, royal, locate_folder("/bee-bread"), query)


async def test_a_search_says_when_the_ranking_ceiling_ends_a_long_result(
    tmp_path: Path,
) -> None:
    seeded = await seed_honey(tmp_path)
    for number in range(50):
        await seeded.hive.ripen(f"Row {number}: the {WORDS} again.")

    page = await read_search(
        seeded.browser,
        _viewer("observe:honey:*"),
        locate_folder("/hive"),
        HoneySearchQuery(q=WORDS, limit=50),
    )

    assert len(page.hits) == 50 and page.next_cursor is None and page.note == CAP_NOTE


async def test_a_search_needs_a_retriever(seeded: Seeded) -> None:
    unsearchable = seeded.hive.browser()

    with pytest.raises(HoneyUnavailableError):
        await read_search(
            unsearchable,
            _viewer("observe:honey:*"),
            locate_folder("/hive"),
            HoneySearchQuery(q=WORDS),
        )


@dataclass(frozen=True)
class _OnlyHoney:
    """Stand in for HiveReads where only its `honey` field is read."""

    honey: HoneyReads | None


def _reads(honey: HoneyReads | None) -> HiveReads:
    """Return something `require_honey` reads as the Entrance's reads, carrying only `honey`."""
    return cast(HiveReads, _OnlyHoney(honey))
