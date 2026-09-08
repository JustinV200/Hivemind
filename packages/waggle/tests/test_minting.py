"""Tests for waggle.minting: the thirteen typed new_<kind>_id wrappers.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises every wrapper against a FakeClock so
    results are deterministic, and pins the one-wrapper-per-IdKind invariant that keeps
    waggle.minting and waggle.ids.IdKind from drifting apart.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.minting for the module under test.
    - waggle.ids for IdKind, parse_id and timestamp_of, which every assertion here goes through.
    - test_ids.py for the generic new_id and the parsers themselves.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from waggle import minting
from waggle.clock import Clock, FakeClock
from waggle.ids import IdKind, parse_id, timestamp_of
from waggle.minting import (
    new_alarm_id,
    new_cell_id,
    new_device_id,
    new_event_id,
    new_grant_id,
    new_hive_id,
    new_lease_id,
    new_message_id,
    new_node_id,
    new_task_id,
    new_tool_id,
    new_warden_id,
    new_worker_id,
)
from waggle.ulid import ULID_LENGTH

# One (wrapper, expected IdKind) pair per new_*_id wrapper, so the tests below run once per kind
# via parametrize instead of being copy-pasted thirteen times. Every wrapper returns a NewType
# over str, which is a subtype of Callable[[Clock], str] by return-type covariance.
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
    (new_message_id, IdKind.MESSAGE),
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


@pytest.mark.parametrize(("wrapper", "kind"), _WRAPPERS)
def test_new_id_wrapper_stamps_the_fake_clocks_time(
    wrapper: Callable[[Clock], str], kind: IdKind
) -> None:
    # Start somewhere other than FakeClock's default so a wrapper that ignored its clock argument
    # (and reached for a fresh one) would be caught rather than pass by coincidence.
    clock = FakeClock()
    clock.advance(12.345)
    expected_ms = int(clock.now().timestamp() * 1000)

    generated = wrapper(clock)

    # ULIDs are millisecond-resolution, so comparing at that resolution avoids a flaky mismatch
    # against the clock's own microsecond-precision `now()`.
    assert int(timestamp_of(generated).timestamp() * 1000) == expected_ms
    assert parse_id(generated, kind) == generated


@pytest.mark.parametrize(("wrapper", "kind"), _WRAPPERS)
def test_new_id_wrapper_is_named_for_its_kind(
    wrapper: Callable[[Clock], str], kind: IdKind
) -> None:
    # The naming rule is new_<IdKind member, lowercased>_id, not new_<prefix>_id: that is why the
    # MESSAGE kind's wrapper is new_message_id even though its prefix is the abbreviation "msg".
    assert wrapper.__name__ == f"new_{kind.name.lower()}_id"


def test_new_message_id_mints_a_msg_prefixed_message_id() -> None:
    clock = FakeClock()

    message_id = new_message_id(clock)

    assert message_id.startswith("msg_")
    assert parse_id(message_id, IdKind.MESSAGE) == message_id


def test_minting_exports_exactly_one_wrapper_per_id_kind() -> None:
    # Adding an IdKind member without its wrapper (or the reverse) must fail here, because the
    # rest of the workspace reaches for the typed wrapper, never for new_id with a bare kind.
    expected = sorted(f"new_{kind.name.lower()}_id" for kind in IdKind)

    assert sorted(minting.__all__) == expected
    assert {kind for _, kind in _WRAPPERS} == set(IdKind)
    assert len(_WRAPPERS) == len(IdKind)
