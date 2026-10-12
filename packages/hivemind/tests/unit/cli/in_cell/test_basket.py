"""Tests for hivemind.cli.in_cell.basket: the in-Cell Warden's resident Basket below its scratch.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/basket.py (codingrules section 3). The config is built from a
    real `HIVEMIND_*` environment, as a Virtual Cell reads it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.basket for the module under test.
    - hivemind.cli.in_cell.deps, which hands the result to the Warden.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.basket import make_blob, make_manifest
from structlog.testing import capture_logs

from hivemind.cli.in_cell.basket import build_resident_basket, resident_ceiling_bytes
from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.forage import Ceilings
from hivemind.manifest.env import read_in_cell_env
from hivemind.manifest.schema.royal_jelly import DEFAULT_RESIDENT_CEILING_BYTES
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer


def _config(scratch_root: Path) -> InCellRuntimeConfig:
    """A runtime config read from a real in-Cell environment whose scratch is `scratch_root`."""
    clock = FakeClock()
    environ = {
        "HIVEMIND_QUEEN_WAGGLE_URL": "ws://host.docker.internal:8710",
        "HIVEMIND_CELL_ID": new_cell_id(clock),
        "HIVEMIND_HIVE_ID": new_hive_id(clock),
        "HIVEMIND_QUEEN_NODE_ID": new_node_id(clock),
        "HIVEMIND_CELL_SIGNING_KEY": Ed25519Signer.generate().private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": Ed25519Signer.generate().public_key_bytes.hex(),
        "HIVEMIND_SCRATCH_ROOT": str(scratch_root),
    }
    return build_runtime_config(read_in_cell_env(environ), clock)


def _ceilings(resident_bytes: int) -> Ceilings:
    return Ceilings(
        max_sub_bees=1,
        model_vram_bytes=0,
        model_disk_bytes=0,
        exportable_seats=0,
        resident_basket_disk_bytes=resident_bytes,
    )


def test_the_resident_basket_lives_below_the_cells_scratch_root(tmp_path: Path) -> None:
    config = _config(tmp_path / "scratch")

    resident = build_resident_basket(config, FakeClock())

    assert resident is not None
    assert resident.cell_id == config.spawn_config.cell_id
    assert (tmp_path / "scratch" / "basket" / "basket.sqlite").is_file()


def test_the_resident_basket_is_usable_and_keeps_bytes_under_its_blobs_directory(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path / "scratch")
    resident = build_resident_basket(config, FakeClock())
    assert resident is not None
    blob = make_blob(b"resident bytes")

    async def scenario() -> bool:
        store = resident._store  # The store is private; the facade has no deposit of its own.
        await store.deposit(b"resident bytes", blob, make_manifest(blob))
        decision = await resident.admit(blob)
        return decision.admit and await resident.is_resident(blob.sha256)

    assert asyncio.run(scenario())
    assert any((tmp_path / "scratch" / "basket" / "blobs").rglob(blob.sha256))


def test_the_ceiling_without_a_grant_is_the_documented_default(tmp_path: Path) -> None:
    resident = build_resident_basket(_config(tmp_path / "scratch"), FakeClock())

    assert resident is not None
    assert resident.ceiling_bytes == DEFAULT_RESIDENT_CEILING_BYTES > 0


@pytest.mark.parametrize(("granted", "expected"), [(0, 777), (4096, 4096)])
def test_a_grants_ceiling_wins_only_when_it_is_non_zero(granted: int, expected: int) -> None:
    # 0 on the wire means "no ceiling beyond free disk"; a ResidentBasket would admit nothing at 0.
    assert resident_ceiling_bytes(_ceilings(granted), default=777) == expected


def test_no_ceilings_at_all_gives_the_default() -> None:
    assert resident_ceiling_bytes(None, default=777) == 777


def test_an_unwritable_scratch_gives_no_resident_basket_and_one_warning(tmp_path: Path) -> None:
    blocker = tmp_path / "scratch"
    blocker.write_text("a file where the scratch directory should be", encoding="utf-8")

    with capture_logs() as logs:
        resident = build_resident_basket(_config(blocker), FakeClock())

    assert resident is None
    assert [e["event"] for e in logs].count("in_cell.resident_basket_unavailable") == 1
