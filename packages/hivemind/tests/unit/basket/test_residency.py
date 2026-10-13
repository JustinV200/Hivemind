"""Tests for hivemind.basket.residency: the pure eviction decision and the per-Cell facade.

`decide_residency` is checked with hypothesis properties (never over the ceiling once the
evictions are applied, oldest-used first, an oversized blob refused with nothing evicted) and with
worked examples; `ResidentBasket` is checked against a real store.

Fits into the Hive:
    Mirrors src/hivemind/basket/residency.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.residency for the module under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from builders.basket import make_blob, make_manifest, open_test_basket_store
from hypothesis import given
from hypothesis import strategies as st

from hivemind.basket import (
    Blob,
    BlobIntegrityError,
    BlobNotFoundError,
    ResidentBasket,
    ResidentBlob,
    SqliteBasketStore,
    decide_residency,
)
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@st.composite
def _resident_sets(draw: st.DrawFn) -> list[ResidentBlob]:
    """A resident set with distinct hashes, sizes 1..1000 and arbitrary use times."""
    count = draw(st.integers(min_value=0, max_value=12))
    return [
        ResidentBlob(
            sha256=f"{index:064x}",
            size_bytes=draw(st.integers(min_value=1, max_value=1_000)),
            resident_since=_EPOCH + timedelta(minutes=draw(st.integers(0, 50))),
            last_used=_EPOCH + timedelta(minutes=draw(st.integers(0, 50))),
        )
        for index in range(count)
    ]


@given(_resident_sets(), st.integers(1, 2_000), st.integers(0, 6_000))
def test_an_admitted_blob_never_pushes_the_total_over_the_ceiling(
    resident: list[ResidentBlob], incoming: int, ceiling: int
) -> None:
    decision = decide_residency(resident, incoming, ceiling)

    if decision.admit:
        kept = sum(b.size_bytes for b in resident if b.sha256 not in decision.evict)
        assert kept + incoming <= ceiling


@given(_resident_sets(), st.integers(1, 2_000), st.integers(0, 6_000))
def test_evictions_are_a_prefix_of_the_oldest_used_first_order(
    resident: list[ResidentBlob], incoming: int, ceiling: int
) -> None:
    decision = decide_residency(resident, incoming, ceiling)

    order = [
        b.sha256 for b in sorted(resident, key=lambda b: (b.last_used, b.resident_since, b.sha256))
    ]
    assert list(decision.evict) == order[: len(decision.evict)]


@given(_resident_sets(), st.integers(1, 2_000), st.integers(0, 6_000))
def test_it_evicts_no_more_than_needed(
    resident: list[ResidentBlob], incoming: int, ceiling: int
) -> None:
    decision = decide_residency(resident, incoming, ceiling)

    if decision.admit and decision.evict:
        # Putting the last eviction back would not have fit: the decision is minimal.
        sizes = {b.sha256: b.size_bytes for b in resident}
        kept_with_last = (
            sum(b.size_bytes for b in resident if b.sha256 not in decision.evict)
            + sizes[decision.evict[-1]]
        )
        assert kept_with_last + incoming > ceiling


@given(_resident_sets(), st.integers(1, 2_000), st.integers(0, 6_000))
def test_a_blob_larger_than_the_whole_ceiling_is_refused_and_evicts_nothing(
    resident: list[ResidentBlob], incoming: int, ceiling: int
) -> None:
    decision = decide_residency(resident, incoming, ceiling)

    if incoming > ceiling:
        assert not decision.admit and decision.evict == ()
    else:
        assert decision.admit


def test_a_blob_that_fits_evicts_nothing() -> None:
    resident = [ResidentBlob("a" * 64, 100, _EPOCH, _EPOCH)]

    decision = decide_residency(resident, 50, 200)

    assert decision.admit and decision.evict == ()


def test_the_least_recently_used_goes_first_and_ties_break_on_arrival() -> None:
    old_use = ResidentBlob("a" * 64, 100, _EPOCH + timedelta(minutes=5), _EPOCH)
    tie_early = ResidentBlob("b" * 64, 100, _EPOCH, _EPOCH + timedelta(minutes=9))
    tie_late = ResidentBlob(
        "c" * 64, 100, _EPOCH + timedelta(minutes=3), _EPOCH + timedelta(minutes=9)
    )

    decision = decide_residency([tie_late, tie_early, old_use], 150, 300)

    assert decision.evict == ("a" * 64, "b" * 64)


def test_a_zero_ceiling_admits_nothing() -> None:
    assert not decide_residency([], 1, 0).admit


# ──────────────────────────────────────────────────────────────────────────────
# ResidentBasket over a real store
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _Rig:
    """A resident set for one Cell over a real store, with the clock that stamps it."""

    basket: ResidentBasket
    other_cell: ResidentBasket
    store: SqliteBasketStore
    clock: FakeClock


async def _rig(tmp_path: Path, ceiling: int) -> _Rig:
    clock = FakeClock()
    store = await open_test_basket_store(tmp_path, clock=clock)
    return _Rig(
        ResidentBasket(store, new_cell_id(clock), ceiling, clock),
        ResidentBasket(store, new_cell_id(clock), ceiling, clock),
        store,
        clock,
    )


async def _put(rig: _Rig, content: bytes) -> Blob:
    blob = make_blob(content)
    await rig.store.deposit(content, blob, make_manifest(blob))
    return blob


async def test_admit_makes_a_blob_resident_and_evicts_the_oldest_used_when_full(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path, ceiling=10)
    first, second, third = [await _put(rig, c) for c in (b"aaaaaa", b"bbbb", b"cccccc")]
    await rig.basket.admit(first)
    rig.clock.advance(1)
    await rig.basket.admit(second)
    rig.clock.advance(1)

    decision = await rig.basket.admit(third)

    assert decision.admit and decision.evict == (first.sha256,)
    assert not await rig.basket.is_resident(first.sha256)
    assert await rig.basket.is_resident(second.sha256) and await rig.basket.is_resident(
        third.sha256
    )


async def test_a_blob_over_the_ceiling_is_refused_and_the_resident_set_is_untouched(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path, ceiling=5)
    small, huge = await _put(rig, b"abc"), await _put(rig, b"way too large")
    await rig.basket.admit(small)

    decision = await rig.basket.admit(huge)

    assert not decision.admit and decision.evict == ()
    assert await rig.basket.is_resident(small.sha256)
    assert not await rig.basket.is_resident(huge.sha256)


async def test_admitting_a_resident_blob_again_evicts_nothing_and_refreshes_its_use(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path, ceiling=6)
    blob = await _put(rig, b"abcdef")
    await rig.basket.admit(blob)
    rig.clock.advance(60)

    decision = await rig.basket.admit(blob)

    [held] = await rig.store.resident(rig.basket.cell_id)
    assert decision.admit and decision.evict == () and held.last_used > held.resident_since


async def test_plan_reports_the_decision_without_changing_anything(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, ceiling=8)
    held = await _put(rig, b"aaaaaaaa")
    await rig.basket.admit(held)

    decision = await rig.basket.plan(4)

    assert decision.admit and decision.evict == (held.sha256,)
    assert await rig.basket.is_resident(held.sha256)


async def test_bytes_for_returns_resident_bytes_and_counts_as_a_use(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, ceiling=100)
    blob = await _put(rig, b"payload")
    await rig.basket.admit(blob)
    rig.clock.advance(30)

    data = await rig.basket.bytes_for(blob.sha256)

    [held] = await rig.store.resident(rig.basket.cell_id)
    assert data == b"payload" and held.last_used > held.resident_since


async def test_bytes_for_a_blob_that_is_not_resident_is_not_found(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, ceiling=100)
    blob = await _put(rig, b"payload")

    with pytest.raises(BlobNotFoundError):
        await rig.basket.bytes_for(blob.sha256)


async def test_bytes_for_refuses_bytes_that_do_not_hash_to_the_blob(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, ceiling=100)
    blob = await _put(rig, b"payload")
    await rig.basket.admit(blob)

    class LyingStore:
        """A store that is resident-aware but hands back the wrong bytes."""

        async def resident(self, cell_id: CellId) -> tuple[ResidentBlob, ...]:
            return await rig.store.resident(cell_id)

        async def open_bytes(self, sha256: str) -> bytes:
            return b"not the payload"

    liar = ResidentBasket(LyingStore(), rig.basket.cell_id, 100, rig.clock)  # type: ignore[arg-type]  # WHY: a deliberately partial fake store.

    with pytest.raises(BlobIntegrityError):
        await liar.bytes_for(blob.sha256)


async def test_evict_drops_residency_for_one_cell_only(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, ceiling=100)
    blob = await _put(rig, b"payload")
    await rig.basket.admit(blob)
    await rig.other_cell.admit(blob)

    await rig.basket.evict(blob.sha256)

    assert not await rig.basket.is_resident(blob.sha256)
    assert await rig.other_cell.is_resident(blob.sha256)
