"""Tests for hivemind.honey_store.nectar.reassembly: ChunkGroups and the spec's section 5 rules.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/nectar/reassembly.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.nectar.reassembly for the module under test.
    - docs/waggle/spec.md section 5 for the rules each test states.
"""

from __future__ import annotations

import pytest
from builders.honey import make_deposit_chunks, make_nectar_deposit
from hypothesis import given
from hypothesis import strategies as st

from hivemind.honey_store.errors import (
    ChunkMismatchError,
    DepositLengthMismatchError,
    DepositTimedOutError,
    FirstChunkNotAtZeroError,
    NectarRejectedError,
    NectarTooLargeError,
    OffsetMismatchError,
    Sha256MismatchError,
    TooManyOpenDepositsError,
)
from hivemind.honey_store.nectar.reassembly import (
    CHUNK_GROUP_TIMEOUT_S,
    MAX_OPEN_CHUNK_GROUPS,
    ChunkGroups,
)
from waggle.clock import FakeClock
from waggle.ids import new_nectar_id, new_warden_id, new_worker_id
from waggle.messages.honey import NectarDeposit

_MAX_BYTES = 1_000_000  # Far above every test deposit, unless a test is about the cap itself.
_CONTENT = b"0123456789" * 10  # 100 bytes: several chunks at the sizes below.


def _chunks(content: bytes = _CONTENT, size: int = 30) -> list[NectarDeposit]:
    """Split `content` into `size`-byte chunks sharing one fresh deposit's metadata."""
    return make_deposit_chunks(content, size, make_nectar_deposit())


def _open_group(groups: ChunkGroups, sender: str, clock: FakeClock) -> None:
    """Open one incomplete group for `sender` by feeding a fresh deposit's first chunk only."""
    # A fresh id salts the content, so every call opens a group with its own digest (group key).
    content = f"deposit {new_nectar_id(clock)}".encode()
    first = make_deposit_chunks(content, 4, make_nectar_deposit(clock))[0]
    assert groups.accept(sender, first, clock.now(), _MAX_BYTES) is None


# ──────────────────────────────────────────────────────────────────────────────
# Completing a deposit
# ──────────────────────────────────────────────────────────────────────────────


def test_accept_returns_a_single_chunk_deposit_at_once() -> None:
    groups = ChunkGroups()
    deposit = make_nectar_deposit(chunk=b"one whole finding")

    whole = groups.accept("warden_x", deposit, FakeClock().now(), _MAX_BYTES)

    assert whole == b"one whole finding"
    assert groups.open_count() == 0


def test_accept_returns_none_until_the_final_chunk_then_the_whole_content() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    chunks = _chunks()

    partial = [groups.accept("warden_x", chunk, clock.now(), _MAX_BYTES) for chunk in chunks[:-1]]
    whole = groups.accept("warden_x", chunks[-1], clock.now(), _MAX_BYTES)

    assert partial == [None] * (len(chunks) - 1)
    assert whole == _CONTENT
    assert groups.open_count() == 0


def test_accept_keeps_two_workers_behind_one_warden_apart() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    first = _chunks()
    second = [chunk.model_copy(update={"worker_id": new_worker_id(clock)}) for chunk in first]

    groups.accept("warden_x", first[0], clock.now(), _MAX_BYTES)
    groups.accept("warden_x", second[0], clock.now(), _MAX_BYTES)

    assert groups.open_count("warden_x") == 2


# ──────────────────────────────────────────────────────────────────────────────
# Refusals: each drops the group and raises the matching reason
# ──────────────────────────────────────────────────────────────────────────────


def test_accept_refuses_a_first_chunk_not_at_offset_zero() -> None:
    groups = ChunkGroups()

    with pytest.raises(FirstChunkNotAtZeroError):
        groups.accept("warden_x", _chunks()[1], FakeClock().now(), _MAX_BYTES)
    assert groups.open_count() == 0


def test_accept_refuses_a_declared_total_over_the_cap_on_the_first_chunk() -> None:
    groups = ChunkGroups()

    with pytest.raises(NectarTooLargeError) as caught:
        groups.accept("warden_x", _chunks()[0], FakeClock().now(), len(_CONTENT) - 1)

    assert caught.value.total_bytes == len(_CONTENT)
    assert groups.open_count() == 0


def test_accept_refuses_a_ninth_open_group_but_still_takes_single_chunk_deposits() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    for _ in range(MAX_OPEN_CHUNK_GROUPS):
        _open_group(groups, "warden_x", clock)

    with pytest.raises(TooManyOpenDepositsError):
        _open_group(groups, "warden_x", clock)
    whole = groups.accept("warden_x", make_nectar_deposit(chunk=b"small"), clock.now(), _MAX_BYTES)
    _open_group(groups, new_warden_id(clock), clock)

    assert whole == b"small"
    assert groups.open_count("warden_x") == MAX_OPEN_CHUNK_GROUPS


def test_accept_sweeps_timed_out_groups_before_counting_the_senders_limit() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    for _ in range(MAX_OPEN_CHUNK_GROUPS):
        _open_group(groups, "warden_x", clock)
    clock.advance(CHUNK_GROUP_TIMEOUT_S + 1)

    _open_group(groups, "warden_x", clock)

    assert groups.open_count("warden_x") == 1


def test_accept_refuses_a_gap_and_drops_the_group() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    chunks = _chunks()
    groups.accept("warden_x", chunks[0], clock.now(), _MAX_BYTES)

    with pytest.raises(OffsetMismatchError) as caught:
        groups.accept("warden_x", chunks[2], clock.now(), _MAX_BYTES)

    assert (caught.value.expected_offset, caught.value.chunk_offset) == (30, 60)
    assert groups.open_count() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("title", "A different title"), ("media_type", "text/markdown"), ("total_bytes", 101)],
)
def test_accept_refuses_a_chunk_whose_metadata_differs_from_the_first(
    field: str, value: object
) -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    chunks = _chunks()
    groups.accept("warden_x", chunks[0], clock.now(), _MAX_BYTES)

    with pytest.raises(ChunkMismatchError) as caught:
        groups.accept(
            "warden_x", chunks[1].model_copy(update={field: value}), clock.now(), _MAX_BYTES
        )

    assert caught.value.field == field
    assert groups.open_count() == 0


def test_accept_refuses_bytes_past_the_declared_total_before_the_final_chunk() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    first = _chunks()[0].model_copy(update={"final": False})
    overrun = first.model_copy(update={"chunk": b"x" * 80, "offset": 30})
    groups.accept("warden_x", first, clock.now(), _MAX_BYTES)

    with pytest.raises(DepositLengthMismatchError) as caught:
        groups.accept("warden_x", overrun, clock.now(), _MAX_BYTES)

    assert (caught.value.total_bytes, caught.value.received_bytes) == (100, 110)
    assert groups.open_count() == 0


def test_accept_refuses_a_final_chunk_that_leaves_the_deposit_short() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    chunks = _chunks()
    groups.accept("warden_x", chunks[0], clock.now(), _MAX_BYTES)

    with pytest.raises(DepositLengthMismatchError):
        groups.accept(
            "warden_x", chunks[1].model_copy(update={"final": True}), clock.now(), _MAX_BYTES
        )
    assert groups.open_count() == 0


def test_accept_refuses_a_whole_that_does_not_hash_to_the_declared_digest() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    chunks = _chunks()
    tampered = chunks[-1].model_copy(update={"chunk": b"X" * len(chunks[-1].chunk)})
    for chunk in chunks[:-1]:
        groups.accept("warden_x", chunk, clock.now(), _MAX_BYTES)

    with pytest.raises(Sha256MismatchError):
        groups.accept("warden_x", tampered, clock.now(), _MAX_BYTES)
    assert groups.open_count() == 0


def test_accept_refuses_a_chunk_arriving_after_its_group_timed_out() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    chunks = _chunks()
    groups.accept("warden_x", chunks[0], clock.now(), _MAX_BYTES)
    clock.advance(CHUNK_GROUP_TIMEOUT_S + 1)

    with pytest.raises(DepositTimedOutError):
        groups.accept("warden_x", chunks[1], clock.now(), _MAX_BYTES)
    assert groups.open_count() == 0


# ──────────────────────────────────────────────────────────────────────────────
# expire
# ──────────────────────────────────────────────────────────────────────────────


def test_expire_drops_only_groups_idle_past_the_timeout() -> None:
    groups = ChunkGroups()
    clock = FakeClock()
    _open_group(groups, "warden_old", clock)
    clock.advance(CHUNK_GROUP_TIMEOUT_S)
    _open_group(groups, "warden_new", clock)
    clock.advance(1)

    dropped = groups.expire(clock.now())

    assert dropped == 1
    assert groups.open_count("warden_old") == 0
    assert groups.open_count("warden_new") == 1


# ──────────────────────────────────────────────────────────────────────────────
# Properties: ordering and the digest check
# ──────────────────────────────────────────────────────────────────────────────


@given(content=st.binary(min_size=1, max_size=400), size=st.integers(min_value=1, max_value=64))
def test_in_order_chunks_always_reassemble_exactly(content: bytes, size: int) -> None:
    groups = ChunkGroups()
    now = FakeClock().now()
    chunks = make_deposit_chunks(content, size, make_nectar_deposit())

    results = [groups.accept("warden_x", chunk, now, _MAX_BYTES) for chunk in chunks]

    assert results[:-1] == [None] * (len(chunks) - 1)
    assert results[-1] == content


@given(
    content=st.binary(min_size=2, max_size=300),
    size=st.integers(min_value=1, max_value=40),
    data=st.data(),
)
def test_out_of_order_chunks_are_refused_never_misassembled(
    content: bytes, size: int, data: st.DataObject
) -> None:
    groups = ChunkGroups()
    now = FakeClock().now()
    chunks = make_deposit_chunks(content, size, make_nectar_deposit())
    order = data.draw(st.permutations(chunks))

    try:
        results = [groups.accept("warden_x", chunk, now, _MAX_BYTES) for chunk in order]
    except NectarRejectedError:
        # A refusal is always a correct answer to a reordering: nothing was reassembled wrongly.
        return
    # Accepted end to end only when the order was already the right one, and then exactly.
    assert list(order) == chunks
    assert results[-1] == content


@given(
    content=st.binary(min_size=1, max_size=300),
    size=st.integers(min_value=1, max_value=40),
    data=st.data(),
)
def test_a_flipped_byte_anywhere_fails_the_digest_check(
    content: bytes, size: int, data: st.DataObject
) -> None:
    groups = ChunkGroups()
    now = FakeClock().now()
    chunks = make_deposit_chunks(content, size, make_nectar_deposit())
    victim = data.draw(st.integers(min_value=0, max_value=len(chunks) - 1))
    position = data.draw(st.integers(min_value=0, max_value=len(chunks[victim].chunk) - 1))
    flipped = bytearray(chunks[victim].chunk)
    flipped[position] ^= 0xFF
    chunks[victim] = chunks[victim].model_copy(update={"chunk": bytes(flipped)})

    with pytest.raises(Sha256MismatchError):
        for chunk in chunks:
            groups.accept("warden_x", chunk, now, _MAX_BYTES)
