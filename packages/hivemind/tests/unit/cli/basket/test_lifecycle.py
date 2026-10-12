"""Tests for hivemind.cli.basket.lifecycle: `hive basket ship`, `evict` and `retire`.

Fits into the Hive:
    Mirrors src/hivemind/cli/basket/lifecycle.py (codingrules section 3). Drives the real typer app
    against a real fake-provider manifest and SQLite file (`harness`); each act is read back through
    the real Basket store and trail, and each refusal must leave both exactly as they were.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.basket.lifecycle for the module under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from unit.cli.basket.harness import (
    deposit,
    hive_stand,
    invoke,
    make_hive,
    mark_tainted,
    store_of,
    stored,
    trail_events,
)

from hivemind.basket import BlobRefusedError, BlobState
from waggle.clock import SystemClock
from waggle.ids import CellId, new_cell_id


def _hold(manifest: Path, sha256: str, cell_id: CellId) -> None:
    """Make `cell_id` hold the blob resident, as a Warden's receiver would have."""
    blob = stored(manifest, sha256)
    asyncio.run(store_of(manifest).mark_resident(cell_id, sha256, blob.deposited_at))


def _pending(manifest: Path) -> tuple[tuple[str, str, str], ...]:
    """Return each queued shipment as (blob, destination, reason)."""
    queue = asyncio.run(store_of(manifest).pending_shipments())
    return tuple((s.sha256, s.destination_cell_id, s.reason) for s in queue)


def test_ship_queues_a_shipment_and_prints_its_id(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)
    destination = new_cell_id(SystemClock())

    result = invoke(manifest, "ship", sha256[:10], str(destination), "--reason", "Needed there.")

    assert result.exit_code == 0, result.output
    assert f"queued shipment 1: {sha256} to {destination}" in result.stdout
    assert _pending(manifest) == ((sha256, str(destination), "Needed there."),)


def test_ship_refuses_a_retired_or_tainted_blob_and_a_bad_cell(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    retired = deposit(manifest, "retired blob")
    tainted = deposit(manifest, "tainted blob")
    invoke(manifest, "retire", retired, "--reason", "Withdrawn.")
    mark_tainted(manifest, tainted)
    cell = str(new_cell_id(SystemClock()))

    after_retire = invoke(manifest, "ship", retired, cell, "--reason", "No.")
    after_taint = invoke(manifest, "ship", tainted, cell, "--reason", "No.")
    not_a_cell = invoke(manifest, "ship", tainted, "somewhere", "--reason", "No.")
    unknown = invoke(manifest, "ship", "ff" * 32, cell, "--reason", "No.")

    assert (after_retire.exit_code, after_taint.exit_code, unknown.exit_code) == (1, 1, 1)
    assert "is retired and is never shipped" in after_retire.stderr
    assert "is tainted and is never shipped" in after_taint.stderr
    assert unknown.stderr.startswith("hivemind.basket.blob_not_found")
    assert not_a_cell.exit_code == 2 and "is not a Cell id" in not_a_cell.stderr
    assert _pending(manifest) == ()


def test_evict_frees_the_hive_stands_copy_by_default_and_records_it(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)
    _hold(manifest, sha256, hive_stand(manifest))

    result = invoke(manifest, "evict", sha256)

    assert result.exit_code == 0, result.output
    assert f"blob {sha256} evicted from {hive_stand(manifest)}" in result.stdout
    assert asyncio.run(store_of(manifest).resident(hive_stand(manifest))) == ()
    (event,) = trail_events(manifest, "basket.evicted")
    assert event.actor == "human" and event.subject_id == hive_stand(manifest)
    assert event.payload["sha256"] == sha256
    assert stored(manifest, sha256).state is BlobState.PRESENT  # Only the copy goes.


def test_evict_names_another_cell_and_says_when_nothing_was_resident(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)
    other = new_cell_id(SystemClock())
    _hold(manifest, sha256, other)

    elsewhere = invoke(manifest, "evict", sha256, "--cell", str(other))
    nothing = invoke(manifest, "evict", sha256)

    assert f"evicted from {other}" in elsewhere.stdout
    assert f"was not resident in {hive_stand(manifest)}" in nothing.stdout
    assert len(trail_events(manifest, "basket.evicted")) == 1  # The no-op recorded nothing.


def test_evict_of_an_unknown_blob_exits_1(tmp_path: Path) -> None:
    result = invoke(make_hive(tmp_path), "evict", "ff" * 32)

    assert result.exit_code == 1
    assert result.stderr.startswith("hivemind.basket.blob_not_found")


def test_retire_withdraws_the_blob_and_records_it_without_the_reason(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)

    result = invoke(manifest, "retire", sha256, "--reason", "Superseded by a newer wheel.")

    assert result.exit_code == 0, result.output
    assert f"blob {sha256} retired" in result.stdout
    assert stored(manifest, sha256).state is BlobState.RETIRED
    (event,) = trail_events(manifest, "basket.retired")
    assert event.actor == "human" and event.payload["sha256"] == sha256
    assert "Superseded" not in str(event.payload)  # The reason stays on the row, for the human.
    with pytest.raises(BlobRefusedError):  # Readers are refused; the bytes stay on disk.
        asyncio.run(store_of(manifest).open_bytes(sha256))


def test_retiring_twice_records_one_event(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)
    invoke(manifest, "retire", sha256, "--reason", "First.")

    again = invoke(manifest, "retire", sha256, "--reason", "Second.")

    assert again.exit_code == 0, again.output
    assert f"blob {sha256} was already retired" in again.stdout
    assert len(trail_events(manifest, "basket.retired")) == 1


def test_retire_and_ship_need_a_reason_and_a_known_blob(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)
    cell = str(new_cell_id(SystemClock()))

    blank_retire = invoke(manifest, "retire", sha256, "--reason", "  ")
    blank_ship = invoke(manifest, "ship", sha256, cell, "--reason", "")
    unknown = invoke(manifest, "retire", "ff" * 32, "--reason", "Gone.")

    assert blank_retire.exit_code == 2 and blank_ship.exit_code == 2
    assert unknown.exit_code == 1 and unknown.stderr.startswith("hivemind.basket.blob_not_found")
    assert stored(manifest, sha256).state is BlobState.PRESENT
    assert trail_events(manifest, "basket.retired") == [] and _pending(manifest) == ()
