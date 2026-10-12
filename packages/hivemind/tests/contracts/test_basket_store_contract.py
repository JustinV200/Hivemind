"""Contract suite for BasketStore: one contract, run over `:memory:` and temp-file SQLite stores.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of
    hivemind.basket.store.protocol.BasketStore's contract and runs against two harnesses of the
    one shipped implementation (`SqliteBasketStore`): metadata in `:memory:` and metadata in a
    temp database file, bytes always in a temp directory. A future fake joins the same fixture's
    params and must pass here before it is used anywhere else (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.store.protocol for the contract under test.
    - packages/hivemind/tests/contracts/test_leavings_store_contract.py for the pattern mirrored.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from builders.basket import DEFAULT_CONTENT, make_blob, make_manifest, open_test_basket_store

from hivemind.basket import (
    BasketStore,
    BlobIntegrityError,
    BlobNotFoundError,
    BlobRefusedError,
    BlobState,
    BlobTooLargeError,
    MediaKind,
    ShipmentNotFoundError,
)
from hivemind.cell import HoneyClearance
from waggle.clock import FakeClock
from waggle.ids import new_cell_id

_MAX_BYTES = 64  # Small enough that a test can exceed it with a short payload.


class Harness:
    """A store under test plus the clock that stamps it."""

    def __init__(self, store: BasketStore, clock: FakeClock) -> None:
        """Hold the store and its clock."""
        self.store = store
        self.clock = clock


@pytest.fixture(params=["memory", "file"])
async def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Harness:
    """Build a fresh store over `:memory:` or a temp database file."""
    clock = FakeClock()
    store = await open_test_basket_store(
        tmp_path, in_memory=request.param == "memory", clock=clock, max_bytes=_MAX_BYTES
    )
    return Harness(store, clock)


async def _deposit(
    store: BasketStore, content: bytes = DEFAULT_CONTENT, **overrides: object
) -> tuple[str, bool]:
    """Deposit `content` with a freshly signed manifest and return its hash and is_new."""
    blob = make_blob(content, **overrides)
    stored, is_new = await store.deposit(content, blob, make_manifest(blob))
    return stored.sha256, is_new


# ──────────────────────────────────────────────────────────────────────────────
# Deposit, read and dedupe
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_deposit_is_readable_by_hash_with_its_manifest_and_bytes(harness: Harness) -> None:
    blob = make_blob(DEFAULT_CONTENT)
    manifest = make_manifest(blob)

    stored, is_new = await harness.store.deposit(DEFAULT_CONTENT, blob, manifest)

    assert is_new and stored == blob
    assert await harness.store.get(blob.sha256) == blob
    assert await harness.store.manifest(blob.sha256) == manifest
    assert await harness.store.open_bytes(blob.sha256) == DEFAULT_CONTENT


async def test_depositing_the_same_bytes_twice_is_one_blob(harness: Harness) -> None:
    first, first_new = await _deposit(harness.store)
    second, second_new = await _deposit(harness.store)

    assert first == second and first_new and not second_new
    assert len(await harness.store.list_blobs()) == 1


async def test_a_repeat_deposit_may_raise_the_label_and_taint_but_never_lower_them(
    harness: Harness,
) -> None:
    sha256, _ = await _deposit(harness.store, clearance=HoneyClearance.C1)
    existing = await harness.store.get(sha256)

    # A more sensitive repeat raises the label and taints the blob, re-signed for the merge.
    raised = existing.model_copy(update={"clearance": HoneyClearance.C2, "tainted": True})
    merged, is_new = await harness.store.deposit(DEFAULT_CONTENT, raised, make_manifest(raised))
    # A less sensitive repeat changes nothing.
    lowered = existing.model_copy(update={"clearance": HoneyClearance.C0})
    again, _ = await harness.store.deposit(DEFAULT_CONTENT, lowered, make_manifest(lowered))

    assert not is_new and merged.clearance is HoneyClearance.C2 and merged.tainted
    assert again == merged and await harness.store.get(sha256) == merged
    assert (await harness.store.manifest(sha256)).blob == merged


async def test_a_deposit_whose_bytes_are_not_the_blob_is_an_integrity_error(
    harness: Harness,
) -> None:
    blob = make_blob(b"the declared bytes")

    with pytest.raises(BlobIntegrityError):
        await harness.store.deposit(b"other bytes entirely", blob, make_manifest(blob))
    with pytest.raises(BlobIntegrityError):
        await harness.store.deposit(
            b"the declared bytes", blob, make_manifest(make_blob(b"someone else"))
        )


async def test_a_deposit_over_the_store_cap_is_too_large(harness: Harness) -> None:
    big = b"x" * (_MAX_BYTES + 1)
    blob = make_blob(big)

    with pytest.raises(BlobTooLargeError):
        await harness.store.deposit(big, blob, make_manifest(blob))
    assert await harness.store.list_blobs() == ()


@pytest.mark.parametrize("sha256", ["f" * 64, "not-a-hash", "../../etc/passwd"])
async def test_reading_an_unknown_or_malformed_hash_is_not_found(
    harness: Harness, sha256: str
) -> None:
    with pytest.raises(BlobNotFoundError):
        await harness.store.get(sha256)
    with pytest.raises(BlobNotFoundError):
        await harness.store.manifest(sha256)
    with pytest.raises(BlobNotFoundError):
        await harness.store.open_bytes(sha256)


# ──────────────────────────────────────────────────────────────────────────────
# Listing and retiring
# ──────────────────────────────────────────────────────────────────────────────


async def test_list_blobs_filters_by_kind_state_and_origin_cell(harness: Harness) -> None:
    cell = new_cell_id(harness.clock)
    wheel, _ = await _deposit(harness.store, b"wheel", media_kind=MediaKind.WHEEL)
    data, _ = await _deposit(
        harness.store, b"data", media_kind=MediaKind.DATASET, origin_cell_id=cell
    )
    await harness.store.retire(wheel, "superseded")

    by_kind = await harness.store.list_blobs(media_kind=MediaKind.DATASET)
    by_state = await harness.store.list_blobs(state=BlobState.RETIRED)
    by_cell = await harness.store.list_blobs(origin_cell_id=cell)
    everything = await harness.store.list_blobs()

    assert [b.sha256 for b in by_kind] == [data] == [b.sha256 for b in by_cell]
    assert [b.sha256 for b in by_state] == [wheel]
    assert {b.sha256 for b in everything} == {wheel, data}


async def test_list_blobs_orders_oldest_deposit_first(harness: Harness) -> None:
    later = harness.clock.now() + timedelta(hours=1)
    late, _ = await _deposit(harness.store, b"late", deposited_at=later)
    early, _ = await _deposit(harness.store, b"early")

    assert [b.sha256 for b in await harness.store.list_blobs()] == [early, late]


async def test_a_retired_blob_keeps_its_row_but_refuses_its_bytes(harness: Harness) -> None:
    sha256, _ = await _deposit(harness.store)

    retired = await harness.store.retire(sha256, "withdrawn by the operator")

    assert retired.state is BlobState.RETIRED
    assert (await harness.store.get(sha256)).state is BlobState.RETIRED
    with pytest.raises(BlobRefusedError) as raised:
        await harness.store.open_bytes(sha256)
    assert raised.value.reason_code == "retired"


async def test_retire_is_idempotent_and_unknown_hashes_are_not_found(harness: Harness) -> None:
    sha256, _ = await _deposit(harness.store)

    await harness.store.retire(sha256, "once")
    again = await harness.store.retire(sha256, "twice")

    assert again.state is BlobState.RETIRED
    with pytest.raises(BlobNotFoundError):
        await harness.store.retire("e" * 64, "never held")


async def test_a_retired_blob_is_not_resurrected_by_a_repeat_deposit(harness: Harness) -> None:
    sha256, _ = await _deposit(harness.store)
    await harness.store.retire(sha256, "withdrawn")

    _, is_new = await _deposit(harness.store)

    assert not is_new and (await harness.store.get(sha256)).state is BlobState.RETIRED


# ──────────────────────────────────────────────────────────────────────────────
# Residency
# ──────────────────────────────────────────────────────────────────────────────


async def test_residency_orders_least_recently_used_first_and_counts_bytes(
    harness: Harness,
) -> None:
    cell = new_cell_id(harness.clock)
    first, _ = await _deposit(harness.store, b"aaaa")
    second, _ = await _deposit(harness.store, b"bbbbbb")
    now = harness.clock.now()

    await harness.store.mark_resident(cell, second, now)
    await harness.store.mark_resident(cell, first, now + timedelta(minutes=1))

    resident = await harness.store.resident(cell)
    assert [r.sha256 for r in resident] == [second, first]
    assert [r.size_bytes for r in resident] == [6, 4]
    assert await harness.store.resident_bytes(cell) == 10


async def test_marking_a_resident_blob_again_refreshes_last_used_only(harness: Harness) -> None:
    cell = new_cell_id(harness.clock)
    sha256, _ = await _deposit(harness.store)
    start = harness.clock.now()
    await harness.store.mark_resident(cell, sha256, start)

    await harness.store.mark_resident(cell, sha256, start + timedelta(hours=2))

    [row] = await harness.store.resident(cell)
    assert row.resident_since == start and row.last_used == start + timedelta(hours=2)


async def test_evict_forgets_the_residency_but_keeps_the_blob_and_is_idempotent(
    harness: Harness,
) -> None:
    cell = new_cell_id(harness.clock)
    sha256, _ = await _deposit(harness.store)
    await harness.store.mark_resident(cell, sha256, harness.clock.now())

    await harness.store.evict(cell, sha256)
    await harness.store.evict(cell, sha256)

    assert await harness.store.resident(cell) == ()
    assert await harness.store.resident_bytes(cell) == 0
    assert await harness.store.open_bytes(sha256) == DEFAULT_CONTENT


async def test_residency_is_per_cell(harness: Harness) -> None:
    here, there = new_cell_id(harness.clock), new_cell_id(harness.clock)
    sha256, _ = await _deposit(harness.store)

    await harness.store.mark_resident(here, sha256, harness.clock.now())

    assert len(await harness.store.resident(here)) == 1
    assert await harness.store.resident(there) == ()


async def test_marking_an_unknown_blob_resident_is_not_found(harness: Harness) -> None:
    with pytest.raises(BlobNotFoundError):
        await harness.store.mark_resident(new_cell_id(harness.clock), "d" * 64, harness.clock.now())


# ──────────────────────────────────────────────────────────────────────────────
# Shipments
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_queued_shipment_is_pending_until_completed(harness: Harness) -> None:
    sha256, _ = await _deposit(harness.store)
    cell = new_cell_id(harness.clock)

    queued = await harness.store.queue_shipment(sha256, cell, "needed by the next task")
    pending = await harness.store.pending_shipments()
    await harness.store.complete_shipment(queued.id)

    assert pending == (queued,) and queued.completed_at is None
    assert await harness.store.pending_shipments() == ()


async def test_completing_a_shipment_twice_is_fine_and_an_unknown_id_is_not(
    harness: Harness,
) -> None:
    sha256, _ = await _deposit(harness.store)
    queued = await harness.store.queue_shipment(sha256, new_cell_id(harness.clock), "r")

    await harness.store.complete_shipment(queued.id)
    await harness.store.complete_shipment(queued.id)

    with pytest.raises(ShipmentNotFoundError):
        await harness.store.complete_shipment(queued.id + 100)


async def test_shipments_are_listed_oldest_first_and_need_a_known_blob(harness: Harness) -> None:
    sha256, _ = await _deposit(harness.store)
    cell = new_cell_id(harness.clock)

    one = await harness.store.queue_shipment(sha256, cell, "one")
    two = await harness.store.queue_shipment(sha256, cell, "two")

    assert [s.id for s in await harness.store.pending_shipments()] == [one.id, two.id]
    with pytest.raises(BlobNotFoundError):
        await harness.store.queue_shipment("c" * 64, cell, "no such blob")


async def test_a_label_raise_whose_manifest_describes_another_label_is_refused(
    harness: Harness,
) -> None:
    sha256, _ = await _deposit(harness.store, clearance=HoneyClearance.C0)
    existing = await harness.store.get(sha256)
    raised = existing.model_copy(update={"clearance": HoneyClearance.C2})

    # The manifest still describes the old label, so it cannot vouch for the raise.
    with pytest.raises(BlobIntegrityError):
        await harness.store.deposit(DEFAULT_CONTENT, raised, make_manifest(existing))

    assert (await harness.store.get(sha256)).clearance is HoneyClearance.C0
