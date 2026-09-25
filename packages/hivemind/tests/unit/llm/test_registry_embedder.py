"""Tests for hivemind.llm.registry: ProviderRegistry.embedder, the EMBEDDER slot's resolution.

Split by feature (codingrules 14.2/5.1) from `test_registry.py`, which covers provider
construction, caching, offline enforcement and the chat slots. Here: resolution per provider kind,
EmbeddingUnsupportedError, the same-model fallback rule (ADR-0036), an in-process kind accepted
offline, and the cache per (provider name, model).

Fits into the Hive:
    Mirrors src/hivemind/llm/registry.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.registry for the module under test.
    - tests/unit/llm/test_registry.py for the rest of the registry's tests.
"""

from __future__ import annotations

import pytest
from builders.llm import (
    make_binding,
    make_provider_config,
    make_registry_deps,
)
from pydantic import SecretStr

from hivemind.forage.slots import ModelSlot
from hivemind.llm.embedding import EmbeddingProvider, EmbeddingRequest, FakeEmbedding
from hivemind.llm.errors import (
    EmbeddingUnsupportedError,
    OfflineViolationError,
)
from hivemind.llm.registry import (
    ProviderConfig,
    ProviderRegistry,
    default_embedding_factories,
)
from waggle.clock import Clock

# ──────────────────────────────────────────────────────────────────────────────
# embedder(): resolution per kind, EmbeddingUnsupportedError, same/different-model
# fallback, offline acceptance of an in-process kind, caching per (name, model)
# ──────────────────────────────────────────────────────────────────────────────


def test_embedder_resolves_the_fake_kind() -> None:
    bindings = [make_binding(key="embedder", provider="hosted", model="test-embed")]
    providers = {"hosted": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    bound = registry.embedder()

    assert bound.slot is ModelSlot.EMBEDDER
    assert bound.binding == "embedder"
    assert bound.model == "test-embed"
    assert isinstance(bound.provider, FakeEmbedding)


async def test_embedder_fake_reports_the_bindings_own_model_id() -> None:
    # The Honey Store tags and filters vectors by one model id (ADR-0036); a fake reporting its own
    # constant instead would make every stored vector look stale to a re-embed forever.
    bindings = [make_binding(key="embedder", provider="hosted", model="test-embed")]
    providers = {"hosted": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())
    bound = registry.embedder()

    response = await bound.provider.embed(EmbeddingRequest(texts=("hello",)))

    assert response.model == bound.model == "test-embed"


def test_embedder_resolves_the_openai_compat_kind() -> None:
    bindings = [make_binding(key="embedder", provider="hosted", model="test-embed")]
    providers = {
        "hosted": make_provider_config(kind="openai_compat", base_url="http://127.0.0.1:9/v1")
    }
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    bound = registry.embedder()

    assert bound.provider.name == "hosted"


def test_embedder_resolves_the_sentence_transformers_kind() -> None:
    bindings = [make_binding(key="embedder", provider="local", model="test-embed")]
    providers = {"local": make_provider_config(kind="sentence_transformers", base_url="")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    bound = registry.embedder()

    assert bound.provider.name == "local"
    assert bound.model == "test-embed"


def test_embedder_raises_embedding_unsupported_for_anthropic() -> None:
    bindings = [make_binding(key="embedder", provider="claude", model="test-embed")]
    providers = {"claude": make_provider_config(kind="anthropic", base_url="")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    with pytest.raises(EmbeddingUnsupportedError):
        registry.embedder()


def test_embedder_keeps_a_same_model_fallback() -> None:
    bindings = [
        make_binding(key="embedder", provider="a", model="shared-model", fallback="local_embedder"),
        make_binding(key="local_embedder", provider="b", model="shared-model"),
    ]
    providers = {"a": make_provider_config(kind="fake"), "b": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    bound = registry.embedder()

    assert bound.fallback is not None
    assert bound.fallback.binding == "local_embedder"
    assert bound.fallback.model == "shared-model"


def test_embedder_cuts_a_different_model_fallback() -> None:
    bindings = [
        make_binding(key="embedder", provider="a", model="model-a", fallback="local_embedder"),
        make_binding(key="local_embedder", provider="b", model="model-b"),
    ]
    providers = {"a": make_provider_config(kind="fake"), "b": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    bound = registry.embedder()

    assert bound.fallback is None  # ADR-0036: a different model id is never comparable.


def test_embedder_cuts_a_fallback_with_no_embedding_factory() -> None:
    bindings = [
        make_binding(key="embedder", provider="a", model="shared-model", fallback="local_embedder"),
        make_binding(key="local_embedder", provider="claude", model="shared-model"),
    ]
    providers = {
        "a": make_provider_config(kind="fake"),
        "claude": make_provider_config(kind="anthropic", base_url=""),
    }
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    bound = registry.embedder()

    assert bound.fallback is None  # Unlike the primary, a bad fallback link is cut, not raised.


def test_embedder_accepts_the_in_process_kind_while_offline() -> None:
    bindings = [make_binding(key="embedder", provider="local", model="test-embed")]
    providers = {"local": make_provider_config(kind="sentence_transformers", base_url="")}
    registry = ProviderRegistry(providers, bindings, offline=True, deps=make_registry_deps())

    bound = registry.embedder()

    assert bound.provider.name == "local"


def test_embedder_still_refuses_a_remote_kind_while_offline() -> None:
    bindings = [make_binding(key="embedder", provider="hosted", model="test-embed")]
    providers = {
        "hosted": make_provider_config(kind="openai_compat", base_url="http://example.com/v1")
    }
    registry = ProviderRegistry(providers, bindings, offline=True, deps=make_registry_deps())

    with pytest.raises(OfflineViolationError):
        registry.embedder()


def test_embedder_caches_per_provider_name_and_model() -> None:
    calls: list[tuple[str, str]] = []

    def _counting_factory(
        name: str, config: ProviderConfig, model: str, api_key: SecretStr | None, clock: Clock
    ) -> EmbeddingProvider:
        calls.append((name, model))
        return FakeEmbedding(name=name, clock=clock)

    deps = make_registry_deps(
        embedding_factories={**default_embedding_factories(), "fake": _counting_factory}
    )
    bindings = [make_binding(key="embedder", provider="hosted", model="test-embed")]
    providers = {"hosted": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=deps)

    first = registry.embedder()
    second = registry.embedder()

    assert first.provider is second.provider
    assert calls == [("hosted", "test-embed")]  # Built once, not once per embedder() call.
