"""Define the chat door's factory table: how each kind string becomes a live LLMProvider.

`default_factories()` is the built-in `kind -> ProviderFactory` table for the three chat adapters
(`fake`, `openai_compat`, `anthropic`); a transcription-only kind (`whisper_local`) and an
embedding-only kind (`sentence_transformers`) have no entry, so `ProviderRegistry.provider` refuses
them by name. `apply_overrides` is the one place a manifest's per-provider capability override
replaces a base `ProviderCapabilities` field.

Fits into the Hive:
    Layer 1 (foundational services). Read by `hivemind.llm.registry.provider_registry` (the
    default `RegistryDeps.factories`) and by the composition root; calls into
    `hivemind.llm.capabilities`, `hivemind.llm.fake`, `hivemind.llm.providers.anthropic`,
    `hivemind.llm.providers.openai_compat` and this package's `config`.

Key invariants:
    - A factory never checks offline mode or reads the environment itself: the registry has done
      both (`_check_offline`, `_resolve_api_key`) before a factory ever runs.
    - An openai_compat chat provider never claims audio input unless the manifest turns it on: an
      OpenAI-compatible server that takes audio parts in a chat call is rare (roadmap step 6.5).

See Also:
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - hivemind.llm.registry.transcription and hivemind.llm.registry.embedding for the other doors.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import SecretStr

from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.provider import LLMProvider
from hivemind.llm.providers.anthropic import AnthropicConfig, AnthropicProvider
from hivemind.llm.providers.openai_compat import OpenAICompatConfig, OpenAICompatProvider
from hivemind.llm.registry.config import MissingDefaultModelError, ProviderConfig, ProviderKind
from waggle.clock import Clock

__all__ = ["ProviderFactory", "apply_overrides", "default_factories"]


class ProviderFactory(Protocol):
    """Build a live LLMProvider from one provider's config and its already-resolved API key."""

    def __call__(
        self, name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
    ) -> LLMProvider:
        """Return a new provider instance for `name`.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: That provider's config.
            api_key: The resolved secret, or None when this provider needs none.
            clock: Passed through to the provider's own health()/count_tokens readings.
        """
        ...


def apply_overrides(
    base: ProviderCapabilities, overrides: Mapping[str, bool | int]
) -> ProviderCapabilities:
    """Return `base` with every field named in `overrides` replaced.

    Args:
        base: The adapter's own default capabilities (usually `ProviderCapabilities.full()`).
        overrides: Field name to value, as `hivemind.manifest.schema.llm.CapabilityOverrides.
            as_overrides()` produces: only the fields a manifest author actually set.

    Returns:
        `base` unchanged when `overrides` is empty, otherwise a copy with those fields replaced.
    """
    return base.model_copy(update=dict(overrides)) if overrides else base


def default_factories() -> Mapping[ProviderKind, ProviderFactory]:
    """Return the built-in `kind -> ProviderFactory` table for every chat adapter.

    Covers every `ProviderKind` except `PENDING_KINDS` (currently none), the transcription-only
    `whisper_local` (only in `default_transcription_factories()`) and `EMBEDDING_ONLY_KINDS`
    (`sentence_transformers`, only in `default_embedding_factories()`).
    """
    return {
        "fake": _build_fake,
        "openai_compat": _build_openai_compat,
        "anthropic": _build_anthropic,
    }


def _build_fake(
    name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
) -> LLMProvider:
    """Build a FakeLLMProvider from `config`; `api_key` is accepted (Protocol shape) and unused."""
    capabilities = apply_overrides(ProviderCapabilities.full(), config.capability_overrides)
    return FakeLLMProvider(name=name, capabilities=capabilities, clock=clock)


def _build_anthropic(
    name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
) -> LLMProvider:
    """Build an AnthropicProvider from `config` via its own SDK-client-building classmethod.

    An empty `base_url` means the SDK's default hosted endpoint (None to the adapter);
    `_check_offline` has already refused that case when the Hive is offline, so this never
    opens a client an air-gapped Hive would not want.
    """
    base = AnthropicConfig(
        api_key=api_key, base_url=config.base_url or None, timeout_s=config.timeout_s
    )
    capabilities = apply_overrides(base.capabilities, config.capability_overrides)
    adapter_config = base.model_copy(update={"capabilities": capabilities})
    return AnthropicProvider.from_config(name, adapter_config, clock)


def _build_openai_compat(
    name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
) -> LLMProvider:
    """Build an OpenAICompatProvider from `config`, via its own httpx-client-building classmethod.

    Raises:
        MissingDefaultModelError: `config.default_model` is None; an openai_compat provider's
            wire body needs a model id and has no manifest-level default (see
            hivemind.llm.providers.openai_compat's README, "Why model is on the config").
    """
    if config.default_model is None:
        raise MissingDefaultModelError(name)
    # Everything but audio: an OpenAI-compatible server that takes audio parts in a chat call is
    # rare, so audio stays off unless the manifest's capability override turns it on.
    base = ProviderCapabilities.full().model_copy(update={"audio": False})
    capabilities = apply_overrides(base, config.capability_overrides)
    oc_config = OpenAICompatConfig(
        base_url=config.base_url,
        model=config.default_model,
        api_key=api_key,
        timeout_s=config.timeout_s,
        capabilities=capabilities,
    )
    return OpenAICompatProvider.create(name, oc_config, clock)
