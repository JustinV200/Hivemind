"""Define the embedding door: resolve the EMBEDDER slot to a same-model BoundEmbedder chain.

Roadmap step 7.1 (ADR-0036) adds the embedding half beside the chat and transcription doors:
`EmbedderDoor.bind(slot, bindings)` is `ProviderRegistry.bound`'s counterpart for the non-chat
`hivemind.llm.embedding.EmbeddingProvider` door. It differs from `bound` in three ways ADR-0036
requires. First, an embedding provider is built per `(provider name, model)` pair, not per
provider name alone (`EmbeddingRequest` carries no model id -- see that module's own docstring),
so this door keeps its own, separately keyed cache. Second, a `[llm.slots]` kind with no
embedding factory at all (`anthropic` today) fails the *primary* binding outright
(`EmbeddingUnsupportedError`), because there is then nothing to serve the slot; the same problem
on a *fallback* link just ends the chain there, the same graceful-degrade spirit the chat side's
chain walk already applies to a missing key. Third, a fallback link is only ever included when it
names the *same model id* as the link before it: two different embedding models' vectors are not
comparable, so the door never spills across a model change the way `bound`'s fallback chain
freely spills across providers for chat.

Fits into the Hive:
    Layer 1 (foundational services). `EmbedderDoor` is owned by
    `hivemind.llm.registry.provider_registry.ProviderRegistry`, which serves `embedder()` through
    it. Calls into `hivemind.forage.map`, `hivemind.forage.slots`, `hivemind.llm.embedding`,
    `hivemind.llm.errors`, `hivemind.llm.providers.openai_compat`,
    `hivemind.llm.providers.sentence_transformers` and this package's `config`.

Key invariants:
    - `EmbedderDoor` constructs at most one provider per (provider name, model) pair.
    - Offline mode is checked before a factory ever runs; an `IN_PROCESS_KINDS` member
      (`sentence_transformers`) is provably local by construction.
    - A fallback link is built only when it serves the same model id, names a known provider whose
      kind can embed, and passes the offline check; otherwise the chain silently ends there.

See Also:
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the rules above.
    - hivemind.llm.embedding for BoundEmbedder, walk_embedder_chain and embedder_cost.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from pydantic import SecretStr

from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import ModelSlot
from hivemind.llm.embedding import (
    BoundEmbedder,
    EmbeddingProvider,
    FakeEmbedding,
    embedder_cost,
    walk_embedder_chain,
)
from hivemind.llm.errors import (
    EmbeddingUnsupportedError,
    OfflineViolationError,
    UnknownProviderError,
)
from hivemind.llm.providers.openai_compat import (
    OpenAICompatEmbedding,
    OpenAICompatEmbeddingConfig,
)
from hivemind.llm.providers.sentence_transformers import (
    SentenceTransformersConfig,
    SentenceTransformersEmbedding,
)
from hivemind.llm.registry.config import (
    ProviderConfig,
    ProviderKind,
    _check_offline,
    _DoorContext,
    _resolve_api_key,
)
from waggle.clock import Clock

__all__ = ["EmbedderDoor", "EmbeddingFactory", "default_embedding_factories"]


class EmbeddingFactory(Protocol):
    """Build a live EmbeddingProvider for one specific `(provider name, model)` pair.

    Unlike a chat `ProviderFactory`, this also takes `model`: an embedding adapter is built per
    model (`hivemind.llm.embedding.models`'s own docstring explains why a request carries no model
    id of its own), where a chat provider is built once per provider name and takes a model per
    call.
    """

    def __call__(
        self,
        name: str,
        config: ProviderConfig,
        model: str,
        api_key: SecretStr | None,
        clock: Clock,
    ) -> EmbeddingProvider:
        """Return a new embedding provider instance for `(name, model)`.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: That provider's config.
            model: The model id this instance is built for.
            api_key: The resolved secret, or None when this provider needs none.
            clock: Passed through to the provider's own health() readings.
        """
        ...


class EmbedderDoor:
    """The registry's embedding half: one cached embedder per (provider name, model)."""

    def __init__(
        self,
        context: _DoorContext,
        factories: Mapping[ProviderKind, EmbeddingFactory],
        forage_map: ForageMap | None,
    ) -> None:
        """Remember the rows, factories and prices; build nothing until a binding asks.

        Args:
            context: The owning registry's provider rows, offline flag, environment and clock.
            factories: kind -> how to build an embedder of it.
            forage_map: Prices each link's input tokens; None leaves them unpriced.
        """
        self._context = context
        self._factories = factories
        self._map = forage_map
        self._built: dict[tuple[str, str], EmbeddingProvider] = {}

    def bind(self, slot: ModelSlot, bindings: Iterable[SlotBinding]) -> BoundEmbedder:
        """Resolve `slot`'s own `[llm.slots]` chain; see `ProviderRegistry.embedder`.

        Raises:
            UnresolvableSlotError: `bindings` has no row for `slot.manifest_key`, or it cycles.
            UnknownProviderError: The primary binding names a provider no row declares.
            EmbeddingUnsupportedError: The primary binding's kind has no embedding factory.
            OfflineViolationError: `[llm] offline = true` and the primary binding's provider is
                not provably local.
        """
        by_key = {binding.key: binding for binding in bindings}
        chain = walk_embedder_chain(slot.manifest_key, by_key)
        primary_config = self._context.providers.get(chain[0].provider)
        if primary_config is None:
            raise UnknownProviderError(chain[0].provider)
        # Nothing else could serve the slot, so an unsupported primary kind is a hard failure.
        if primary_config.kind not in self._factories:
            raise EmbeddingUnsupportedError(chain[0].provider, primary_config.kind)
        return self._bind(slot, chain, 0)

    def release(self) -> list[EmbeddingProvider]:
        """Forget every embedder built so far and return them, for the owning registry to close."""
        built = list(self._built.values())
        self._built.clear()
        return built

    def _bind(self, slot: ModelSlot, chain: list[SlotBinding], index: int) -> BoundEmbedder:
        """Build `chain[index]` into a BoundEmbedder, recursing for its own fallback.

        `bind` has already proved `chain[0]`'s provider and kind are buildable before the first
        call; every later call builds a link `_next_link` already proved buildable the same way,
        so the provider row lookup below never misses.
        """
        binding = chain[index]
        config = self._context.providers[binding.provider]
        return BoundEmbedder(
            slot=slot,
            binding=binding.key,
            provider=self._provider(binding.provider, binding.model, config),
            model=binding.model,
            cost_per_million_input_usd=embedder_cost(binding, self._map),
            fallback=self._next_link(slot, chain, index),
        )

    def _next_link(
        self, slot: ModelSlot, chain: list[SlotBinding], index: int
    ) -> BoundEmbedder | None:
        """Return `chain[index + 1]` bound, or None when the chain ends or ADR-0036 cuts it here.

        Cutting is silent, never a raised error: a fallback is a nicety the primary binding does
        not depend on, so a badly configured or offline fallback should not break a slot whose
        primary binding already resolved cleanly (contrast `bind`'s own hard failures on the
        primary link, where there is nothing else to serve the slot at all).
        """
        if index + 1 >= len(chain):
            return None
        next_binding = chain[index + 1]
        if next_binding.model != chain[index].model:
            return None  # ADR-0036: a fallback is followed only when it serves the same model id.
        next_config = self._context.providers.get(next_binding.provider)
        if next_config is None or next_config.kind not in self._factories:
            return None  # Unknown provider, or a kind that cannot embed: nothing to build here.
        try:
            _check_offline(next_binding.provider, next_config, self._context.offline)
        except OfflineViolationError:
            return None  # Same "cannot build, so cut" treatment as an unsupported kind above.
        return self._bind(slot, chain, index + 1)

    def _provider(self, name: str, model: str, config: ProviderConfig) -> EmbeddingProvider:
        """Return the live embedding provider for `(name, model)`, constructing it on first use.

        Raises:
            OfflineViolationError: `[llm] offline = true` and `config` is not provably local.
        """
        cached = self._built.get((name, model))
        if cached is not None:
            return cached
        _check_offline(name, config, self._context.offline)
        factory = self._factories[config.kind]
        api_key = _resolve_api_key(name, config.api_key_env, self._context.environ)
        instance = factory(name, config, model, api_key, self._context.clock)
        self._built[(name, model)] = instance
        return instance


def default_embedding_factories() -> Mapping[ProviderKind, EmbeddingFactory]:
    """Return the built-in `kind -> EmbeddingFactory` table for every embedding adapter this ships.

    Covers `fake`, `openai_compat` and `sentence_transformers`; `anthropic` has no embedding
    endpoint and `whisper_local` only transcribes, so both are absent here the same way
    `EMBEDDING_ONLY_KINDS` kinds are absent from the chat table (ADR-0036).
    """
    return {
        "fake": _build_fake_embedding,
        "openai_compat": _build_openai_compat_embedding,
        "sentence_transformers": _build_sentence_transformers_embedding,
    }


def _build_fake_embedding(
    name: str, config: ProviderConfig, model: str, api_key: SecretStr | None, clock: Clock
) -> EmbeddingProvider:
    """Build a FakeEmbedding; `config` and `api_key` are accepted (Protocol shape) and unused.

    The fake hashes text directly (no model-specific behaviour), but still reports `model` as its
    responses' model id, so a fake-backed Hive tags its vectors with the binding's model exactly
    the way a real adapter does (ADR-0036).
    """
    return FakeEmbedding(name=name, clock=clock, model=model)


def _build_openai_compat_embedding(
    name: str, config: ProviderConfig, model: str, api_key: SecretStr | None, clock: Clock
) -> EmbeddingProvider:
    """Build an OpenAICompatEmbedding from `config` and `model`; see its own `.create()`."""
    oc_config = OpenAICompatEmbeddingConfig(
        base_url=config.base_url,
        model=model,
        api_key=api_key,
        timeout_s=config.timeout_s,
        **_embedding_batch_kwargs(config),
    )
    return OpenAICompatEmbedding.create(name, oc_config, clock)


def _build_sentence_transformers_embedding(
    name: str, config: ProviderConfig, model: str, api_key: SecretStr | None, clock: Clock
) -> EmbeddingProvider:
    """Build a SentenceTransformersEmbedding from `config`/`model`; `api_key` unused (local)."""
    st_config = SentenceTransformersConfig(
        model=model,
        device=config.embedding_device,
        local_files_only=config.embedding_local_files_only,
        **_embedding_batch_kwargs(config),
    )
    return SentenceTransformersEmbedding(name, st_config, clock)


def _embedding_batch_kwargs(config: ProviderConfig) -> dict[str, int]:
    """Return `{"batch_size": ...}` when `config` set one, else `{}` to keep the adapter's default.

    Shared by both embedding factories that take a `batch_size`, so an unset manifest value
    (`config.embedding_batch_size is None`) never overrides an adapter's own sensible default
    with a hard-coded number this module would otherwise have to duplicate from each adapter.
    """
    if config.embedding_batch_size is None:
        return {}
    return {"batch_size": config.embedding_batch_size}
