"""Test hivemind.entrance.routes.hive.trail: the Pheromone Trail, filtered and paged from a cursor.

Over real listeners and the Queen's own trail: events recorded at one instant are paged with the
cursor (an instant and how many events there were already read) every one exactly once, oldest
first and newest first alike, in the trail's own order; the filters narrow by family, kind and
subject; a task's title never leaves in a payload; and a query string the route does not know,
or a page past the bound, is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/hive/trail.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import ProgramGrant, ServingRig, serving
from builders.entrance.views import trail_event
from builders.tasks import make_graph_draft

from hivemind.pheromone import TrailQuery

_OBSERVE = ProgramGrant(capabilities=("observe",))
_CELL = "cell_0000000000000000000000000A"  # The subject every paged event is about.


async def _same_instant(rig: ServingRig, count: int) -> None:
    """Record ``count`` ``cell.ready`` events all at one instant, on the Queen's node."""
    at = rig.clock.now()
    for _ in range(count):
        event = trail_event(rig, "cell.ready", _CELL).model_copy(update={"at": at})
        await rig.deps.trail.record(event)


async def _every_page(
    client: LandingClient, session: LandingSession, query: dict[str, Any]
) -> list[str]:
    """Follow the cursor from the first page to the last; every event id read, in order."""
    ids: list[str] = []
    cursor: dict[str, Any] = {}
    while True:
        params = {**query, **{key: value for key, value in cursor.items() if value is not None}}
        page = await client.call(session, "GET", f"/v1/trail?{urlencode(params)}")
        assert page.status_code == 200, page.text
        ids.extend(event["id"] for event in page.json()["events"])
        cursor = page.json()["next"] or {}
        if not cursor:
            return ids


async def test_events_at_one_instant_are_paged_each_exactly_once_either_way() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        await _same_instant(rig, 5)
        oldest_first = await rig.deps.trail.query(TrailQuery(kind="cell.ready"))

        forward = await _every_page(client, session, {"kind": "cell.ready", "limit": 2})
        backward = await _every_page(
            client, session, {"kind": "cell.ready", "limit": 2, "newest_first": "true"}
        )

    expected = [event.id for event in oldest_first]
    assert len(expected) == 5
    assert forward == expected
    assert backward == expected[::-1]


async def test_the_filters_narrow_by_family_kind_and_subject() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        await rig.deps.trail.record(trail_event(rig, "cell.ready", _CELL))
        await rig.deps.trail.record(trail_event(rig, "cell.leased", _CELL))
        await rig.deps.trail.record(
            trail_event(rig, "cell.ready", "cell_0000000000000000000000000B")
        )

        family = await client.call(session, "GET", "/v1/trail?family=cell")
        kind = await client.call(session, "GET", "/v1/trail?kind=cell.leased")
        subject = await client.call(session, "GET", f"/v1/trail?family=cell&subject_id={_CELL}")

    assert [event["kind"] for event in family.json()["events"]] == [
        "cell.ready",
        "cell.leased",
        "cell.ready",
    ]
    assert [event["kind"] for event in kind.json()["events"]] == ["cell.leased"]
    assert {event["subject_id"] for event in subject.json()["events"]} == {_CELL}
    assert len(subject.json()["events"]) == 2


async def test_a_tasks_title_is_left_out_of_its_submitted_event() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        [task] = await rig.deps.chamber.submit(make_graph_draft({"garden": ()}))

        read = await client.call(session, "GET", "/v1/trail?kind=task.submitted")

    [event] = read.json()["events"]
    assert event["subject_id"] == task.id
    assert "title" not in event["payload"]
    assert task.spec.title not in read.text


async def test_an_unknown_parameter_or_an_oversized_page_is_refused() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)

        unknown = await client.call(session, "GET", "/v1/trail?text=garden")
        oversized = await client.call(session, "GET", "/v1/trail?limit=501")

    assert (unknown.status_code, oversized.status_code) == (422, 422)
