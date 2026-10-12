"""Test hivemind.observation.api.thoughts: cursor pages of episode records, and the Queen's rules.

Records page newest first and a cursor continues strictly after the last record shown, even when a
newer one has arrived; the last page has no cursor; a malformed cursor is refused when the query is
validated; and the Queen's autopilot decisions (the action and reason recorded) appear for her and
for an unfiltered read, not for another bee.

Fits into the Hive:
    Mirrors src/hivemind/observation/api/thoughts.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.memory import make_episode
from builders.observation import after, make_event
from pydantic import ValidationError

from hivemind.memory import EpisodeRecord
from hivemind.observation import (
    MAX_DECISIONS,
    ThoughtsQuery,
    decode_cursor,
    encode_cursor,
    thoughts_page,
)
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_task_id

_CLOCK = FakeClock()


def _records(count: int) -> list[EpisodeRecord]:
    """``count`` episode records, one second apart, oldest first."""
    return [make_episode(_CLOCK, at=after(second)) for second in range(count)]


def test_an_empty_hive_has_an_empty_page_and_no_cursor() -> None:
    page = thoughts_page([], [], ThoughtsQuery())

    assert (page.episodes, page.next_cursor, page.decisions) == ([], None, [])


def test_pages_run_newest_first_and_a_cursor_continues_after_the_last_record() -> None:
    records = _records(5)

    first = thoughts_page(records, [], ThoughtsQuery(limit=2))
    # A newer record arrives between the two requests: the next page must not shift.
    newer = make_episode(_CLOCK, at=after(99))
    second = thoughts_page([*records, newer], [], ThoughtsQuery(limit=2, cursor=first.next_cursor))
    last = thoughts_page(records, [], ThoughtsQuery(limit=2, cursor=second.next_cursor))

    assert [e.id for e in first.episodes] == [records[4].id, records[3].id]
    assert [e.id for e in second.episodes] == [records[2].id, records[1].id]
    assert [e.id for e in last.episodes] == [records[0].id]
    assert last.next_cursor is None


def test_the_cursor_round_trips_and_a_malformed_one_is_refused() -> None:
    [record] = _records(1)

    assert decode_cursor(encode_cursor(record)) == (record.at, record.id)
    for bad in ("nonsense", "1000.", ".evt_x", "-5.evt_x", "soon.evt_x", "9" * 40 + ".evt_x"):
        with pytest.raises(ValidationError):
            ThoughtsQuery(cursor=bad)


def test_the_queens_decisions_carry_the_action_and_reason_of_each_rule() -> None:
    task_id, alarm_id = new_task_id(_CLOCK), new_alarm_id(_CLOCK)
    events = [
        make_event("queen.decided", task_id, after(1), payload={"action": "REBIND"}),
        make_event("queen.decided", task_id, after(2), payload={"reason": "placement_failed"}),
        make_event("alarm.handled", alarm_id, after(3), payload={"action": "RECORD"}),
        make_event("task.started", task_id, after(4)),  # Not a decision.
    ]

    queen = thoughts_page([], events, ThoughtsQuery(principal="queen"))
    everyone = thoughts_page([], events, ThoughtsQuery())
    warden = thoughts_page([], events, ThoughtsQuery(principal="warden_a"))

    assert [(d.kind, d.action, d.reason) for d in queen.decisions] == [
        ("alarm.handled", "RECORD", None),
        ("queen.decided", None, "placement_failed"),
        ("queen.decided", "REBIND", None),
    ]
    assert queen.decisions[0].alarm_id == alarm_id
    assert everyone.decisions == queen.decisions
    assert warden.decisions == []


def test_only_the_newest_decisions_are_listed() -> None:
    task_id = new_task_id(_CLOCK)
    events = [
        make_event("queen.decided", task_id, after(n), payload={"action": "CLUSTER"})
        for n in range(MAX_DECISIONS + 5)
    ]

    page = thoughts_page([], events, ThoughtsQuery())

    assert len(page.decisions) == MAX_DECISIONS
    assert page.decisions[0].at == after(MAX_DECISIONS + 4)
