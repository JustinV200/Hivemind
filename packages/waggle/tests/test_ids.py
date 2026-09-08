"""Property and unit tests for waggle.ids: IdKind, the generic generator and the two parsers.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises IdKind, new_id, parse_id and
    timestamp_of against a FakeClock so results are deterministic. The typed new_<kind>_id
    wrappers of the same module are covered by test_ids_minting.py, a split by feature under
    test (codingrules 5.1).

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.ids for the module under test.
    - waggle.clock for FakeClock, used throughout to control id timestamps.
    - test_ids_minting.py for the typed wrapper tests.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from waggle.clock import FakeClock
from waggle.errors import InvalidIdError
from waggle.ids import IdKind, new_id, parse_id, timestamp_of
from waggle.ulid import ULID_LENGTH

# The twelve kinds roadmap step 0.5 shipped plus MESSAGE (step 1.2a, the envelope's own id); a
# new member must be added here on purpose, because every kind also needs a NewType, a wrapper in
# waggle.ids and a row in the protocol spec.
_EXPECTED_KIND_COUNT = 13


def test_id_kind_has_thirteen_members_including_message() -> None:
    assert len(IdKind) == _EXPECTED_KIND_COUNT
    assert IdKind.MESSAGE in IdKind


def test_id_kind_message_uses_the_msg_abbreviation_as_its_prefix() -> None:
    # "msg" is one of the few abbreviations codingrules 6.2 permits; the prefix is what shows up
    # in every log line, so the abbreviation is deliberate, not an oversight.
    assert IdKind.MESSAGE.value == "msg"


def test_id_kind_prefixes_are_unique_lowercase_words() -> None:
    prefixes = [kind.value for kind in IdKind]

    assert len(set(prefixes)) == len(prefixes)
    assert all(prefix.isalpha() and prefix.islower() for prefix in prefixes)


@pytest.mark.parametrize("kind", list(IdKind))
def test_new_id_has_the_requested_kinds_prefix_and_length(kind: IdKind) -> None:
    clock = FakeClock()

    generated = new_id(kind, clock)

    prefix = f"{kind.value}_"
    assert generated.startswith(prefix)
    assert len(generated) == len(prefix) + ULID_LENGTH


@pytest.mark.parametrize("kind", list(IdKind))
def test_new_id_round_trips_through_parse_id(kind: IdKind) -> None:
    clock = FakeClock()

    generated = new_id(kind, clock)

    assert parse_id(generated, kind) == generated


def test_parse_id_rejects_wrong_prefix() -> None:
    clock = FakeClock()
    cell_id = new_id(IdKind.CELL, clock)

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

    task_id = new_id(IdKind.TASK, clock)

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
    ids = [new_id(IdKind.CELL, clock)]

    # Advance by at least one millisecond each time so every id gets a strictly later timestamp,
    # which is what the sort-by-creation-time property below actually claims.
    for delay_ms in delays_ms:
        clock.advance(delay_ms / 1000)
        ids.append(new_id(IdKind.CELL, clock))

    assert ids == sorted(ids)
