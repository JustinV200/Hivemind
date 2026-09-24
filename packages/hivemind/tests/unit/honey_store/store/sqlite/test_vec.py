"""Tests for hivemind.honey_store.store.sqlite.vec: the float32 codec and the Python fallback.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/store/sqlite/vec.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.store.sqlite.vec for the module under test.
"""

from __future__ import annotations

import math
import sqlite3

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hivemind.honey_store.store.sqlite.vec import (
    cosine_distance,
    cosine_distances,
    decode_vector,
    encode_vector,
    load_vector_extension,
)

# float32-representable components, so a round trip through the blob is exact.
_COMPONENT = st.integers(min_value=-1000, max_value=1000).map(lambda n: n / 8)
_VECTOR = st.lists(_COMPONENT, min_size=1, max_size=32)


@given(_VECTOR)
def test_a_vector_round_trips_through_its_blob(values: list[float]) -> None:
    assert list(decode_vector(encode_vector(values))) == values


def test_cosine_distance_is_zero_for_the_same_direction_and_two_for_the_opposite() -> None:
    assert cosine_distance((1.0, 2.0), (2.0, 4.0)) == pytest.approx(0.0)
    assert cosine_distance((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(2.0)


def test_cosine_distance_is_neutral_against_a_zero_vector() -> None:
    assert cosine_distance((0.0, 0.0), (1.0, 0.0)) == 1.0


def test_cosine_distance_refuses_vectors_of_different_lengths() -> None:
    with pytest.raises(ValueError):
        cosine_distance((1.0, 0.0), (1.0, 0.0, 0.0))


@given(_VECTOR, st.lists(_VECTOR, max_size=8))
def test_cosine_distances_match_one_at_a_time(query: list[float], rows: list[list[float]]) -> None:
    same_length = [row[: len(query)] + [0.0] * (len(query) - len(row)) for row in rows]

    batch = cosine_distances(query, (encode_vector(row) for row in same_length))

    single = [cosine_distance(query, decode_vector(encode_vector(row))) for row in same_length]
    assert batch == pytest.approx(single)
    assert all(not math.isnan(distance) for distance in batch)


def test_python_fallback_agrees_with_sqlite_vec_when_it_loads() -> None:
    connection = sqlite3.connect(":memory:")
    if not load_vector_extension(connection):
        pytest.skip("sqlite-vec cannot load on this host; the fallback is all there is.")
    query, row = (0.3, -1.2, 2.5), (1.0, 0.5, -0.25)

    (in_sql,) = connection.execute(
        "SELECT vec_distance_cosine(?, ?)", (encode_vector(row), encode_vector(query))
    ).fetchone()

    assert cosine_distances(query, [encode_vector(row)])[0] == pytest.approx(in_sql, abs=1e-6)
