"""Tests for hivemind.llm.registry: ProviderRegistry, RegistryDeps, ProviderConfig.

Fits into the Hive:
    Mirrors src/hivemind/llm/registry.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.registry for the module under test.
    - docs/manifests/ for the example manifests the manifest-backed tests load.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from builders.llm import (
    bindings_from_manifest,
    make_binding,
    make_provider_config,
    make_registry_deps,
    provider_configs_from_manifest,
)
from pydantic import SecretStr

from hivemind.forage.slots import ModelSlot
from hivemind.llm.capabilities import HealthState, ProviderCapabilities
from hivemind.llm.errors import OfflineViolationError, UnknownProviderError
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.provider import LLMProvider
from hivemind.llm.registry import (
    PENDING_KINDS,
    MissingDefaultModelError,
    ProviderConfig,
    ProviderKind,
    ProviderRegistry,
    apply_overrides,
    default_factories,
)
from hivemind.manifest import ProviderKind as ManifestProviderKind
from hivemind.manifest import load_manifest
from waggle.clock import Clock

# Same depth as tests/unit/manifest/test_loader.py's own _REPO_ROOT: five parents up from
# packages/hivemind/tests/unit/llm/test_registry.py.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MANIFESTS_DIR = _REPO_ROOT / "docs" / "manifests"


def _stub_anthropic_factory(
    name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
) -> LLMProvider:
    """A stand-in ANTHROPIC factory: the real adapter (roadmap step 3.6) is not wired up yet."""
    return FakeLLMProvider(name=name)


# ──────────────────────────────────────────────────────────────────────────────
# ProviderKind mirrors the manifest's own Literal; default_factories() completeness
# ──────────────────────────────────────────────────────────────────────────────


def test_provider_kind_mirrors_the_manifest_provider_kind() -> None:
    assert set(get_args(ProviderKind)) == set(get_args(ManifestProviderKind))


def test_default_factories_covers_every_kind_except_pending() -> None:
    factories = default_factories()

    for kind in get_args(ProviderKind):
        if kind in PENDING_KINDS:
            assert kind not in factories
        else:
            assert kind in factories


# ──────────────────────────────────────────────────────────────────────────────
# apply_overrides()
# ──────────────────────────────────────────────────────────────────────────────


def test_apply_overrides_replaces_only_the_named_fields() -> None:
    base = ProviderCapabilities.full()

    result = apply_overrides(base, {"vision": False, "context_window": 4096})

    assert result.vision is False
    assert result.context_window == 4096
    assert result.native_tool_calls is True  # Unset field: kept from base.


def test_apply_overrides_returns_base_unchanged_when_overrides_is_empty() -> None:
    base = ProviderCapabilities.full()

    assert apply_overrides(base, {}) is base


# ──────────────────────────────────────────────────────────────────────────────
# names() and provider(): lazy construction, caching, unknown name / kind
# ──────────────────────────────────────────────────────────────────────────────


def test_names_returns_every_configured_provider() -> None:
    providers = {"a": make_provider_config(), "b": make_provider_config()}
    registry = ProviderRegistry(providers, [], offline=False, deps=make_registry_deps())

    assert set(registry.names()) == {"a", "b"}


def test_provider_constructs_once_and_caches_the_instance() -> None:
    calls: list[str] = []

    def _counting_factory(
        name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
    ) -> LLMProvider:
        calls.append(name)
        return FakeLLMProvider(name=name)

    deps = make_registry_deps(factories={"fake": _counting_factory})
    providers = {"hosted": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, [], offline=False, deps=deps)

    first = registry.provider("hosted")
    second = registry.provider("hosted")

    assert first is second
    assert calls == ["hosted"]


def test_provider_raises_unknown_provider_error_for_an_unconfigured_name() -> None:
    registry = ProviderRegistry({}, [], offline=False, deps=make_registry_deps())

    with pytest.raises(UnknownProviderError):
        registry.provider("nobody")


def test_provider_raises_unknown_provider_error_for_a_kind_with_no_factory() -> None:
    # "anthropic" is in PENDING_KINDS; default_factories() has no entry for it yet.
    providers = {"hosted": make_provider_config(kind="anthropic", base_url="")}
    registry = ProviderRegistry(
        providers, [], offline=False, deps=make_registry_deps(factories=default_factories())
    )

    with pytest.raises(UnknownProviderError):
        registry.provider("hosted")


# ──────────────────────────────────────────────────────────────────────────────
# API key resolution from the environment
# ──────────────────────────────────────────────────────────────────────────────


def test_provider_resolves_api_key_from_the_derived_environment_variable() -> None:
    captured: list[SecretStr | None] = []

    def _capturing_factory(
        name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
    ) -> LLMProvider:
        captured.append(api_key)
        return FakeLLMProvider(name=name)

    deps = make_registry_deps(
        factories={"fake": _capturing_factory}, environ={"HIVEMIND_HOSTED_API_KEY": "secret-1"}
    )
    providers = {"hosted": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, [], offline=False, deps=deps)

    registry.provider("hosted")

    assert captured[0] is not None
    assert captured[0].get_secret_value() == "secret-1"


def test_provider_resolves_api_key_from_an_explicit_api_key_env_override() -> None:
    captured: list[SecretStr | None] = []

    def _capturing_factory(
        name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
    ) -> LLMProvider:
        captured.append(api_key)
        return FakeLLMProvider(name=name)

    deps = make_registry_deps(
        factories={"fake": _capturing_factory}, environ={"CUSTOM_KEY": "secret-2"}
    )
    providers = {"hosted": make_provider_config(kind="fake", api_key_env="CUSTOM_KEY")}
    registry = ProviderRegistry(providers, [], offline=False, deps=deps)

    registry.provider("hosted")

    assert captured[0] is not None
    assert captured[0].get_secret_value() == "secret-2"


def test_provider_passes_none_api_key_when_no_matching_variable_is_set() -> None:
    captured: list[SecretStr | None] = []

    def _capturing_factory(
        name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
    ) -> LLMProvider:
        captured.append(api_key)
        return FakeLLMProvider(name=name)

    deps = make_registry_deps(factories={"fake": _capturing_factory}, environ={})
    providers = {"hosted": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, [], offline=False, deps=deps)

    registry.provider("hosted")

    assert captured[0] is None


# ──────────────────────────────────────────────────────────────────────────────
# default_factories(): capability overrides for "fake" and "openai_compat"
# ──────────────────────────────────────────────────────────────────────────────


def test_default_fake_factory_applies_capability_overrides() -> None:
    providers = {
        "hosted": make_provider_config(kind="fake", capability_overrides={"vision": False})
    }
    registry = ProviderRegistry(providers, [], offline=False, deps=make_registry_deps())

    provider = registry.provider("hosted")

    assert provider.capabilities.vision is False
    assert provider.capabilities.native_tool_calls is True  # Unset: kept from .full().


def test_default_openai_compat_factory_applies_capability_overrides() -> None:
    providers = {
        "local": make_provider_config(
            kind="openai_compat",
            base_url="http://127.0.0.1:8080/v1",
            default_model="local-small",
            capability_overrides={"native_tool_calls": False, "context_window": 8192},
        )
    }
    registry = ProviderRegistry(providers, [], offline=False, deps=make_registry_deps())

    provider = registry.provider("local")

    assert provider.name == "local"
    assert provider.capabilities.native_tool_calls is False
    assert provider.capabilities.context_window == 8192


def test_default_openai_compat_factory_raises_without_a_default_model() -> None:
    providers = {
        "local": make_provider_config(kind="openai_compat", base_url="http://127.0.0.1:8080/v1")
    }
    registry = ProviderRegistry(providers, [], offline=False, deps=make_registry_deps())

    with pytest.raises(MissingDefaultModelError, match="default_model"):
        registry.provider("local")


# ──────────────────────────────────────────────────────────────────────────────
# Offline enforcement: non-loopback base_url, and ANTHROPIC with no base_url at all
# ──────────────────────────────────────────────────────────────────────────────


def test_provider_raises_offline_violation_for_a_non_loopback_base_url() -> None:
    providers = {
        "hosted": make_provider_config(
            kind="openai_compat", base_url="http://example.com/v1", default_model="local-small"
        )
    }
    registry = ProviderRegistry(providers, [], offline=True, deps=make_registry_deps())

    with pytest.raises(OfflineViolationError):
        registry.provider("hosted")


def test_provider_raises_offline_violation_before_a_stub_anthropic_factory_ever_runs() -> None:
    calls: list[str] = []

    def _counting_stub(
        name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
    ) -> LLMProvider:
        calls.append(name)
        return FakeLLMProvider(name=name)

    deps = make_registry_deps(factories={**default_factories(), "anthropic": _counting_stub})
    providers = {"hosted": make_provider_config(kind="anthropic", base_url="")}
    registry = ProviderRegistry(providers, [], offline=True, deps=deps)

    with pytest.raises(OfflineViolationError):
        registry.provider("hosted")
    assert calls == []  # _check_offline ran before the factory did.


def test_provider_allows_a_loopback_base_url_while_offline() -> None:
    providers = {
        "local": make_provider_config(
            kind="openai_compat", base_url="http://127.0.0.1:8080/v1", default_model="local-small"
        )
    }
    registry = ProviderRegistry(providers, [], offline=True, deps=make_registry_deps())

    provider = registry.provider("local")

    assert provider.name == "local"


# ──────────────────────────────────────────────────────────────────────────────
# bound() / bound_for_key()
# ──────────────────────────────────────────────────────────────────────────────


def test_bound_resolves_via_the_registrys_own_provider_lookup() -> None:
    bindings = [make_binding(key="worker", provider="hosted", model="test-model")]
    providers = {"hosted": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    bound = registry.bound(ModelSlot.WORKER)

    assert bound.provider.name == "hosted"
    assert bound.model == "test-model"


def test_bound_for_key_resolves_a_named_binding_for_the_given_slot() -> None:
    bindings = [make_binding(key="local_worker", provider="hosted", model="local-small")]
    providers = {"hosted": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    bound = registry.bound_for_key("local_worker", ModelSlot.WORKER)

    assert bound.slot is ModelSlot.WORKER
    assert bound.binding == "local_worker"


# ──────────────────────────────────────────────────────────────────────────────
# health()
# ──────────────────────────────────────────────────────────────────────────────


async def test_health_aggregates_only_already_constructed_providers() -> None:
    providers = {"a": make_provider_config(kind="fake"), "b": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, [], offline=False, deps=make_registry_deps())
    registry.provider("a")

    health = await registry.health()

    assert set(health) == {"a"}
    assert health["a"].state is HealthState.HEALTHY


# ──────────────────────────────────────────────────────────────────────────────
# Built from the shipped example manifests
# ──────────────────────────────────────────────────────────────────────────────


def test_registry_built_from_minimal_manifest_resolves_the_queen_slot() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")
    providers = provider_configs_from_manifest(manifest)
    bindings = bindings_from_manifest(manifest)
    factories = {**default_factories(), "anthropic": _stub_anthropic_factory}
    deps = make_registry_deps(factories=factories)
    registry = ProviderRegistry(providers, bindings.values(), manifest.llm.offline, deps)

    bound = registry.bound(ModelSlot.QUEEN)

    assert bound.provider.name == "anthropic"
    assert bound.model == bindings["queen"].model


def test_registry_built_from_local_manifest_is_offline_and_uses_the_local_provider() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "local.toml")
    providers = provider_configs_from_manifest(manifest)
    bindings = bindings_from_manifest(manifest)
    registry = ProviderRegistry(
        providers, bindings.values(), manifest.llm.offline, make_registry_deps()
    )

    bound = registry.bound(ModelSlot.WORKER)

    assert bound.provider.name == "local"
    assert bound.model == bindings["worker"].model


def test_registry_built_from_full_manifest_constructs_the_fallback_providers_own_provider() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "full.toml")
    providers = provider_configs_from_manifest(manifest)
    bindings = bindings_from_manifest(manifest)
    factories = {**default_factories(), "anthropic": _stub_anthropic_factory}
    deps = make_registry_deps(factories=factories)
    registry = ProviderRegistry(providers, bindings.values(), manifest.llm.offline, deps)

    bound = registry.bound(ModelSlot.WORKER)

    assert bound.provider.name == "anthropic"
    assert bound.fallback is not None
    assert bound.fallback.provider.name == "local"
