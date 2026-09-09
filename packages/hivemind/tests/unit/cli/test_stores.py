"""Tests for hivemind.cli.stores: store composition plus the manifest-to-llm conversion helpers.

Fits into the Hive:
    Mirrors src/hivemind/cli/stores.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.stores for the module under test.
    - docs/manifests/ for the example manifests the manifest-backed tests load.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, TaskFilter
from hivemind.cli.stores import (
    DEFAULT_DB,
    build_forage_map,
    build_registry,
    open_chamber,
    open_trail,
    provider_configs,
    slot_bindings,
)
from hivemind.forage import ModelSlot
from hivemind.manifest import load_manifest
from hivemind.pheromone import SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock, SystemClock
from waggle.ids import new_hive_id, new_node_id

# Five parents up from packages/hivemind/tests/unit/cli/test_stores.py, matching
# tests/unit/llm/test_registry.py's own _REPO_ROOT depth.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MANIFESTS_DIR = _REPO_ROOT / "docs" / "manifests"


def _identity() -> ChamberIdentity:
    clock = SystemClock()
    return ChamberIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system")


def test_default_db_is_hive_sqlite3_in_the_current_directory() -> None:
    assert Path("hive.sqlite3") == DEFAULT_DB


def test_open_trail_returns_a_ready_sqlite_trail_on_a_fresh_file(tmp_path: Path) -> None:
    # open_trail runs its own asyncio.run internally (it is a sync composition function, matching
    # a typer command body), so this test stays sync too rather than nesting event loops.
    trail = open_trail(tmp_path / "hive.sqlite3")

    assert isinstance(trail, SqlitePheromoneTrail)
    assert asyncio.run(trail.query(TrailQuery())) == ()


def test_open_chamber_applies_both_subsystems_migrations_on_a_fresh_file(tmp_path: Path) -> None:
    chamber = open_chamber(tmp_path / "hive.sqlite3", _identity())

    assert isinstance(chamber, BroodChamber)
    assert asyncio.run(chamber.list(TaskFilter())) == ()


def test_open_chamber_works_on_a_file_open_trail_already_migrated(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    open_trail(db)  # migrates pheromone_events first, as a `hive trail` command would

    chamber = open_chamber(db, _identity())

    assert asyncio.run(chamber.list(TaskFilter())) == ()


# ──────────────────────────────────────────────────────────────────────────────
# slot_bindings() / provider_configs() / build_forage_map(): the manifest-to-llm conversion
# ──────────────────────────────────────────────────────────────────────────────


def test_slot_bindings_covers_every_llm_slots_row() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "full.toml")

    bindings = slot_bindings(manifest)

    assert {binding.key for binding in bindings} == set(manifest.llm.slots)


def test_provider_configs_derives_default_model_for_openai_compat_from_its_first_slot() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "local.toml")

    configs = provider_configs(manifest)

    # "queen" is local.toml's first [llm.slots] row bound to the "local" provider.
    assert configs["local"].default_model == manifest.llm.slots["queen"].model


def test_provider_configs_derives_default_model_even_for_a_non_openai_compat_provider() -> None:
    # provider_configs derives default_model from each provider's first [llm.slots] row
    # unconditionally (it does not know, or need to know, which kind will read it); only the
    # openai_compat factory (hivemind.llm.registry._build_openai_compat) actually uses the field,
    # so an anthropic provider carries a populated-but-unread default_model.
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")

    configs = provider_configs(manifest)

    assert configs["anthropic"].default_model == manifest.llm.slots["queen"].model


def test_build_forage_map_has_one_source_per_forage_map_entry() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "full.toml")

    forage_map = build_forage_map(manifest, FakeClock())

    assert {source.source_id for source in forage_map.sources()} == set(manifest.forage.map)


def test_build_forage_map_seeds_abundance_from_the_sources_own_seat_count() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")

    forage_map = build_forage_map(manifest, FakeClock())

    source = forage_map.get("anthropic_opus")
    assert source.abundance.seats_free == source.spec.seats
    assert source.distance is None  # Never measured yet.


# ──────────────────────────────────────────────────────────────────────────────
# build_registry(): every slot resolves, against all three example manifests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("filename", ["minimal.toml", "local.toml", "full.toml"])
def test_build_registry_resolves_every_model_slot(filename: str) -> None:
    manifest = load_manifest(_MANIFESTS_DIR / filename)
    registry = build_registry(manifest, {}, FakeClock())

    for slot in ModelSlot:
        bound = registry.bound(slot)
        assert bound.slot is slot
        assert bound.provider.name in manifest.llm.providers


def test_build_registry_constructs_a_provider_lazily_and_never_probes_it_unasked() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")
    registry = build_registry(manifest, {}, FakeClock())

    # names() lists every configured provider without constructing any of them.
    assert registry.names() == tuple(manifest.llm.providers)
    # health() reports only providers already constructed -- none yet, so no network probe fires
    # for "anthropic" just from building the registry (hivemind.llm.registry.ProviderRegistry's
    # own "constructs at most once, on first use" rule).
    assert asyncio.run(registry.health()) == {}

    # Resolving a slot constructs "anthropic" with no key set; AnthropicProvider.from_config
    # never raises for a missing key (a real call would be the first thing to fail on one), and
    # construction alone never reaches health()'s own network probe.
    bound = registry.bound(ModelSlot.QUEEN)
    assert bound.provider.name == "anthropic"
