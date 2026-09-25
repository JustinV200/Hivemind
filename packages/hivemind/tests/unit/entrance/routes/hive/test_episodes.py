"""Test hivemind.entrance.routes.hive.episodes: the bees' episode records, behind thoughts and C2.

Over real listeners and the Queen's own memory: records written the way every bee writes them are
read newest first, one bee's alone with a principal filter and no more than the limit; a device
without ``observe:thoughts``, or with it but without ``honey:clearance:c2``, is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/hive/episodes.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta

from builders.entrance.serving import ProgramGrant, ServingRig, serving
from builders.memory import make_episode

from hivemind.cell import HoneyClearance
from hivemind.memory import EpisodeRecord, MemoryContext, record_episode

_THOUGHTS = ProgramGrant(capabilities=("observe", "observe:thoughts", "honey:clearance:c2"))


async def _think(rig: ServingRig, principal: str, step: int) -> EpisodeRecord:
    """Record one C2 episode for ``principal`` exactly as a bee does, and return it."""
    # `step` ms past now: the rig's clock is real, and a coarse one (Windows') can read one
    # instant twice, while newest-first needs strictly newer records to have one right answer.
    at = rig.clock.now() + timedelta(milliseconds=step)
    record = make_episode(rig.clock, principal=principal, clearance=HoneyClearance.C2, at=at)
    await record_episode(record, MemoryContext(rig.deps.memory, rig.deps.identity, rig.clock))
    return record


async def test_records_are_read_newest_first_by_principal_and_limit() -> None:
    async with serving() as rig:
        client, session = await rig.program(_THOUGHTS)
        first = await _think(rig, "queen", 0)
        garden = await _think(rig, "warden_garden", 1)
        last = await _think(rig, "queen", 2)

        every = await client.call(session, "GET", "/v1/episodes")
        queens = await client.call(session, "GET", "/v1/episodes?principal=queen")
        newest = await client.call(session, "GET", "/v1/episodes?limit=1")

    assert every.status_code == 200, every.text
    assert [episode["id"] for episode in every.json()["episodes"]] == [
        last.id,
        garden.id,
        first.id,
    ]
    assert [episode["id"] for episode in queens.json()["episodes"]] == [last.id, first.id]
    assert [episode["id"] for episode in newest.json()["episodes"]] == [last.id]
    assert every.json()["episodes"][0]["decision"] == last.decision


async def test_the_read_needs_thoughts_and_c2() -> None:
    async with serving() as rig:
        observer, observer_session = await rig.program(ProgramGrant(capabilities=("observe",)))
        uncleared, uncleared_session = await rig.program(
            ProgramGrant(capabilities=("observe", "observe:thoughts"))
        )

        statuses = [
            (await observer.call(observer_session, "GET", "/v1/episodes")).status_code,
            (await uncleared.call(uncleared_session, "GET", "/v1/episodes")).status_code,
        ]

    assert statuses == [403, 403]
