"""Tests for hivemind.cli.compose.hive.build: measured grades in the ledger reach the Forage map.

Fits into the Hive:
    Mirrors the measured-grade half of src/hivemind/cli/compose/hive/build.py (codingrules
    section 3; split by feature, 14.2, from test_hive.py). Every Hive here is built from a real
    `builders.cli.fake_manifest` over the Hive's own SQLite file, whose ledger a test fills before
    the build, the way `hive llm eval --update-map` fills it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.hive.build for build_hive.
    - hivemind.cli.stores.build_forage_map for where a measured grade is applied.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cli import fake_manifest

from hivemind.cli.compose import build_hive
from hivemind.cli.stores import open_ledger
from hivemind.manifest import HiveManifest, load_manifest
from waggle.clock import SystemClock

_SOURCE_ID = "fake_default"  # The one [forage.map] entry fake_manifest writes.


def _manifest(tmp_path: Path) -> HiveManifest:
    """Load the fake manifest, whose `[hive] db` is the file the ledger below shares."""
    return load_manifest(fake_manifest(tmp_path), {})


def _grade_in_ledger(manifest: HiveManifest, source_id: str, grade: int) -> None:
    """Write one measured grade into the manifest's own ledger, as `--update-map` does."""
    store = open_ledger(manifest.resolve_path(manifest.hive.db))
    asyncio.run(store.put_measured_grade(source_id, grade, SystemClock().now(), "test run"))


def test_build_hive_applies_a_measured_grade_over_the_manifests_hand_set_one(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    hand_set = manifest.forage.map[_SOURCE_ID].grade
    measured = 1 if hand_set != 1 else 2
    _grade_in_ledger(manifest, _SOURCE_ID, measured)

    hive = build_hive(manifest, environ={}, clock=SystemClock())

    source = hive.queen_deps.map.get(_SOURCE_ID)
    assert source.spec.measured_grade == measured
    assert source.effective_grade == measured
    assert source.spec.grade == hand_set  # The manifest's own figure is never rewritten.


def test_build_hive_without_a_measured_grade_keeps_the_hand_set_one(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)

    hive = build_hive(manifest, environ={}, clock=SystemClock())

    source = hive.queen_deps.map.get(_SOURCE_ID)
    assert source.spec.measured_grade is None
    assert source.effective_grade == manifest.forage.map[_SOURCE_ID].grade


def test_build_hive_ignores_a_measured_grade_for_a_source_the_map_no_longer_holds(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    _grade_in_ledger(manifest, "removed-since-the-run", 5)

    hive = build_hive(manifest, environ={}, clock=SystemClock())

    assert [source.source_id for source in hive.queen_deps.map.sources()] == [_SOURCE_ID]
