"""Test hivemind.entrance.reads.screens.thoughts: a page of thoughts read from memory and the trail.

Episode records come back newest first for one bee or every bee, a cursor continues after the last,
and the Queen's autopilot decisions come from the trail's decision events.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/screens/thoughts.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.screens import scene
from builders.memory import make_episode
from builders.observation import after, make_event

from hivemind.cell import HoneyClearance
from hivemind.entrance.reads.screens import read_thoughts
from hivemind.memory import MemoryContext, record_episode
from hivemind.observation import ThoughtsQuery
from waggle.clock import FakeClock
from waggle.ids import new_task_id


async def test_pages_run_newest_first_per_bee_and_a_cursor_continues() -> None:
    clock = FakeClock()
    hive = scene(clock)
    context = MemoryContext(hive.deps.memory, hive.deps.identity, clock)
    records = [
        make_episode(clock, principal=bee, clearance=HoneyClearance.C2, at=after(n))
        for n, bee in enumerate(["queen", "warden_a", "queen", "queen"])
    ]
    for record in records:
        await record_episode(record, context)

    first = await read_thoughts(hive.reads, ThoughtsQuery(principal="queen", limit=2))
    second = await read_thoughts(
        hive.reads, ThoughtsQuery(principal="queen", limit=2, cursor=first.next_cursor)
    )

    assert [e.id for e in first.episodes] == [records[3].id, records[2].id]
    assert [e.id for e in second.episodes] == [records[0].id]
    assert second.next_cursor is None


async def test_the_queens_decisions_come_from_the_trail() -> None:
    clock = FakeClock()
    hive = scene(clock)
    await hive.record(
        make_event("queen.decided", new_task_id(clock), after(1), payload={"action": "CLUSTER"})
    )

    page = await read_thoughts(hive.reads, ThoughtsQuery())

    assert [(d.kind, d.action) for d in page.decisions] == [("queen.decided", "CLUSTER")]
    assert page.episodes == []
