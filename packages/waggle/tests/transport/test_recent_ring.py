"""Tests for waggle.transport.recent.ring: the bounded memory of what a node sent lately.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises RecentFrames with plain floats for the
    node's monotonic clock and no I/O: which frames a reconnection is due, in what order, the three
    bounds (age, count, bytes), a re-send keeping its place, the receiver's replay horizon, the
    limits' validation, and two hypothesis properties that hold the bounds and the order whatever
    the node sends.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.recent.ring for the module under test.
    - test_recent_remembering.py for the Transport wrapper that fills the ring.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import new_device_id, new_hive_id, new_node_id
from waggle.messages.control import HumanMessage, Ping
from waggle.transport.recent import (
    MAX_RECENT_BYTES,
    MAX_RECENT_FRAMES,
    RESEND_HORIZON_S,
    UNNOTICED_S,
    RecentFrames,
    RecentLimits,
)
from waggle.transport.recent import ring as ring_module
from waggle.transport.replay import REPLAY_WINDOW_S
from waggle.transport.websocket import PING_INTERVAL_S, PING_TIMEOUT_S

WINDOW_S = 80.0  # The default window, spelled out so the arithmetic below reads at a glance.
Make = Callable[..., Envelope]


def _ids(frames: tuple[Envelope, ...]) -> list[str]:
    """The envelope ids of `frames`, in order."""
    return [str(frame.id) for frame in frames]


def _size_of(frame: Envelope) -> int:
    """What the ring counts a frame as: its unsigned wire text."""
    return len(Codec().encode(frame))


def _pool(count: int) -> list[Envelope]:
    """`count` distinct Pings from one node, built without a fixture (hypothesis resets none)."""
    clock = FakeClock()
    hop = Hop(sender=new_hive_id(clock), recipient=new_hive_id(clock), node_id=new_node_id(clock))
    return [wrap(Ping(), hop, clock=clock) for _ in range(count)]


def _long_message(make_envelope: Make) -> Envelope:
    """An envelope of a few kilobytes: a human's words at their longest."""
    words = HumanMessage(text="x" * 16_000, task_id=None, device_id=new_device_id(FakeClock()))
    return make_envelope(words)


def test_the_defaults_follow_the_keepalive_and_the_receivers_replay_window() -> None:
    # Twice the time the keepalive takes to give up on a link; a minute less than the window the
    # receiver remembers a frame for; and a count and a byte total a small device can promise.
    assert UNNOTICED_S == 2 * (PING_INTERVAL_S + PING_TIMEOUT_S) == WINDOW_S
    assert RESEND_HORIZON_S == REPLAY_WINDOW_S - 60.0
    assert RecentLimits() == RecentLimits(
        window_s=UNNOTICED_S,
        horizon_s=RESEND_HORIZON_S,
        max_frames=MAX_RECENT_FRAMES,
        max_bytes=MAX_RECENT_BYTES,
    )


def test_a_frame_just_before_the_loss_is_due_and_nothing_is_due_from_an_empty_ring(
    make_envelope: Make,
) -> None:
    ring = RecentFrames()
    assert ring.due(lost_at=10.0, now=12.0) == ()

    sent = make_envelope()
    ring.remember(sent, at=9.0)

    assert ring.due(lost_at=10.0, now=12.0) == (sent,)
    assert len(ring) == 1 and ring.size_bytes == _size_of(sent)


def test_frames_are_due_in_the_order_they_were_sent(make_envelope: Make) -> None:
    ring = RecentFrames()
    sent = [make_envelope() for _ in range(4)]
    for moment, frame in enumerate(sent):
        ring.remember(frame, at=float(moment))

    assert _ids(ring.due(lost_at=5.0, now=6.0)) == _ids(tuple(sent))


def test_a_frame_sent_again_replaces_its_entry_and_takes_the_young_end(
    make_envelope: Make,
) -> None:
    ring = RecentFrames()
    first, second, third = make_envelope(), make_envelope(), make_envelope()
    for moment, frame in enumerate((first, second, third)):
        ring.remember(frame, at=float(moment))

    # Sending `first` again (a re-send) is the same frame, so the ring keeps one of it, last.
    ring.remember(first, at=10.0)

    assert len(ring) == 3 and ring.size_bytes == sum(map(_size_of, (first, second, third)))
    assert _ids(ring.due(lost_at=11.0, now=12.0)) == _ids((second, third, first))


def test_a_whole_resend_in_order_leaves_the_order_it_found(make_envelope: Make) -> None:
    ring = RecentFrames()
    sent = tuple(make_envelope() for _ in range(3))
    for moment, frame in enumerate(sent):
        ring.remember(frame, at=float(moment))

    for frame in ring.due(lost_at=3.0, now=20.0):  # What a reconnection does, frame by frame.
        ring.remember(frame, at=20.0)

    assert _ids(ring.due(lost_at=21.0, now=22.0)) == _ids(sent)


def test_a_frame_sent_longer_before_the_loss_than_the_window_is_not_due(
    make_envelope: Make,
) -> None:
    ring = RecentFrames()
    early, late = make_envelope(), make_envelope()
    ring.remember(early, at=0.0)
    ring.remember(late, at=50.0)

    # The link was found dead at 100 s: `early` was sent 100 s before, while the link was
    # certainly alive (the keepalive finds a dead one within 40 s); `late` 50 s before, inside 80.
    assert ring.due(lost_at=100.0, now=101.0) == (late,)
    # Exactly the window before the loss is still inside it.
    assert ring.due(lost_at=80.0, now=81.0) == (early, late)


def test_a_frame_is_dropped_once_a_newer_one_is_a_window_younger(make_envelope: Make) -> None:
    ring = RecentFrames()
    old, new = make_envelope(), make_envelope()
    ring.remember(old, at=0.0)

    ring.remember(new, at=WINDOW_S + 1.0)

    # Nothing ever asks for `old` again: a loss can be found no earlier than the newest frame.
    assert len(ring) == 1 and ring.due(lost_at=WINDOW_S + 2.0, now=WINDOW_S + 3.0) == (new,)


def test_a_frame_older_than_the_receivers_memory_is_held_but_not_due(make_envelope: Make) -> None:
    ring = RecentFrames(RecentLimits(horizon_s=100.0))
    sent = make_envelope()
    ring.remember(sent, at=0.0)

    # The receiver forgets a frame it handled a window after its last sighting, and a frame sent
    # again then would be handled twice: past the horizon the ring offers nothing.
    assert ring.due(lost_at=10.0, now=99.0) == (sent,)
    assert ring.due(lost_at=10.0, now=100.0) == ()
    assert len(ring) == 1  # Still held: only ageing against a newer frame removes it.


def test_the_oldest_frames_go_first_when_there_are_too_many(make_envelope: Make) -> None:
    ring = RecentFrames(RecentLimits(max_frames=3))
    sent = [make_envelope() for _ in range(5)]

    for moment, frame in enumerate(sent):
        ring.remember(frame, at=float(moment))

    assert len(ring) == 3
    assert _ids(ring.due(lost_at=5.0, now=6.0)) == _ids(tuple(sent[2:]))


def test_the_oldest_frames_go_first_when_they_weigh_too_much(make_envelope: Make) -> None:
    probe = make_envelope()
    room_for_two = _size_of(probe) * 2 + 1  # Every Ping weighs about the same.
    ring = RecentFrames(RecentLimits(max_bytes=room_for_two))
    sent = [make_envelope() for _ in range(4)]

    for moment, frame in enumerate(sent):
        ring.remember(frame, at=float(moment))

    assert len(ring) == 2 and ring.size_bytes <= room_for_two
    assert _ids(ring.due(lost_at=4.0, now=5.0)) == _ids(tuple(sent[2:]))


def test_a_frame_too_big_for_the_ring_is_not_kept_and_costs_the_others_nothing(
    make_envelope: Make,
) -> None:
    kept = make_envelope()
    ring = RecentFrames(RecentLimits(max_bytes=_size_of(kept)))
    ring.remember(kept, at=0.0)

    ring.remember(_long_message(make_envelope), at=1.0)

    assert ring.due(lost_at=2.0, now=3.0) == (kept,) and ring.size_bytes == _size_of(kept)


def test_a_frame_the_wire_would_refuse_is_not_kept(
    make_envelope: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    ring = RecentFrames()
    # A wire that takes nothing: a send of this frame raised, so there is nothing to send again.
    monkeypatch.setattr(ring_module, "_SIZER", Codec(max_frame_bytes=1))

    ring.remember(make_envelope(), at=0.0)

    assert len(ring) == 0 and ring.size_bytes == 0


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: RecentLimits(window_s=0.0), "window_s"),
        (lambda: RecentLimits(horizon_s=-1.0), "horizon_s"),
        (lambda: RecentLimits(max_frames=0), "max_frames"),
        (lambda: RecentLimits(max_bytes=0), "max_bytes"),
    ],
)
def test_limits_that_could_remember_nothing_are_refused(
    make: Callable[[], RecentLimits], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        make()


# ──────────────────────────────────────────────────────────────────────────────
# Properties
# ──────────────────────────────────────────────────────────────────────────────

# Each step is (an id out of eight, seconds since the previous step): time never goes back, and
# an id can come round again, which is how a re-send looks to the ring.
_steps = st.lists(
    st.tuples(st.integers(min_value=0, max_value=7), st.floats(min_value=0.0, max_value=60.0)),
    max_size=80,
)


@settings(max_examples=120, deadline=None)
@given(steps=_steps, max_frames=st.integers(min_value=1, max_value=5))
def test_the_ring_never_holds_more_than_its_bounds_whatever_is_sent(
    steps: list[tuple[int, float]], max_frames: int
) -> None:
    pool = _pool(8)
    room = _size_of(pool[0]) * 3 + 1
    ring = RecentFrames(RecentLimits(max_frames=max_frames, max_bytes=room))
    now = 0.0

    for index, gap_s in steps:
        now += gap_s
        ring.remember(pool[index], at=now)

        assert len(ring) <= max_frames and ring.size_bytes <= room
        assert ring.size_bytes == sum(_size_of(frame) for frame in ring.due(now, now))


@settings(max_examples=120, deadline=None)
@given(steps=_steps)
def test_the_frames_due_are_always_in_the_order_they_were_last_sent(
    steps: list[tuple[int, float]],
) -> None:
    pool = _pool(8)
    ring = RecentFrames()
    last_sent: dict[int, float] = {}
    now = 0.0

    for index, gap_s in steps:
        now += gap_s
        ring.remember(pool[index], at=now)
        last_sent[index] = now

    # The model: a frame is held if it was last sent inside the window of the newest, and the
    # frames come back ordered by when they were last sent (ties by who was sent last).
    due = _ids(ring.due(lost_at=now, now=now))
    held = [i for i, moment in last_sent.items() if moment >= now - WINDOW_S]
    expected = sorted(held, key=lambda i: (last_sent[i], _last_step(steps, i)))
    assert due == [str(pool[i].id) for i in expected]


def _last_step(steps: list[tuple[int, float]], index: int) -> int:
    """The position of the last step that sent `index`, to order frames sent at the same instant."""
    return max(position for position, (sent, _) in enumerate(steps) if sent == index)
