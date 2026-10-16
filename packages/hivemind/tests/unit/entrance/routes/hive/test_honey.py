"""Test hivemind.entrance.routes.hive.honey: the Honey browser's tree, items and search.

Over real listeners and a real SQLite Honey Store seeded in two Cells' scopes, a task's and the
shared one, with a Cell's live wax and a Bee Bread entry. The scopes a device holds decide exactly
what it sees: two sessions with different ``observe:honey:*`` capabilities get trees that differ
as the scopes say, a folder whose scope a device lacks is a 403 and is missing from its parent, an
item above the device's clearance or outside its scopes is a 403 that names neither scope nor
label, an unknown folder or item is a 404, pages continue by cursor, and the routes only read.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/hive/honey.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from builders.entrance.honey import WORDS, Seeded, seed_honey
from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import ProgramGrant, RigOptions, ServingRig, serving

from hivemind.entrance.gate import BOTH_LISTENERS, RouteEffect
from hivemind.entrance.routes.registry import RESOURCE_ROUTES
from hivemind.honey_store.browse import EMPTY_INDEX_REASON

C2 = "honey:clearance:c2"  # What a device needs to read a C2 item.
Viewer = tuple[LandingClient, LandingSession]


@pytest.fixture
async def seeded(tmp_path: Path) -> Seeded:
    """Seed one Honey Store for a test."""
    return await seed_honey(tmp_path)


@asynccontextmanager
async def honey_rig(seeded: Seeded, *, remote: bool = False) -> AsyncIterator[ServingRig]:
    """Serve an Entrance whose Honey routes read the seeded store."""
    async with serving(RigOptions(honey=seeded.browser, remote=remote)) as rig:
        yield rig


async def viewer(rig: ServingRig, *capabilities: str, remote: bool = False) -> Viewer:
    """Enrol a program holding `observe` and `capabilities`, approved and logged in."""
    grant = ProgramGrant(capabilities=("observe", *capabilities), remote=remote)
    return await rig.program(grant)


async def get(who: Viewer, route: str, **query: str | int) -> httpx.Response:
    """GET a Honey route with a query string; paths keep their slashes."""
    pairs = "&".join(f"{key}={quote(str(value), safe='/')}" for key, value in query.items())
    client, session = who
    return await client.call(session, "GET", f"/v1/honey/{route}" + (f"?{pairs}" if pairs else ""))


def paths(response: httpx.Response, key: str) -> list[str]:
    """Return the paths of a folder body's `folders` or `items`, or a search body's `hits`."""
    assert response.status_code == 200, response.text
    return [entry["path"] for entry in response.json()[key]]


async def test_the_root_lists_only_the_folders_a_devices_scopes_reach(seeded: Seeded) -> None:
    async with honey_rig(seeded) as rig:
        everything = await viewer(rig, "observe:honey:*")
        shared_only = await viewer(rig, "observe:honey:hive")
        cells_only = await viewer(rig, "observe:honey:cell:*")
        nothing = await viewer(rig)

        roots = [await get(who, "tree") for who in (everything, shared_only, cells_only, nothing)]

    assert [paths(response, "folders") for response in roots] == [
        ["/hive", "/cells", "/bees", "/tasks", "/bee-bread"],
        ["/hive", "/bee-bread"],
        ["/cells"],
        [],
    ]


async def test_two_sessions_with_different_scopes_get_trees_that_differ_as_the_scopes_say(
    seeded: Seeded,
) -> None:
    one = f"observe:honey:cell:{seeded.cell}"
    async with honey_rig(seeded) as rig:
        # Both read up to C2, so only the scopes tell them apart.
        one_cell = await viewer(rig, "observe:honey:hive", one, C2)
        all_cells = await viewer(rig, "observe:honey:hive", "observe:honey:cell:*", C2)

        cells = [await get(who, "tree", path="/cells") for who in (one_cell, all_cells)]
        shared = [await get(who, "tree", path="/hive") for who in (one_cell, all_cells)]
        mine = await get(one_cell, "tree", path=f"/cells/{seeded.cell}")
        other = [
            await get(who, "tree", path=f"/cells/{seeded.other_cell}")
            for who in (one_cell, all_cells)
        ]

    assert paths(cells[0], "folders") == [f"/cells/{seeded.cell}"]
    assert paths(cells[1], "folders") == sorted(
        [f"/cells/{seeded.cell}", f"/cells/{seeded.other_cell}"]
    )
    # The shared folder is the same for both: both hold observe:honey:hive.
    assert shared[0].json() == shared[1].json()
    assert paths(mine, "items") == [seeded.cell_row.path]
    # The Cell the first device does not hold is a 403 for it, and its rows the second's to read.
    assert other[0].status_code == 403
    assert other[0].json()["capability"] == f"observe:honey:cell:{seeded.other_cell}"
    assert paths(other[1], "items") == [seeded.other_cell_row.path]


async def test_a_folder_is_a_403_without_its_scope_and_nothing_of_it_shows_elsewhere(
    seeded: Seeded,
) -> None:
    async with honey_rig(seeded) as rig:
        hive_only = await viewer(rig, "observe:honey:hive")
        refused = [
            await get(hive_only, "tree", path=path)
            for path in (
                f"/cells/{seeded.cell}",
                f"/cells/{seeded.cell}/wax",
                f"/tasks/{seeded.task}",
                "/bees/worker_01H0000000000000000000000A",
            )
        ]
        search = await get(hive_only, "search", path=f"/tasks/{seeded.task}", q=WORDS)
        everywhere = await get(hive_only, "search", path="/", q=WORDS)
        tasks = await get(hive_only, "tree", path="/tasks")
        bread = await get(hive_only, "tree", path="/bee-bread")

    assert [response.status_code for response in refused] == [403, 403, 403, 403]
    assert search.status_code == 403
    # A search from the root covers only the scopes the device holds, at its own clearance.
    assert paths(everywhere, "hits") == [seeded.shared.path]
    assert paths(tasks, "folders") == []
    # The Bee Bread entry is about a task: filed under a scope this device does not hold.
    assert paths(bread, "items") == []


async def test_a_cells_folder_holds_its_rows_and_its_live_wax(seeded: Seeded) -> None:
    async with honey_rig(seeded) as rig:
        who = await viewer(rig, f"observe:honey:cell:{seeded.cell}")

        folder = await get(who, "tree", path=f"/cells/{seeded.cell}")
        wax = await get(who, "tree", path=f"/cells/{seeded.cell}/wax")

    body = folder.json()
    assert (body["scope"], body["parent"], body["can_search"]) == (
        f"cell:{seeded.cell}",
        "/cells",
        True,
    )
    assert paths(folder, "folders") == [f"/cells/{seeded.cell}/wax"]
    assert paths(folder, "items") == [seeded.cell_row.path]
    assert (wax.json()["parent"], wax.json()["can_search"]) == (f"/cells/{seeded.cell}", False)
    (item,) = wax.json()["items"]
    assert (item["kind"], item["id"], item["severity"]) == ("wax", seeded.wax.id, "CAUTION")
    assert (item["proposer"], item["clearance"]) == (seeded.wax.proposer, "C1")
    assert (item["scope"], item["size_chars"]) == (f"cell:{seeded.cell}", len(seeded.wax.text))
    assert item["expires_at"] is not None


async def test_an_items_label_must_be_within_the_devices_clearance(seeded: Seeded) -> None:
    async with honey_rig(seeded) as rig:
        plain = await viewer(rig, "observe:honey:*")
        royal = await viewer(rig, "observe:honey:*", C2)

        plain_hive = await get(plain, "tree", path="/hive")
        royal_hive = await get(royal, "tree", path="/hive")
        plain_other = await get(plain, "tree", path=f"/cells/{seeded.other_cell}")
        refused = await get(plain, f"items/{seeded.shared_note.id}")
        allowed = await get(royal, f"items/{seeded.shared_note.id}")

    assert paths(plain_hive, "items") == [seeded.shared.path]
    assert plain_hive.json()["ceiling"] == "C1"
    assert sorted(paths(royal_hive, "items")) == sorted(
        [seeded.shared.path, seeded.shared_note.path]
    )
    assert royal_hive.json()["ceiling"] == "C2"
    assert paths(plain_other, "items") == []
    # The refusal names the family only: not the item's scope, not its label.
    assert refused.status_code == 403
    assert refused.json()["capability"] == "observe:honey"
    assert "C2" not in refused.text and "hive" not in refused.json()["detail"].lower()
    assert allowed.status_code == 200
    assert (allowed.json()["clearance"], allowed.json()["kind"]) == ("C2", "note")


async def test_each_kind_of_item_reads_with_its_text_provenance_and_label(seeded: Seeded) -> None:
    async with honey_rig(seeded) as rig:
        who = await viewer(rig, "observe:honey:*", C2)

        row = await get(who, f"items/{seeded.cell_row.id}")
        wax = await get(who, f"items/{seeded.wax.id}")
        bread = await get(who, f"items/{seeded.bread.id}")

    honey, note, entry = row.json(), wax.json(), bread.json()
    assert (honey["kind"], honey["path"], honey["scope"]) == (
        "honey",
        seeded.cell_row.path,
        seeded.cell_row.scope,
    )
    assert honey["content"] == seeded.cell_row.body and honey["summary"] == seeded.cell_row.summary
    assert (honey["clearance"], honey["sources"]) == ("C1", [])
    assert honey["provenance"]["source"] == "BEE"
    assert honey["provenance"]["cell_id"] == seeded.cell_row.cell_id
    assert (note["kind"], note["content"], note["reason"]) == (
        "wax",
        seeded.wax.text,
        seeded.wax.reason,
    )
    assert (note["severity"], note["proposer"]) == ("CAUTION", seeded.wax.proposer)
    assert note["provenance"]["source"] == "BEE" and note["expires_at"] is not None
    assert (entry["kind"], entry["content"], entry["scope"]) == (
        "bee_bread",
        "make build\nok\n",
        f"task:{seeded.task}",
    )
    assert entry["path"] == f"/bee-bread/{seeded.bread.id}"


async def test_an_item_outside_the_devices_scopes_is_a_403_that_names_no_scope(
    seeded: Seeded,
) -> None:
    async with honey_rig(seeded) as rig:
        hive_only = await viewer(rig, "observe:honey:hive", C2)

        refused = [
            await get(hive_only, f"items/{item}")
            for item in (seeded.cell_row.id, seeded.wax.id, seeded.bread.id)
        ]
        shared = await get(hive_only, f"items/{seeded.shared.id}")

    assert [response.status_code for response in refused] == [403, 403, 403]
    assert {response.json()["capability"] for response in refused} == {"observe:honey"}
    assert shared.status_code == 200 and shared.json()["id"] == seeded.shared.id


async def test_an_unknown_folder_or_item_is_a_404_with_its_own_code(seeded: Seeded) -> None:
    async with honey_rig(seeded) as rig:
        who = await viewer(rig, "observe:honey:*", C2)

        folders = [
            await get(who, "tree", path=path)
            for path in ("/nope", "/hive/a/b", "/cells/bad.id", seeded.shared.path)
        ]
        wax_search = await get(who, "search", path=f"/cells/{seeded.cell}/wax", q=WORDS)
        bread_search = await get(who, "search", path="/bee-bread", q=WORDS)
        items = [
            await get(who, f"items/{item}")
            for item in (
                "honey_0000000000000000000000000A",
                "wax_0000000000000000000000000A",
                "event_0000000000000000000000000A",
            )
        ]
        malformed = await get(who, "items/not.an.id")

    assert [response.status_code for response in folders] == [404] * 4
    assert {response.json()["error"] for response in folders} == {
        "hivemind.entrance.honey_folder_not_found"
    }
    assert wax_search.status_code == bread_search.status_code == 404
    assert [response.status_code for response in items] == [404, 404, 404]
    assert {response.json()["error"] for response in items} == {
        "hivemind.entrance.honey_item_not_found"
    }
    assert malformed.status_code == 422


async def test_a_tainted_bee_bread_entry_is_a_404_like_a_missing_one(seeded: Seeded) -> None:
    # A tainted Honey row answers 404; a tainted Bee Bread entry must answer the same, never a
    # 403 naming the tainting trail event, or taint status could be probed through this route.
    tainted = seeded.hive.bee_bread_note(task_id=seeded.task, tainted=True)
    browser = seeded.hive.browser(wax=[seeded.wax], bee_bread=[seeded.bread, tainted])
    async with serving(RigOptions(honey=browser)) as rig:
        who = await viewer(rig, "observe:honey:*", C2)

        refused = await get(who, f"items/{tainted.id}")
        listed = await get(who, "tree", path="/bee-bread")

    assert refused.status_code == 404
    assert refused.json()["error"] == "hivemind.entrance.honey_item_not_found"
    assert "tainted" not in refused.text
    assert paths(listed, "items") == [f"/bee-bread/{seeded.bread.id}"]


async def test_a_folder_pages_by_cursor_without_repeating_or_skipping(seeded: Seeded) -> None:
    for number in range(4):
        await seeded.hive.ripen(f"Extra {number}: the {WORDS} again.")
    async with honey_rig(seeded) as rig:
        who = await viewer(rig, "observe:honey:hive")

        whole = await get(who, "tree", path="/hive")
        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            extra = {"cursor": cursor} if cursor is not None else {}
            page = await get(who, "tree", path="/hive", limit=2, **extra)
            seen += paths(page, "items")
            pages += 1
            cursor = page.json()["next_cursor"]
            if cursor is None:
                break
        malformed = await get(who, "tree", path="/hive", cursor="!!")
        too_many = await get(who, "tree", path="/hive", limit=1000)

    assert len(paths(whole, "items")) == 5 and whole.json()["next_cursor"] is None
    assert seen == paths(whole, "items") and pages == 3
    assert malformed.status_code == too_many.status_code == 422
    assert malformed.json()["error"] == "hivemind.entrance.invalid_request"


async def test_a_search_ranks_hits_within_the_folder_and_pages_by_cursor(seeded: Seeded) -> None:
    for number in range(3):
        await seeded.hive.ripen(f"Extra {number}: the {WORDS} once more.")
    async with honey_rig(seeded) as rig:
        who = await viewer(rig, "observe:honey:*", C2)
        scoped = await viewer(rig, "observe:honey:hive")

        everywhere = await get(who, "search", path="/", q=WORDS, limit=50)
        in_cells = await get(who, "search", path="/cells", q=WORDS)
        mine = await get(who, "search", path=f"/cells/{seeded.cell}", q=WORDS)
        first = await get(who, "search", path="/hive", q=WORDS, limit=2)
        second = await get(
            who, "search", path="/hive", q=WORDS, limit=2, cursor=first.json()["next_cursor"]
        )
        empty = await get(scoped, "search", path="/cells", q=WORDS)
        blank = await get(who, "search", path="/", q="")

    assert len(paths(everywhere, "hits")) == 8
    assert set(paths(in_cells, "hits")) == {seeded.cell_row.path, seeded.other_cell_row.path}
    assert paths(mine, "hits") == [seeded.cell_row.path]
    hit = mine.json()["hits"][0]
    assert (hit["id"], hit["scope"], hit["clearance"]) == (
        seeded.cell_row.id,
        seeded.cell_row.scope,
        "C1",
    )
    assert 0.0 <= hit["score"] <= 1.0 and WORDS.split()[0] in hit["snippet"]
    assert hit["provenance"]["cell_id"] == seeded.cell_row.cell_id
    assert len(paths(first, "hits")) == len(paths(second, "hits")) == 2
    assert not set(paths(first, "hits")) & set(paths(second, "hits"))
    assert first.json()["next_cursor"] is not None
    # The retriever's own count of what it withheld is not part of the page.
    assert set(everywhere.json()) == {"path", "hits", "next_cursor", "note"}
    assert (empty.json()["hits"], empty.json()["note"]) == ([], EMPTY_INDEX_REASON)
    assert blank.status_code == 422


async def test_a_hive_without_a_honey_store_answers_404_on_every_honey_route() -> None:
    async with serving() as rig:
        who = await viewer(rig, "observe:honey:*")

        answers = [
            await get(who, "tree"),
            await get(who, "items/honey_0000000000000000000000000A"),
            await get(who, "search", q=WORDS),
        ]

    assert [response.status_code for response in answers] == [404, 404, 404]
    assert {response.json()["error"] for response in answers} == {
        "hivemind.entrance.honey_unavailable"
    }


async def test_the_remote_listener_serves_the_same_tree(seeded: Seeded) -> None:
    async with honey_rig(seeded, remote=True) as rig:
        local = await viewer(rig, "observe:honey:hive")
        far = await viewer(rig, "observe:honey:hive", remote=True)

        near_tree = await get(local, "tree", path="/hive")
        far_tree = await get(far, "tree", path="/hive")
        far_refused = await get(far, "tree", path=f"/cells/{seeded.cell}")

    assert near_tree.status_code == far_tree.status_code == 200
    assert near_tree.json() == far_tree.json()
    assert far_refused.status_code == 403


async def test_the_gate_still_guards_the_honey_routes(seeded: Seeded) -> None:
    async with honey_rig(seeded) as rig:
        submit_only = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))
        anonymous = rig.client()

        refused = [await get(submit_only, route) for route in ("tree", "search")]
        unsigned = await anonymous.http.get("/v1/honey/tree")

    assert [response.status_code for response in refused] == [403, 403]
    assert unsigned.status_code == 401


async def test_the_honey_routes_only_read(seeded: Seeded) -> None:
    rows = [row for group in RESOURCE_ROUTES for row in group if row.path.startswith("/v1/honey")]
    async with honey_rig(seeded) as rig:
        who = await viewer(rig, "observe:honey:*", C2)
        client, session = who

        writes = [
            await client.call(session, method, "/v1/honey/tree", {"path": "/hive"})
            for method in ("POST", "PUT", "PATCH", "DELETE")
        ]

    assert sorted(row.path for row in rows) == [
        "/v1/honey/items/{item_id}",
        "/v1/honey/search",
        "/v1/honey/tree",
    ]
    assert {(row.method, row.effect, row.listeners) for row in rows} == {
        ("GET", RouteEffect.READ, BOTH_LISTENERS)
    }
    assert {row.access.capability for row in rows} == {"observe"}
    assert all(not row.access.c2 for row in rows)
    assert {response.status_code for response in writes} <= {404, 405}
