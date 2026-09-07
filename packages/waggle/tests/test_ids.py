"""Property and unit tests for waggle.ids: prefixed, sortable, typed ids.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises every new_*_id wrapper, new_id,
    parse_id and timestamp_of against a FakeClock so results are deterministic.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.ids for the module under test.
    - waggle.clock for FakeClock, used throughout to control id timestamps.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from waggle.clock import Clock, FakeClock
from waggle.errors import InvalidIdError
from waggle.ids import (
    IdKind,
    new_alarm_id,
    new_cell_id,
    new_device_id,
    new_event_id,
    new_grant_id,
    new_hive_id,
    new_id,
    new_lease_id,
    new_node_id,
    new_task_id,
    new_tool_id,
    new_warden_id,
    new_worker_id,
    parse_id,
    timestamp_of,
)
from waggle.ulid import ULID_LENGTH

# One (wrapper, expected IdKind) pair per new_*_id wrapper, so the tests below run once per kind
# via parametrize instead of being copy-pasted twelve times. Every wrapper returns a NewType over
# str, which is a subtype of Callable[[Clock], str] by return-type covariance.
_WRAPPERS: list[tuple[Callable[[Clock], str], IdKind]] = [
    (new_hive_id, IdKind.HIVE),
    (new_cell_id, IdKind.CELL),
    (new_lease_id, IdKind.LEASE),
    (new_task_id, IdKind.TASK),
    (new_worker_id, IdKind.WORKER),
    (new_warden_id, IdKind.WARDEN),
    (new_alarm_id, IdKind.ALARM),
    (new_grant_id, IdKind.GRANT),
    (new_tool_id, IdKind.TOOL),
    (new_node_id, IdKind.NODE),
    (new_event_id, IdKind.EVENT),
    (new_device_id, IdKind.DEVICE),
]


@pytest.mark.parametrize(("wrapper", "kind"), _WRAPPERS)
def test_new_id_wrapper_has_expected_prefix_and_length(
    wrapper: Callable[[Clock], str], kind: IdKind
) -> None:
    clock = FakeClock()

    generated = wrapper(clock)

    prefix = f"{kind.value}_"
    assert generated.startswith(prefix)
    assert len(generated) == len(prefix) + ULID_LENGTH


@pytest.mark.parametrize(("wrapper", "kind"), _WRAPPERS)
def test_new_id_wrapper_round_trips_through_parse_id(
    wrapper: Callable[[Clock], str], kind: IdKind
) -> None:
    clock = FakeClock()

    generated = wrapper(clock)

    assert parse_id(generated, kind) == generated


def test_new_id_generic_matches_the_requested_kinds_prefix() -> None:
    clock = FakeClock()

    generated = new_id(IdKind.CELL, clock)

    assert generated.startswith("cell_")


def test_parse_id_rejects_wrong_prefix() -> None:
    clock = FakeClock()
    cell_id = new_cell_id(clock)

    with pytest.raises(InvalidIdError, match="prefix"):
        parse_id(cell_id, IdKind.TASK)


def test_parse_id_rejects_wrong_length() -> None:
    with pytest.raises(InvalidIdError, match="ulid part"):
        parse_id("cell_TOOSHORT", IdKind.CELL)


def test_parse_id_rejects_bad_alphabet_character() -> None:
    # "I" is excluded from the ULID's Crockford alphabet; see waggle.ulid.
    bad = "cell_" + "I" * ULID_LENGTH

    with pytest.raises(InvalidIdError, match="not a valid ulid"):
        parse_id(bad, IdKind.CELL)


def test_timestamp_of_recovers_the_generating_clocks_time() -> None:
    clock = FakeClock()
    before_ms = int(clock.now().timestamp() * 1000)

    task_id = new_task_id(clock)

    # ULIDs are millisecond-resolution, so comparing at that resolution avoids a flaky mismatch
    # against the clock's own microsecond-precision `now()`.
    after_ms = int(timestamp_of(task_id).timestamp() * 1000)
    assert after_ms == before_ms


def test_timestamp_of_rejects_a_string_with_no_ulid_part() -> None:
    with pytest.raises(InvalidIdError, match="ulid part"):
        timestamp_of("not-an-id")


def test_timestamp_of_rejects_a_correctly_sized_but_invalid_ulid() -> None:
    # Right length, wrong alphabet: "I" is excluded from Crockford base32 (see waggle.ulid).
    bad = "cell_" + "I" * ULID_LENGTH

    with pytest.raises(InvalidIdError, match="not a valid ulid"):
        timestamp_of(bad)


@given(delays_ms=st.lists(st.integers(min_value=1, max_value=1000), min_size=2, max_size=6))
def test_ids_of_the_same_kind_sort_by_creation_time(delays_ms: list[int]) -> None:
    clock = FakeClock()
    ids = [new_cell_id(clock)]

    # Advance by at least one millisecond each time so every id gets a strictly later timestamp,
    # which is what the sort-by-creation-time property below actually claims.
    for delay_ms in delays_ms:
        clock.advance(delay_ms / 1000)
        ids.append(new_cell_id(clock))

    assert ids == sorted(ids)
