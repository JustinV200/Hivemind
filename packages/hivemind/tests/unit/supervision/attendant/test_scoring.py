"""Tests for hivemind.supervision.attendant.scoring: score_item, TieBreaker and Attendant.

Fits into the Hive:
    Mirrors src/hivemind/supervision/attendant/scoring.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.attendant.scoring for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from builders.supervision import make_inbox_item

from hivemind.supervision.alarm import AlarmSeverity
from hivemind.supervision.attendant.items import InboxItem, InboxKind
from hivemind.supervision.attendant.scoring import Attendant, score_item
from hivemind.supervision.attendant.weights import WeightTable
from waggle.clock import FakeClock
from waggle.ids import new_task_id


class _RecordingTieBreaker:
    """A TieBreaker that always picks the first item offered, recording every call it saw."""

    def __init__(self) -> None:
        self.calls: list[tuple[InboxItem, ...]] = []

    async def break_tie(self, items: tuple[InboxItem, ...]) -> InboxItem:
        self.calls.append(items)
        return items[0]


def test_score_item_is_pure_given_now() -> None:
    weights = WeightTable.queen_default()
    item = make_inbox_item()
    now = FakeClock().now()

    first = score_item(weights, item, now)
    second = score_item(weights, item, now)

    assert first == second


def test_score_item_reasons_name_every_contributing_factor() -> None:
    weights = WeightTable.queen_default()
    item = make_inbox_item(
        kind=InboxKind.ALARM, severity=AlarmSeverity.CRITICAL, latency_budget_s=2.0
    )
    now = FakeClock().now()

    priority = score_item(weights, item, now)

    joined = " ".join(priority.reasons)
    assert "kind=" in joined
    assert "severity=" in joined
    assert "age=" in joined
    assert "latency_budget=" in joined
    assert "principal=" in joined


def test_score_item_task_linked_item_scores_higher_than_an_otherwise_identical_one() -> None:
    weights = WeightTable.queen_default()
    now = FakeClock().now()
    linked = make_inbox_item(task_id=new_task_id(FakeClock()), received_at=now)
    unlinked = make_inbox_item(task_id=None, received_at=now)

    linked_priority = score_item(weights, linked, now)
    unlinked_priority = score_item(weights, unlinked, now)

    assert linked_priority.score > unlinked_priority.score
    assert any("task_linked" in reason for reason in linked_priority.reasons)


def test_score_item_age_grows_the_score() -> None:
    weights = WeightTable.queen_default()
    clock = FakeClock()
    item = make_inbox_item(received_at=clock.now())

    earlier = score_item(weights, item, clock.now())
    later = score_item(weights, item, clock.now() + timedelta(seconds=1_000))

    assert later.score > earlier.score


async def test_order_ranks_a_critical_alarm_above_a_human_message() -> None:
    weights = WeightTable.queen_default()
    clock = FakeClock()
    attendant = Attendant(weights, clock)
    alarm = make_inbox_item(
        id="alarm-1", kind=InboxKind.ALARM, severity=AlarmSeverity.CRITICAL, clock=clock
    )
    human = make_inbox_item(id="human-1", kind=InboxKind.HUMAN_MESSAGE, clock=clock)

    ordered = await attendant.order((human, alarm))

    assert ordered == (alarm, human)


async def test_order_ranks_a_human_message_above_a_heartbeat() -> None:
    weights = WeightTable.queen_default()
    clock = FakeClock()
    attendant = Attendant(weights, clock)
    human = make_inbox_item(id="human-1", kind=InboxKind.HUMAN_MESSAGE, clock=clock)
    heartbeat = make_inbox_item(
        id="heartbeat-1",
        kind=InboxKind.WAGGLE_MESSAGE,
        payload_kind="supervision.heartbeat",
        clock=clock,
    )

    ordered = await attendant.order((heartbeat, human))

    assert ordered == (human, heartbeat)


async def test_order_breaks_an_exact_tie_by_older_received_at_first() -> None:
    weights = WeightTable.queen_default()
    clock = FakeClock()
    older = make_inbox_item(id="older", kind=InboxKind.TIMER, clock=clock)
    clock.advance(10.0)
    newer = make_inbox_item(id="newer", kind=InboxKind.TIMER, clock=clock)
    attendant = Attendant(weights, clock)

    ordered = await attendant.order((newer, older))

    assert ordered == (older, newer)


async def test_order_breaks_a_same_instant_tie_by_id() -> None:
    weights = WeightTable.queen_default()
    clock = FakeClock()
    item_b = make_inbox_item(id="b", kind=InboxKind.TIMER, clock=clock)
    item_a = make_inbox_item(id="a", kind=InboxKind.TIMER, clock=clock)
    attendant = Attendant(weights, clock)

    ordered = await attendant.order((item_b, item_a))

    assert ordered == (item_a, item_b)


async def test_order_calls_the_tie_breaker_only_on_an_exact_tie() -> None:
    weights = WeightTable.queen_default()
    clock = FakeClock()
    tie_breaker = _RecordingTieBreaker()
    attendant = Attendant(weights, clock, tie_breaker=tie_breaker)
    tied_a = make_inbox_item(id="tied-a", kind=InboxKind.TIMER, clock=clock)
    tied_b = make_inbox_item(id="tied-b", kind=InboxKind.TIMER, clock=clock)
    distinct = make_inbox_item(id="distinct", kind=InboxKind.HUMAN_MESSAGE, clock=clock)

    await attendant.order((tied_a, tied_b, distinct))

    # Exactly one tie among three items: the tied pair triggers one call; the untied `distinct`
    # item is never part of a group of more than one, so it never reaches the tie-breaker.
    assert len(tie_breaker.calls) == 1
    assert set(tie_breaker.calls[0]) == {tied_a, tied_b}


async def test_order_does_not_call_the_tie_breaker_when_no_scores_tie() -> None:
    weights = WeightTable.queen_default()
    clock = FakeClock()
    tie_breaker = _RecordingTieBreaker()
    attendant = Attendant(weights, clock, tie_breaker=tie_breaker)
    alarm = make_inbox_item(id="alarm-1", kind=InboxKind.ALARM, clock=clock)
    human = make_inbox_item(id="human-1", kind=InboxKind.HUMAN_MESSAGE, clock=clock)

    await attendant.order((alarm, human))

    assert tie_breaker.calls == []


async def test_order_honours_the_tie_breakers_choice() -> None:
    weights = WeightTable.queen_default()
    clock = FakeClock()

    class _PickSecond:
        async def break_tie(self, items: tuple[InboxItem, ...]) -> InboxItem:
            return items[1]

    tied_a = make_inbox_item(id="tied-a", kind=InboxKind.TIMER, clock=clock)
    tied_b = make_inbox_item(id="tied-b", kind=InboxKind.TIMER, clock=clock)
    attendant = Attendant(weights, clock, tie_breaker=_PickSecond())

    ordered = await attendant.order((tied_a, tied_b))

    assert ordered[0] == tied_b
