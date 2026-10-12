"""Test hivemind.entrance.routes.screens.thoughts: a bee's thoughts page by page, behind C2.

Records written the way every bee writes them are paged newest first with a cursor and one bee's
alone with a principal; the Queen's autopilot decisions come with the page; a malformed cursor is a
422; and the read needs ``observe:thoughts`` and ``honey:clearance:c2`` both.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/screens/thoughts.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta

from builders.entrance.serving import ProgramGrant, ServingRig, serving
from builders.entrance.views import trail_event
from builders.memory import make_episode

from hivemind.cell import HoneyClearance
from hivemind.memory import EpisodeRecord, MemoryContext, record_episode

_THOUGHTS = ProgramGrant(capabilities=("observe", "observe:thoughts", "honey:clearance:c2"))


async def _think(rig: ServingRig, principal: str, step: int) -> EpisodeRecord:
    """Record one C2 episode for ``principal`` exactly as a bee does, and return it."""
    at = rig.clock.now() + timedelta(milliseconds=step)
    record = make_episode(rig.clock, principal=principal, clearance=HoneyClearance.C2, at=at)
    await record_episode(record, MemoryContext(rig.deps.memory, rig.deps.identity, rig.clock))
    return record


async def test_records_page_newest_first_and_the_queens_decisions_come_with_them() -> None:
    async with serving() as rig:
        client, session = await rig.program(_THOUGHTS)
        first = await _think(rig, "queen", 0)
        garden = await _think(rig, "warden_garden", 1)
        last = await _think(rig, "queen", 2)
        await rig.deps.trail.record(
            trail_event(rig, "queen.decided", rig.deps.identity.hive_id, {"action": "CLUSTER"})
        )

        page_one = await client.call(session, "GET", "/v1/thoughts?limit=2")
        cursor = page_one.json()["next_cursor"]
        page_two = await client.call(session, "GET", f"/v1/thoughts?limit=2&cursor={cursor}")
        queens = await client.call(session, "GET", "/v1/thoughts?principal=queen")
        wardens = await client.call(session, "GET", "/v1/thoughts?principal=warden_garden")

    assert page_one.status_code == 200, page_one.text
    assert [e["id"] for e in page_one.json()["episodes"]] == [last.id, garden.id]
    assert [e["id"] for e in page_two.json()["episodes"]] == [first.id]
    assert page_two.json()["next_cursor"] is None
    assert [e["id"] for e in queens.json()["episodes"]] == [last.id, first.id]
    assert [d["action"] for d in queens.json()["decisions"]] == ["CLUSTER"]
    assert wardens.json()["decisions"] == []


async def test_a_malformed_cursor_is_a_422_and_the_read_needs_thoughts_and_c2() -> None:
    async with serving() as rig:
        client, session = await rig.program(_THOUGHTS)
        observer, observer_session = await rig.program(ProgramGrant(capabilities=("observe",)))
        uncleared, uncleared_session = await rig.program(
            ProgramGrant(capabilities=("observe", "observe:thoughts"))
        )

        bad = await client.call(session, "GET", "/v1/thoughts?cursor=nonsense")
        statuses = [
            (await observer.call(observer_session, "GET", "/v1/thoughts")).status_code,
            (await uncleared.call(uncleared_session, "GET", "/v1/thoughts")).status_code,
        ]

    assert bad.status_code == 422
    assert statuses == [403, 403]
