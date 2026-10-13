"""Tests for hivemind.basket.store.sqlite beyond the contract: bytes on disk, persistence, healing.

The behaviour every BasketStore must show is in `tests/contracts/test_basket_store_contract.py`;
these cover what only the SQLite store does: where the bytes live, that a repeat deposit leaves the
file alone, that tampering with the file is caught on read, and that a reopened store sees its data.

Fits into the Hive:
    Mirrors src/hivemind/basket/store/sqlite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.store.sqlite for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.basket import DEFAULT_CONTENT, make_blob, make_manifest

from hivemind.basket import BlobIntegrityError, SqliteBasketStore
from hivemind.common.sqlite import connect
from waggle.clock import FakeClock


async def _store(tmp_path: Path) -> SqliteBasketStore:
    return await SqliteBasketStore.create(
        connect(tmp_path / "hive.db"), tmp_path / "blobs", FakeClock()
    )


async def test_bytes_land_in_the_two_digit_directory_named_by_their_hash(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    blob = make_blob()

    await store.deposit(DEFAULT_CONTENT, blob, make_manifest(blob))

    path = tmp_path / "blobs" / blob.sha256[:2] / blob.sha256
    assert path.read_bytes() == DEFAULT_CONTENT


async def test_a_repeat_deposit_does_not_rewrite_the_file(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    blob = make_blob()
    await store.deposit(DEFAULT_CONTENT, blob, make_manifest(blob))
    path = tmp_path / "blobs" / blob.sha256[:2] / blob.sha256
    before = path.stat()

    _, is_new = await store.deposit(DEFAULT_CONTENT, blob, make_manifest(blob))

    after = path.stat()
    assert not is_new and (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


async def test_a_file_changed_on_disk_is_refused_on_read(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    blob = make_blob()
    await store.deposit(DEFAULT_CONTENT, blob, make_manifest(blob))
    (tmp_path / "blobs" / blob.sha256[:2] / blob.sha256).write_bytes(b"tampered")

    with pytest.raises(BlobIntegrityError):
        await store.open_bytes(blob.sha256)


async def test_a_lost_file_is_restored_by_the_next_deposit_of_the_same_bytes(
    tmp_path: Path,
) -> None:
    store = await _store(tmp_path)
    blob = make_blob()
    await store.deposit(DEFAULT_CONTENT, blob, make_manifest(blob))
    (tmp_path / "blobs" / blob.sha256[:2] / blob.sha256).unlink()

    await store.deposit(DEFAULT_CONTENT, blob, make_manifest(blob))

    assert await store.open_bytes(blob.sha256) == DEFAULT_CONTENT


async def test_a_reopened_store_sees_its_blobs_residency_and_shipments(tmp_path: Path) -> None:
    first = await _store(tmp_path)
    blob = make_blob()
    await first.deposit(DEFAULT_CONTENT, blob, make_manifest(blob))
    shipment = await first.queue_shipment(blob.sha256, blob.origin_cell_id, "later")

    second = await _store(tmp_path)

    assert await second.get(blob.sha256) == blob
    assert await second.pending_shipments() == (shipment,)
    assert await second.open_bytes(blob.sha256) == DEFAULT_CONTENT


async def test_a_deposit_is_only_visible_once_its_bytes_are_complete(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    blob = make_blob()

    await store.deposit(DEFAULT_CONTENT, blob, make_manifest(blob))

    assert not list((tmp_path / "blobs").rglob("*.part"))
