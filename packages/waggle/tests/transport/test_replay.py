"""Tests for waggle.transport.replay: the sliding window of envelope ids a listener has seen.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises ReplayWindow with a FakeClock and no
    I/O: a first sighting and a duplicate, node separation, expiry exactly at the window, the
    sliding refresh, eviction by the cap, the limits' validation, and two hypothesis properties
    that compare the window with a plain-dict model and bound its size.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.replay for the module under test.
    - test_websocket_server_limits.py for the window behind a real listener.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from waggle.clock import FakeClock
from waggle.transport.replay import (
    MAX_REMEMBERED_IDS,
    REPLAY_WINDOW_S,
    ReplayLimits,
    ReplayWindow,
)

NODE_A = "node_a"
NODE_B = "node_b"
WINDOW_S = 60.0  # A short window keeps the arithmetic in the tests readable.
ALMOST_A_WINDOW_S = WINDOW_S - 0.001  # Strictly inside the window.


def _window(max_ids: int = MAX_REMEMBERED_IDS) -> ReplayWindow:
    """A window of ``WINDOW_S`` seconds holding at most ``max_ids`` pairs."""
    return ReplayWindow(ReplayLimits(window_s=WINDOW_S, max_ids=max_ids))


def test_defaults_are_ten_minutes_and_fifty_thousand_ids() -> None:
    assert REPLAY_WINDOW_S == 600.0
    assert MAX_REMEMBERED_IDS == 50_000
    assert ReplayLimits() == ReplayLimits(window_s=600.0, max_ids=50_000)


def test_a_first_sighting_is_new_and_the_second_is_a_duplicate(fake_clock: FakeClock) -> None:
    window = _window()

    assert window.seen(NODE_A, "msg_1", fake_clock.now()) is False
    assert window.seen(NODE_A, "msg_1", fake_clock.now()) is True
    assert window.seen(NODE_A, "msg_2", fake_clock.now()) is False


def test_the_same_id_from_two_nodes_is_two_different_pairs(fake_clock: FakeClock) -> None:
    window = _window()

    assert window.seen(NODE_A, "msg_1", fake_clock.now()) is False
    # Ids are unique per node, not globally, so node B's msg_1 is not a replay of node A's.
    assert window.seen(NODE_B, "msg_1", fake_clock.now()) is False
    assert window.seen(NODE_A, "msg_1", fake_clock.now()) is True
    assert window.seen(NODE_B, "msg_1", fake_clock.now()) is True


def test_a_pair_is_forgotten_exactly_one_window_after_it_was_seen(fake_clock: FakeClock) -> None:
    window = _window()
    first = fake_clock.now()
    window.seen(NODE_A, "msg_1", first)

    # One tick short of the window it is still remembered; at the window it is forgotten.
    assert window.seen(NODE_A, "msg_1", first + timedelta(seconds=ALMOST_A_WINDOW_S)) is True
    window = _window()
    window.seen(NODE_A, "msg_1", first)
    assert window.seen(NODE_A, "msg_1", first + timedelta(seconds=WINDOW_S)) is False


def test_asking_again_inside_the_window_slides_it(fake_clock: FakeClock) -> None:
    window = _window()
    start = fake_clock.now()
    window.seen(NODE_A, "msg_1", start)

    # A peer that keeps replaying one frame stays refused: each sighting restarts the window.
    for step in range(1, 5):
        at = start + timedelta(seconds=ALMOST_A_WINDOW_S * step)
        assert window.seen(NODE_A, "msg_1", at) is True
    # Left alone for a full window it finally goes, and the next sighting is new again.
    later = start + timedelta(seconds=ALMOST_A_WINDOW_S * 4 + WINDOW_S)
    assert window.seen(NODE_A, "msg_1", later) is False


def test_expiry_leaves_younger_pairs_in_place(fake_clock: FakeClock) -> None:
    window = _window()
    start = fake_clock.now()
    window.seen(NODE_A, "old", start)
    window.seen(NODE_A, "young", start + timedelta(seconds=WINDOW_S / 2))

    after_old_expires = start + timedelta(seconds=WINDOW_S + 1)

    assert window.seen(NODE_A, "old", after_old_expires) is False
    assert window.seen(NODE_A, "young", after_old_expires) is True


def test_the_oldest_pairs_are_evicted_first_when_the_cap_is_reached(
    fake_clock: FakeClock,
) -> None:
    window = _window(max_ids=3)
    now = fake_clock.now()
    for message_id in ("a", "b", "c"):
        window.seen(NODE_A, message_id, now)
    assert len(window) == 3

    # A fourth pair pushes the oldest ("a") out; the window never holds more than the cap.
    assert window.seen(NODE_A, "d", now) is False
    assert len(window) == 3
    assert window.seen(NODE_A, "b", now) is True
    assert window.seen(NODE_A, "c", now) is True
    assert window.seen(NODE_A, "d", now) is True
    # "a" was forgotten, so it counts as new (and in turn displaces the oldest, "b").
    assert window.seen(NODE_A, "a", now) is False
    assert window.seen(NODE_A, "b", now) is False


def test_a_refreshed_pair_survives_eviction_over_an_untouched_one(fake_clock: FakeClock) -> None:
    window = _window(max_ids=2)
    now = fake_clock.now()
    window.seen(NODE_A, "keep", now)
    window.seen(NODE_A, "drop", now)
    window.seen(NODE_A, "keep", now)  # a duplicate: "keep" becomes the youngest pair

    window.seen(NODE_A, "new", now)  # over the cap: the oldest, "drop", goes

    assert window.seen(NODE_A, "keep", now) is True
    assert window.seen(NODE_A, "drop", now) is False


def test_len_shrinks_as_pairs_expire(fake_clock: FakeClock) -> None:
    window = _window()
    start = fake_clock.now()
    for index in range(5):
        window.seen(NODE_A, f"msg_{index}", start)
    assert len(window) == 5

    window.seen(NODE_B, "later", start + timedelta(seconds=WINDOW_S))

    assert len(window) == 1  # the five expired; only the new pair remains


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: ReplayLimits(window_s=0.0), "window_s"),
        (lambda: ReplayLimits(window_s=-1.0), "window_s"),
        (lambda: ReplayLimits(max_ids=0), "max_ids"),
        (lambda: ReplayLimits(max_ids=-5), "max_ids"),
    ],
)
def test_limits_that_could_remember_nothing_are_refused(
    make: Callable[[], ReplayLimits], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        make()


# ──────────────────────────────────────────────────────────────────────────────
# Properties
# ──────────────────────────────────────────────────────────────────────────────

_BASE = datetime(2030, 1, 1, tzinfo=UTC)
# Each step is (node index, id index, seconds since the previous step); time never goes back.
_steps = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=2),
        st.integers(min_value=0, max_value=5),
        st.floats(min_value=0.0, max_value=WINDOW_S * 1.5),
    ),
    max_size=80,
)


@settings(max_examples=150, deadline=None)
@given(steps=_steps)
def test_the_window_agrees_with_a_plain_dict_model_when_the_cap_never_binds(
    steps: list[tuple[int, int, float]],
) -> None:
    window = _window()  # the cap is far above the 18 distinct pairs the strategy can make
    last_seen: dict[tuple[int, int], datetime] = {}
    now = _BASE

    for node, message, gap_s in steps:
        now += timedelta(seconds=gap_s)
        previous = last_seen.get((node, message))
        # The model: a pair is a duplicate iff its last sighting is strictly inside the window.
        expected = previous is not None and now - previous < timedelta(seconds=WINDOW_S)
        last_seen[(node, message)] = now

        assert window.seen(f"node_{node}", f"msg_{message}", now) is expected


@settings(max_examples=100, deadline=None)
@given(steps=_steps, cap=st.integers(min_value=1, max_value=6))
def test_the_window_never_holds_more_than_its_cap(
    steps: list[tuple[int, int, float]], cap: int
) -> None:
    window = _window(max_ids=cap)
    now = _BASE

    for node, message, gap_s in steps:
        now += timedelta(seconds=gap_s)
        window.seen(f"node_{node}", f"msg_{message}", now)

        assert len(window) <= cap
