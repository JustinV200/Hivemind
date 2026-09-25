"""Test hivemind.entrance.streams.views.episodes: new episode records, read back from memory.

Over a real listener and the Queen's own memory store: a record written the way every bee writes
one (``record_episode``: the record and its ``memory.episode`` event together) reaches the view
as memory holds it, a principal filter sends only that bee's records, the view is refused to a
device without ``observe:thoughts`` and C2, and a subscriber further behind than its backlog is
closed with FELL_BEHIND.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/views/episodes.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, RigOptions, ServingRig, serving
from builders.entrance.views import burst, close_code, next_frame, open_view, refusal
from builders.memory import make_episode

from hivemind.cell import HoneyClearance
from hivemind.entrance.streams import CloseReason
from hivemind.memory import EpisodeRecord, MemoryContext, record_episode
from waggle.ids import new_event_id

_THOUGHTS = ProgramGrant(capabilities=("observe", "observe:thoughts", "honey:clearance:c2"))
_PATH = "/v1/episodes/stream"


async def _think(rig: ServingRig, principal: str) -> EpisodeRecord:
    """Record one C2 episode for ``principal`` exactly as a bee does, and return it."""
    record = make_episode(
        rig.clock,
        principal=principal,
        clearance=HoneyClearance.C2,
        decision="Ask the human which page to tidy first.",
    )
    await record_episode(record, MemoryContext(rig.deps.memory, rig.deps.identity, rig.clock))
    return record


async def test_a_record_a_bee_writes_is_sent_as_memory_holds_it() -> None:
    async with serving() as rig:
        client, session = await rig.program(_THOUGHTS)
        socket = await open_view(rig, client, session, _PATH)
        try:
            record = await _think(rig, "queen")
            frame = await next_frame(socket)
        finally:
            await socket.close()

    episode = frame["episode"]
    assert frame["type"] == "episode"
    assert (episode["id"], episode["principal"]) == (record.id, "queen")
    assert episode["decision"] == "Ask the human which page to tidy first."
    assert episode["clearance"] == "C2"


async def test_a_principal_filter_sends_only_that_bees_records() -> None:
    async with serving() as rig:
        client, session = await rig.program(_THOUGHTS)
        socket = await open_view(rig, client, session, f"{_PATH}?principal=warden_garden")
        try:
            await _think(rig, "warden_kitchen")
            wanted = await _think(rig, "warden_garden")
            frame = await next_frame(socket)
        finally:
            await socket.close()

    assert frame["episode"]["id"] == wanted.id


async def test_the_view_is_refused_without_thoughts_and_c2() -> None:
    async with serving() as rig:
        observer, observer_session = await rig.program(ProgramGrant(capabilities=("observe",)))
        uncleared, uncleared_session = await rig.program(
            ProgramGrant(capabilities=("observe", "observe:thoughts"))
        )

        codes = [
            await refusal(rig, observer, observer_session, _PATH),
            await refusal(rig, uncleared, uncleared_session, _PATH),
        ]

    assert codes == [CloseReason.FORBIDDEN.code] * 2


async def test_a_subscriber_further_behind_than_its_backlog_is_closed_as_fell_behind() -> None:
    async with serving(RigOptions(stream_backlog=2)) as rig:
        client, session = await rig.program(_THOUGHTS)
        socket = await open_view(rig, client, session, _PATH)

        await burst(rig, "memory.episode", new_event_id(rig.clock), 3)
        code = await close_code(socket)

    assert code == CloseReason.FELL_BEHIND.code
