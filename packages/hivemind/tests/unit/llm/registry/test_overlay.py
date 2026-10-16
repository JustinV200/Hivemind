"""Tests for hivemind.llm.registry.overlay: OverlayingProvider and the registry's wrapping.

Fits into the Hive:
    Mirrors src/hivemind/llm/registry/overlay.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.registry.overlay for the module under test.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest
from builders.llm import make_provider_config, make_registry_deps, make_request, text_response

from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.prompts import OverlayNotFoundError, PromptName, load_overlay, load_prompt
from hivemind.llm.registry import OverlayingProvider, ProviderRegistry


def _wrapped() -> tuple[OverlayingProvider, FakeLLMProvider]:
    """Return a wrapper over a fake with the shipped small_local overlay."""
    inner = FakeLLMProvider(name="local-small")
    return OverlayingProvider(inner, load_overlay("small_local")), inner


def _registry(**config: object) -> ProviderRegistry:
    """Build a registry with one fake provider named 'local-small'."""
    providers = {"local-small": make_provider_config(**config)}
    return ProviderRegistry(providers, (), False, make_registry_deps())


# ──────────────────────────────────────────────────────────────────────────────
# OverlayingProvider
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_rewrites_the_system_prompt_before_the_inner_provider_sees_it() -> None:
    provider, inner = _wrapped()
    inner.script(text_response("ok"))

    response = await provider.complete(make_request(system="Plain system text."))

    assert response.text == "ok"
    seen = inner.calls[0].system
    assert seen is not None
    assert seen.startswith("Plain system text.")
    assert seen.endswith(load_overlay("small_local").system_suffix)


async def test_stream_rewrites_the_system_prompt_too() -> None:
    provider, inner = _wrapped()
    inner.script(text_response("streamed"))

    chunks = [chunk async for chunk in provider.stream(make_request(system="Plain."))]

    assert "".join(chunk.text or "" for chunk in chunks) == "streamed"
    assert inner.calls[0].system != "Plain."


async def test_a_keyed_overlay_applies_only_to_the_matching_prompt() -> None:
    provider, inner = _wrapped()
    inner.script(text_response("a"), text_response("b"))
    drone = load_prompt(PromptName.DRONE_SYSTEM)
    queen = load_prompt(PromptName.QUEEN_SYSTEM)
    keyed = load_overlay("small_local").prompt_suffixes[PromptName.DRONE_SYSTEM]

    await provider.complete(make_request(system=drone))
    await provider.complete(make_request(system=queen))

    drone_seen, queen_seen = (call.system or "" for call in inner.calls)
    assert keyed in drone_seen
    assert keyed not in queen_seen


async def test_identity_capabilities_health_and_close_are_forwarded() -> None:
    provider, inner = _wrapped()

    assert provider.name == "local-small"
    assert provider.capabilities == ProviderCapabilities.full()
    assert (await provider.health()).state == (await inner.health()).state
    await provider.aclose()
    await provider.aclose()  # Idempotent, like the wrapped provider's own close.


async def test_count_tokens_counts_the_overlaid_request() -> None:
    provider, inner = _wrapped()
    request = make_request(system="Plain.")

    wrapped_count = await provider.count_tokens(request)
    plain_count = await inner.count_tokens(request)

    assert wrapped_count is not None
    assert plain_count is not None
    assert wrapped_count > plain_count


@runtime_checkable
class _ModelListing(Protocol):
    """The structural probe `hive llm models` makes for the generic `/models` listing."""

    async def list_models(self) -> tuple[str, ...]: ...


class _ListingFake(FakeLLMProvider):
    """A fake that can also list its models, like `OpenAICompatProvider`."""

    async def list_models(self) -> tuple[str, ...]:
        return ("local-small", "local-big")


async def test_an_optional_capability_method_of_the_inner_provider_is_forwarded() -> None:
    provider = OverlayingProvider(_ListingFake(name="local-small"), load_overlay("small_local"))

    assert isinstance(provider, _ModelListing)
    assert await provider.list_models() == ("local-small", "local-big")


def test_the_wrapper_never_claims_a_capability_the_inner_provider_lacks() -> None:
    provider, _ = _wrapped()

    assert not isinstance(provider, _ModelListing)
    assert not hasattr(provider, "list_models")


# ──────────────────────────────────────────────────────────────────────────────
# ProviderRegistry wiring
# ──────────────────────────────────────────────────────────────────────────────


def test_registry_wraps_a_provider_whose_row_names_an_overlay() -> None:
    provider = _registry(prompt_overlay="small_local").provider("local-small")

    assert isinstance(provider, OverlayingProvider)
    assert provider.overlay.name == "small_local"


def test_registry_leaves_a_provider_without_an_overlay_unwrapped() -> None:
    provider = _registry().provider("local-small")

    assert isinstance(provider, FakeLLMProvider)


def test_registry_caches_the_wrapped_provider() -> None:
    registry = _registry(prompt_overlay="small_local")

    assert registry.provider("local-small") is registry.provider("local-small")


def test_registry_fails_at_construction_for_an_unknown_overlay_name() -> None:
    registry = _registry(prompt_overlay="no_such_overlay")

    with pytest.raises(OverlayNotFoundError, match="no_such_overlay"):
        registry.provider("local-small")
