"""Tests for hivemind.manifest.schema.manifest: HiveManifest, resolve_path, cross-section checks.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/manifest.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.manifest for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.forage import ModelSlot
from hivemind.manifest.schema import HiveManifest
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

_CLOCK = FakeClock()


def _smallest_passing_manifest_data() -> dict[str, object]:
    """The smallest input HiveManifest actually validates: hive, every slot, and drone."""
    # [llm] and [forage] both structurally default to an empty table, but each carries its own
    # content requirement an empty table cannot satisfy (see the module docstring), so a real
    # "minimal" manifest supplies both -- this mirrors docs/manifests/minimal.toml's shape.
    return {
        "hive": {"id": new_hive_id(_CLOCK), "node_id": new_node_id(_CLOCK)},
        "llm": {
            "providers": {"anthropic": {"kind": "anthropic"}},
            "slots": {
                slot.manifest_key: {"provider": "anthropic", "model": "test-model"}
                for slot in ModelSlot
            },
        },
        "forage": {
            "roles": {
                "drone": {"cpu_cores": 0.5, "memory_bytes": 1024, "token_rate_per_minute": 100.0}
            }
        },
    }


def test_hive_manifest_validates_with_only_hive_llm_and_drone_supplied() -> None:
    manifest = HiveManifest.model_validate(_smallest_passing_manifest_data())

    assert manifest.queen.tick_interval_s == 0.25
    assert manifest.security.default_comb_shield.value == "MEADOW"
    assert manifest.source_path is None


def test_hive_manifest_requires_the_hive_section() -> None:
    with pytest.raises(ValidationError):
        HiveManifest.model_validate({})


def test_hive_manifest_rejects_llm_or_forage_left_at_their_empty_default() -> None:
    # Documents the nuance the module docstring calls out: [llm] and [forage] have a
    # default_factory, but the factory's own output fails each section's content validator.
    with pytest.raises(ValidationError):
        HiveManifest.model_validate(
            {"hive": {"id": new_hive_id(_CLOCK), "node_id": new_node_id(_CLOCK)}}
        )


def _with_forage_map_entry(provider: str) -> dict[str, object]:
    """Return `_smallest_passing_manifest_data`'s dict with one `[forage.map]` entry added."""
    data = _smallest_passing_manifest_data()
    forage_data = data["forage"]
    assert isinstance(forage_data, dict)  # Narrows from `object` so dict(...) below type-checks.
    forage: dict[str, object] = dict(forage_data)
    forage["map"] = {
        "src1": {
            "provider": provider,
            "model": "test-model",
            "grade": 3,
            "context_window": 8192,
        }
    }
    data["forage"] = forage
    return data


def test_hive_manifest_rejects_a_forage_map_entry_with_an_undeclared_provider() -> None:
    data = _with_forage_map_entry(provider="not_declared")

    with pytest.raises(ValidationError, match="not_declared"):
        HiveManifest.model_validate(data)


def test_hive_manifest_accepts_a_forage_map_entry_with_a_declared_provider() -> None:
    data = _with_forage_map_entry(provider="anthropic")

    manifest = HiveManifest.model_validate(data)

    assert "src1" in manifest.forage.map


def test_resolve_path_returns_an_absolute_path_unchanged(tmp_path: Path) -> None:
    manifest = HiveManifest.model_validate(_smallest_passing_manifest_data())
    absolute = tmp_path / "somewhere.txt"

    assert manifest.resolve_path(absolute) == absolute


def test_resolve_path_joins_onto_the_manifest_directory_when_source_path_is_set() -> None:
    manifest = HiveManifest.model_validate(_smallest_passing_manifest_data())
    manifest = manifest.model_copy(update={"source_path": Path("/hive/manifest.toml")})

    assert manifest.resolve_path(Path("data/hive.sqlite3")) == Path("/hive/data/hive.sqlite3")


def test_resolve_path_falls_back_to_cwd_when_source_path_is_none() -> None:
    manifest = HiveManifest.model_validate(_smallest_passing_manifest_data())

    assert manifest.resolve_path(Path("data/hive.sqlite3")) == Path.cwd() / "data/hive.sqlite3"


def test_hive_manifest_is_frozen_and_forbids_extra_sections() -> None:
    manifest = HiveManifest.model_validate(_smallest_passing_manifest_data())

    with pytest.raises(ValidationError, match="frozen"):
        manifest.pheromone = manifest.pheromone  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        HiveManifest.model_validate({**_smallest_passing_manifest_data(), "nope": {}})


def test_hive_manifest_source_path_is_excluded_from_model_dump() -> None:
    manifest = HiveManifest.model_validate(_smallest_passing_manifest_data())
    manifest = manifest.model_copy(update={"source_path": Path("/hive/manifest.toml")})

    assert "source_path" not in manifest.model_dump()
