"""Unit tests for hivemind.cli.compose.virtual_cell_providers: cell_providers, cell_slots, keys.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/cli/compose/virtual_cell_providers.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.virtual_cell_providers for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from builders.cli import fake_manifest

from hivemind.cli.compose.virtual_cell_providers import (
    cell_providers,
    cell_slots,
    provider_api_keys,
)
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.llm import ProviderSpec


def _with_local_provider(tmp_path: Path, **spec_overrides: object) -> HiveManifest:
    """Load `fake_manifest`'s own manifest, with an extra `[llm.providers.local]` entry."""
    manifest = load_manifest(fake_manifest(tmp_path))
    fields: dict[str, object] = {"kind": "openai_compat", "base_url": "http://127.0.0.1:1234/v1"}
    fields.update(spec_overrides)
    providers = dict(manifest.llm.providers)
    providers["local"] = ProviderSpec(**fields)
    return manifest.model_copy(
        update={"llm": manifest.llm.model_copy(update={"providers": providers})}
    )


def test_cell_providers_rewrites_a_loopback_base_url_for_the_given_gateway_host(
    tmp_path: Path,
) -> None:
    manifest = _with_local_provider(tmp_path)

    providers = cell_providers(manifest, "host.docker.internal")

    local = next(p for p in providers if p.name == "local")
    assert local.base_url == "http://host.docker.internal:1234/v1"
    assert local.kind == "openai_compat"
    assert local.api_key_env == "HIVEMIND_LOCAL_API_KEY"


def test_cell_providers_leaves_base_url_unchanged_when_gateway_host_is_none(
    tmp_path: Path,
) -> None:
    """`gateway_host=None` is the "fake" backend's own case: same process, no rewrite needed."""
    manifest = _with_local_provider(tmp_path)

    providers = cell_providers(manifest, None)

    local = next(p for p in providers if p.name == "local")
    assert local.base_url == "http://127.0.0.1:1234/v1"


def test_cell_providers_leaves_a_non_loopback_base_url_unchanged(tmp_path: Path) -> None:
    manifest = _with_local_provider(tmp_path, base_url="https://api.example.com/v1")

    providers = cell_providers(manifest, "host.docker.internal")

    local = next(p for p in providers if p.name == "local")
    assert local.base_url == "https://api.example.com/v1"


def test_cell_providers_uses_the_providers_own_explicit_api_key_env(tmp_path: Path) -> None:
    manifest = _with_local_provider(tmp_path, api_key_env="HIVEMIND_CUSTOM_KEY")

    providers = cell_providers(manifest, None)

    local = next(p for p in providers if p.name == "local")
    assert local.api_key_env == "HIVEMIND_CUSTOM_KEY"


def test_cell_slots_mirrors_every_llm_slots_row(tmp_path: Path) -> None:
    manifest = load_manifest(fake_manifest(tmp_path))

    slots = cell_slots(manifest)

    assert {s.key for s in slots} == set(manifest.llm.slots.keys())
    warden = next(s for s in slots if s.key == "warden")
    assert warden.provider == manifest.llm.slots["warden"].provider
    assert warden.model == manifest.llm.slots["warden"].model
    assert warden.effort == manifest.llm.slots["warden"].effort.value


def test_provider_api_keys_resolves_only_providers_with_a_key_set(tmp_path: Path) -> None:
    manifest = _with_local_provider(tmp_path)

    keys = provider_api_keys(manifest, {"HIVEMIND_LOCAL_API_KEY": "sk-abc"})

    assert keys["HIVEMIND_LOCAL_API_KEY"].get_secret_value() == "sk-abc"


def test_provider_api_keys_skips_a_provider_with_no_key_set(tmp_path: Path) -> None:
    manifest = _with_local_provider(tmp_path)

    keys = provider_api_keys(manifest, {})

    assert "HIVEMIND_LOCAL_API_KEY" not in keys
