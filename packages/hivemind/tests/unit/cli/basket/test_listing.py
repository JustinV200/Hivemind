"""Tests for hivemind.cli.basket.listing: `hive basket list` and `hive basket show`.

Fits into the Hive:
    Mirrors src/hivemind/cli/basket/listing.py (codingrules section 3). Drives the real typer app
    against a real fake-provider manifest and SQLite file (`harness`), with blobs deposited through
    the CLI's own door so each carries the Hive key's real signature and a real Honey record.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.basket.listing for the module under test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from unit.cli.basket.harness import (
    corrupt_signature,
    deposit,
    hive_stand,
    invoke,
    make_hive,
    store_of,
    stored,
)

from waggle.clock import SystemClock
from waggle.ids import new_cell_id


def test_list_says_so_when_the_basket_is_empty(tmp_path: Path) -> None:
    result = invoke(make_hive(tmp_path), "list")

    assert result.exit_code == 0, result.output
    assert "no blobs in the Basket" in result.stdout


def test_list_prints_each_blob_with_its_short_hash_and_facts(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest, "listed", "--platform", "linux")

    result = invoke(manifest, "list")

    assert result.exit_code == 0, result.output
    header, line = result.stdout.splitlines()
    assert header.split()[:3] == ["SHA256", "SIZE", "KIND"]
    cells = line.split()
    assert cells[:6] == [sha256[:12], "6", "TEXT", "text/plain", "LINUX", "C2"]
    assert cells[6] == str(hive_stand(manifest))
    assert cells[-2:] == ["no", "PRESENT"]


def test_list_narrows_by_kind_state_and_origin_cell(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest, "to be retired")
    deposit(manifest, "stays")
    invoke(manifest, "retire", sha256, "--reason", "Withdrawn.")

    retired = invoke(manifest, "list", "--state", "retired")
    present = invoke(manifest, "list", "--state", "present", "--kind", "text")
    other_kind = invoke(manifest, "list", "--kind", "wheel")
    other_cell = invoke(manifest, "list", "--cell", str(new_cell_id(SystemClock())))
    own_cell = invoke(manifest, "list", "--cell", str(hive_stand(manifest)))

    assert sha256[:12] in retired.stdout and len(retired.stdout.splitlines()) == 2
    assert sha256[:12] not in present.stdout and len(present.stdout.splitlines()) == 2
    assert "no blobs in the Basket" in other_kind.stdout + other_cell.stdout
    assert len(own_cell.stdout.splitlines()) == 3


def test_list_json_is_one_document_with_every_blob(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)

    result = invoke(manifest, "list", "--json")

    (blob,) = json.loads(result.stdout)["blobs"]
    assert blob["sha256"] == sha256 and blob["clearance"] == "C2" and blob["tainted"] is False


def test_show_verifies_the_signature_and_finds_the_honey_record(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)

    result = invoke(manifest, "show", sha256)

    assert result.exit_code == 0, result.output
    assert f"sha256: {sha256}" in result.stdout
    assert "signature: verifies against the Hive key" in result.stdout
    assert "signer: node_" in result.stdout
    assert f"honey record: recorded (source_key basket:{sha256})" in result.stdout
    assert "resident: nowhere known" in result.stdout


def test_show_takes_a_prefix_and_lists_the_cells_that_hold_the_blob(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)
    blob = stored(manifest, sha256)
    asyncio.run(store_of(manifest).mark_resident(hive_stand(manifest), sha256, blob.deposited_at))

    result = invoke(manifest, "show", sha256[:10])

    assert result.exit_code == 0, result.output
    assert f"resident: {hive_stand(manifest)} since" in result.stdout


def test_show_json_carries_the_verdict_and_the_source_key(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)

    document = json.loads(invoke(manifest, "show", sha256, "--json").stdout)

    assert document["signature_verifies"] is True and document["honey_recorded"] is True
    assert document["source_key"] == f"basket:{sha256}"
    assert document["blob"]["sha256"] == sha256


def test_show_of_a_tampered_manifest_prints_the_blob_and_exits_1(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    sha256 = deposit(manifest)
    corrupt_signature(manifest, sha256)

    result = invoke(manifest, "show", sha256)

    assert result.exit_code == 1
    assert "signature: DOES NOT VERIFY (hivemind.basket.blob_integrity" in result.stdout


def test_show_of_an_unknown_blob_exits_1_with_the_baskets_code(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    deposit(manifest)

    unknown = invoke(manifest, "show", "ff" * 32)
    prefix = invoke(manifest, "show", "ffff")

    for result in (unknown, prefix):
        assert result.exit_code == 1
        assert result.stderr.startswith("hivemind.basket.blob_not_found")
