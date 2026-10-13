"""Test hivemind.entrance.reads.screens.attendant: the Attendant view for the Queen or a Warden.

The Queen's view holds every task, an open Alarm and the human messages still waiting; a Warden's
holds only its own tasks; a principal that is neither the Queen nor an attached Warden is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/screens/attendant.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.entrance.screens import scene
from builders.observation import make_event

from hivemind.entrance.errors import PrincipalNotFoundError
from hivemind.entrance.reads.screens import read_attendant
from hivemind.queen import ChatAuthor, ChatEntry, ChatKind
from hivemind.supervision.attendant import InboxKind
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_device_id, new_warden_id


async def test_the_queens_view_holds_every_task_her_alarms_and_waiting_messages() -> None:
    clock = FakeClock()
    hive = scene(clock)
    first, second = await hive.submit({"a": (), "b": ("a",)})
    await hive.place(first)
    alarm_id = new_alarm_id(clock)
    payload = {"kind": "WORKER_FAILED", "severity": "CRITICAL", "task_id": first.id}
    await hive.record(make_event("alarm.raised", alarm_id, clock.now(), payload=payload))
    entry = ChatEntry(
        id="chat_01DXF6DT00XEFQX8VABTA6QFSM",
        at=clock.now(),
        author=ChatAuthor.HUMAN,
        kind=ChatKind.MESSAGE,
        text="Hello.",
        device_id=new_device_id(clock),
    )
    await hive.deps.chat.append(entry)

    view = await read_attendant(hive.reads, "queen", clock.now())

    assert {node.task_id for node in view.nodes} == {first.id, second.id}
    assert [(edge.before, edge.after) for edge in view.edges] == [(first.id, second.id)]
    assert {item.kind for item in view.inbox} == {InboxKind.ALARM, InboxKind.HUMAN_MESSAGE}
    assert view.inbox_is_reconstructed


async def test_a_wardens_view_holds_only_its_own_tasks_and_no_human_messages() -> None:
    clock = FakeClock()
    hive = scene(clock)
    mine, other = await hive.submit({"a": (), "b": ()})
    await hive.place(mine)

    view = await read_attendant(hive.reads, hive.link.warden_id, clock.now())

    assert [node.task_id for node in view.nodes] == [mine.id]
    assert other.id not in view.up_next


async def test_a_principal_that_is_neither_the_queen_nor_attached_is_refused() -> None:
    hive = scene(FakeClock())

    with pytest.raises(PrincipalNotFoundError):
        await read_attendant(hive.reads, new_warden_id(FakeClock()), hive.clock.now())
