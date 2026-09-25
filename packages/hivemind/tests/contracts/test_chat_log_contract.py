"""Contract suite for ChatLog: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.queen.chat.protocol.ChatLog contract and runs against both implementations that
    ship: InMemoryChatLog (over MemoryPheromoneTrail) and SqliteChatLog (over SqlitePheromoneTrail,
    both on the same tmp_path SQLite file). A new implementation joins the fixture's params and
    must pass here before it is used anywhere else (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.chat.protocol for the ChatLog protocol under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import connect
from hivemind.pheromone import (
    MemoryPheromoneTrail,
    PheromoneTrail,
    QueenEvent,
    SqlitePheromoneTrail,
    TrailQuery,
)
from hivemind.queen.chat import (
    ChatAuthor,
    ChatEntry,
    ChatEntryExistsError,
    ChatKind,
    ChatLog,
    ChatQuery,
    InMemoryChatLog,
    SqliteChatLog,
    new_chat_entry_id,
)
from waggle.clock import FakeClock
from waggle.ids import new_device_id, new_event_id, new_hive_id, new_node_id

_LOG_KINDS = ("memory", "sqlite")


@dataclass(frozen=True, slots=True)
class _Fixture:
    """A ChatLog, the trail it records on, and the clock both share."""

    log: ChatLog
    trail: PheromoneTrail
    clock: FakeClock


@pytest.fixture(params=_LOG_KINDS)
async def fx(request: pytest.FixtureRequest, tmp_path: Path) -> _Fixture:
    """A log of the parametrised kind, over a trail and a clock of its own."""
    clock = FakeClock()
    if request.param == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return _Fixture(InMemoryChatLog(memory_trail), memory_trail, clock)
    db_path = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(db_path), clock)
    return _Fixture(await SqliteChatLog.create(connect(db_path), clock), sqlite_trail, clock)


def _human(clock: FakeClock, text: str = "Hello, Monarch.") -> ChatEntry:
    """A fresh human message line."""
    return ChatEntry(
        id=new_chat_entry_id(clock),
        at=clock.now(),
        author=ChatAuthor.HUMAN,
        kind=ChatKind.MESSAGE,
        text=text,
        device_id=new_device_id(clock),
    )


def _queen(clock: FakeClock, kind: ChatKind = ChatKind.REPLY) -> ChatEntry:
    """A fresh Queen line of `kind`."""
    return ChatEntry(
        id=new_chat_entry_id(clock), at=clock.now(), author=ChatAuthor.QUEEN, kind=kind, text="Hi."
    )


def _event(clock: FakeClock, entry: ChatEntry) -> QueenEvent:
    """The event recording that `entry` arrived."""
    return QueenEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="queen.human_message_received",
        subject_id=new_hive_id(clock),
        payload={"chat_entry_id": entry.id},
    )


async def _append_many(fx: _Fixture, count: int) -> list[ChatEntry]:
    """Append `count` Queen lines one clock second apart; return them as stored."""
    stored = []
    for _ in range(count):
        fx.clock.advance(1.0)
        stored.append(await fx.log.append(_queen(fx.clock)))
    return stored


async def test_append_assigns_consecutive_positions_from_one(fx: _Fixture) -> None:
    first = await fx.log.append(_human(fx.clock))
    second = await fx.log.append(_queen(fx.clock))

    assert (first.seq, second.seq) == (1, 2)


async def test_append_with_an_event_records_it_and_without_one_records_nothing(
    fx: _Fixture,
) -> None:
    line = _human(fx.clock)

    await fx.log.append(line, _event(fx.clock, line))
    await fx.log.append(_queen(fx.clock))

    assert [event.kind for event in await fx.trail.query(TrailQuery())] == [
        "queen.human_message_received"
    ]


async def test_append_refuses_an_event_naming_another_line(fx: _Fixture) -> None:
    with pytest.raises(InvariantViolationError):
        await fx.log.append(_human(fx.clock), _event(fx.clock, _human(fx.clock)))

    assert await fx.log.read(ChatQuery()) == ()


async def test_append_refuses_a_line_already_appended(fx: _Fixture) -> None:
    stored = await fx.log.append(_queen(fx.clock))

    with pytest.raises(InvariantViolationError):
        await fx.log.append(stored)


async def test_append_refuses_a_taken_id(fx: _Fixture) -> None:
    line = _queen(fx.clock)
    await fx.log.append(line)

    with pytest.raises(ChatEntryExistsError):
        await fx.log.append(line)


async def test_read_opens_on_the_newest_lines_oldest_first(fx: _Fixture) -> None:
    stored = await _append_many(fx, 5)

    page = await fx.log.read(ChatQuery(limit=3))

    assert [line.seq for line in page] == [3, 4, 5]
    assert page == tuple(stored[2:])


async def test_read_after_a_cursor_pages_forward_oldest_first(fx: _Fixture) -> None:
    await _append_many(fx, 5)

    page = await fx.log.read(ChatQuery(after_seq=1, limit=2))

    assert [line.seq for line in page] == [2, 3]


async def test_read_before_a_cursor_scrolls_back(fx: _Fixture) -> None:
    await _append_many(fx, 5)

    page = await fx.log.read(ChatQuery(before_seq=4, limit=2))

    assert [line.seq for line in page] == [2, 3]


async def test_read_since_a_time_leaves_out_older_lines(fx: _Fixture) -> None:
    stored = await _append_many(fx, 4)

    page = await fx.log.read(ChatQuery(since=stored[2].at))

    assert [line.seq for line in page] == [3, 4]


async def test_unhandled_returns_only_waiting_human_messages_oldest_first(fx: _Fixture) -> None:
    first = await fx.log.append(_human(fx.clock, "first"))
    await fx.log.append(_queen(fx.clock, ChatKind.NOTICE))
    second = await fx.log.append(_human(fx.clock, "second"))

    waiting = await fx.log.unhandled(10)

    assert [line.id for line in waiting] == [first.id, second.id]
    assert [line.id for line in await fx.log.unhandled(1)] == [first.id]


async def test_mark_handled_stamps_a_message_once_and_is_idempotent(fx: _Fixture) -> None:
    line = await fx.log.append(_human(fx.clock))
    stamped_at = fx.clock.now()

    await fx.log.mark_handled(line.id, stamped_at)
    fx.clock.advance(5.0)
    await fx.log.mark_handled(line.id, fx.clock.now())  # A second stamp changes nothing.
    await fx.log.mark_handled(new_chat_entry_id(fx.clock), fx.clock.now())  # Unknown: no-op.

    assert await fx.log.unhandled(10) == ()
    [stored] = await fx.log.read(ChatQuery())
    assert stored.handled_at == stamped_at


async def test_mark_handled_never_stamps_a_queen_line(fx: _Fixture) -> None:
    line = await fx.log.append(_queen(fx.clock))

    await fx.log.mark_handled(line.id, fx.clock.now())

    [stored] = await fx.log.read(ChatQuery())
    assert stored.handled_at is None
