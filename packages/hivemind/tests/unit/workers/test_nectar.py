"""Tests for hivemind.workers.nectar: cutting a Worker's Nectar into Waggle deposit chunks.

Fits into the Hive:
    Mirrors src/hivemind/workers/nectar.py (codingrules section 3). The chunk rule is also
    property-tested (codingrules 14.3), over sizes on both sides of every chunk boundary.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.nectar for the module under test.
    - docs/waggle/spec.md section 5 for the chunking rule asserted here.
"""

from __future__ import annotations

import hashlib

import pytest
from builders.honey_wire import make_deposit_meta
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from hivemind.workers.nectar import split_deposit
from waggle.clock import FakeClock
from waggle.ids import new_event_id
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.honey import NectarKind

_CLOCK = FakeClock()
# Sizes near every boundary a chunk can fall on, up to three chunks; each example is cheap (a
# zero-filled buffer), so a modest example count covers the edges without slowing the suite.
_SIZES = st.integers(min_value=1, max_value=3 * MAX_CHUNK_BYTES) | st.sampled_from(
    [1, MAX_CHUNK_BYTES - 1, MAX_CHUNK_BYTES, MAX_CHUNK_BYTES + 1, 2 * MAX_CHUNK_BYTES]
)


def test_a_small_deposit_is_one_final_chunk_carrying_its_whole_digest() -> None:
    content = b"The staging config lives at /etc/widgets/staging.toml."
    meta = make_deposit_meta(_CLOCK)

    (chunk,) = split_deposit(content, meta)

    assert chunk.chunk == content
    assert chunk.offset == 0
    assert chunk.final is True
    assert chunk.total_bytes == len(content)
    assert chunk.sha256 == hashlib.sha256(content).hexdigest()
    assert (chunk.kind, chunk.title, chunk.worker_id) == (meta.kind, meta.title, meta.worker_id)


def test_a_deposit_just_over_one_chunk_is_two_chunks_only_the_last_final() -> None:
    content = b"a" * MAX_CHUNK_BYTES + b"b"

    first, last = split_deposit(content, make_deposit_meta(_CLOCK))

    assert (first.offset, len(first.chunk), first.final) == (0, MAX_CHUNK_BYTES, False)
    assert (last.offset, last.chunk, last.final) == (MAX_CHUNK_BYTES, b"b", True)
    assert first.sha256 == last.sha256 == hashlib.sha256(content).hexdigest()


def test_an_exact_multiple_of_the_chunk_size_leaves_no_empty_trailing_chunk() -> None:
    chunks = split_deposit(b"c" * (2 * MAX_CHUNK_BYTES), make_deposit_meta(_CLOCK))

    assert [chunk.final for chunk in chunks] == [False, True]
    assert all(len(chunk.chunk) == MAX_CHUNK_BYTES for chunk in chunks)


def test_a_handoff_carries_its_checkpoint_event_on_every_chunk() -> None:
    event_id = new_event_id(_CLOCK)
    meta = make_deposit_meta(_CLOCK, kind=NectarKind.HANDOFF, event_id=event_id)

    chunks = split_deposit(b"h" * (MAX_CHUNK_BYTES + 1), meta)

    assert [chunk.event_id for chunk in chunks] == [event_id, event_id]


def test_an_empty_deposit_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one byte"):
        split_deposit(b"", make_deposit_meta(_CLOCK))


def test_metadata_the_wire_refuses_is_refused_here_too() -> None:
    # An event id belongs to a HANDOFF alone (NectarDeposit's own validator).
    meta = make_deposit_meta(_CLOCK, event_id=new_event_id(_CLOCK))

    with pytest.raises(ValidationError, match="event_id"):
        split_deposit(b"x", meta)


@settings(max_examples=25, deadline=None)
@given(size=_SIZES)
def test_chunks_reassemble_to_the_content_at_running_offsets(size: int) -> None:
    content = bytes(size)

    chunks = split_deposit(content, make_deposit_meta(_CLOCK))

    assert b"".join(chunk.chunk for chunk in chunks) == content
    running = 0
    for chunk in chunks:
        assert chunk.offset == running  # Every chunk starts at the running length.
        assert 0 < len(chunk.chunk) <= MAX_CHUNK_BYTES
        running += len(chunk.chunk)
    assert [chunk.final for chunk in chunks] == [False] * (len(chunks) - 1) + [True]
    assert {chunk.total_bytes for chunk in chunks} == {size}
