"""Define ProviderRegistry: construct, cache and health-check every configured LLM provider.

`hivemind.llm.registry` is roadmap step 3.4's other half: `hivemind.llm.slots.resolve` walks a
`ModelSlot` to a `BoundModel` given a live provider lookup; this module IS that lookup's
production implementation, plus the one place a `kind` string turns into a live `LLMProvider`.
`ProviderRegistry` constructs a provider lazily (on first use, not at startup) and caches it, so
a Hive with nine slots bound to three providers only ever builds three provider objects.
`default_factories()` is the built-in `kind -> ProviderFactory` table for the three adapters this
phase ships (`fake`, `openai_compat`, `anthropic`); `apply_overrides` is the one place a manifest's
per-provider capability override replaces a base `ProviderCapabilities` field. Offline mode
(`[llm] offline = true`, codingrules section 8.6) is enforced a second time here, at
construction, on top of the manifest's own load-time check -- belt and braces, and the only way
to catch a provider kind (`ANTHROPIC`) whose *default* endpoint is hosted even when its
`base_url` is left empty. `_is_provably_local` also accepts a documented Virtual Cell gateway host
(`waggle.uris.is_virtual_cell_gateway_host`), not only genuine loopback: this same registry builds
a Virtual Cell's own in-Cell `ProviderRegistry`
(`hivemind.cli.in_cell.providers.build_in_cell_provider_registry`), whose provider `base_url`s have
already been rewritten to the gateway alias a Cell reaches the Hive Stand through -- not loopback
from inside the Cell, but still "this same machine" for `offline`'s own purpose.

This module cannot import `hivemind.manifest`: codingrules section 4 places `llm` and `manifest`
as independent Layer 1 siblings, neither of which may import the other -- the same reason
`hivemind.forage.map.SlotBinding` exists instead of `hivemind.llm.slots.resolve` taking the
manifest's own `LlmSection` directly (see that module's docstring). `ProviderConfig` below is
this package's own, decoupled mirror of one `[llm.providers.<name>]` row
(`hivemind.manifest.schema.llm.ProviderSpec`); the composition root (the CLI, roadmap step 3.21)
builds one per provider from a loaded `HiveManifest` before constructing a `ProviderRegistry`,
the same way it builds `hivemind.forage.map.SlotBinding` rows for `resolve`/`resolve_key`.

Roadmap step 7.1 (ADR-0032) adds the embedding half beside all of that: `embedder(slot=ModelSlot.
EMBEDDER) -> BoundEmbedder` is `bound`'s counterpart for the non-chat `EmbeddingProvider` door
(`hivemind.llm.embedding`). It differs from `bound` in three ways ADR-0032 requires. First, an
embedding provider is built per `(provider name, model)` pair, not per provider name alone
(`EmbeddingRequest` carries no model id -- see that module's own docstring), so this module caches
embedding providers in a second, separately-keyed cache. Second, a `[llm.slots]` kind with no
embedding factory at all (`ANTHROPIC` today) fails the *primary* binding outright
(`EmbeddingUnsupportedError`), because there is then nothing to serve the slot; the same problem
on a *fallback* link just ends the chain there, the same graceful-degrade spirit `_walk_chain`
already applies to a missing key. Third, a fallback link is only ever included when it names the
*same model id* as the link before it: two different embedding models' vectors are not
comparable, so `embedder` never spills across a model change the way `bound`'s fallback chain
freely spills across providers for chat.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Constructed once by the composition root
    (`cli/stores.py`, roadmap step 3.21) and read by every Worker, Warden and the Queen for a
    `BoundModel` or a `BoundEmbedder`, and by `hive llm providers`/`hive llm test embedder` for
    `health()`/`embedder()`. Calls into `hivemind.forage.map`, `hivemind.forage.slots`,
    `hivemind.llm.provider`, `hivemind.llm.capabilities`, `hivemind.llm.embedding`,
    `hivemind.llm.errors`, `hivemind.llm.fake`, `hivemind.llm.providers.openai_compat`,
    `hivemind.llm.providers.sentence_transformers`, `hivemind.llm.slots` and `waggle` only --
    never `hivemind.manifest` (see above).

Key invariants:
    - `provider(name)` constructs at most once per name: a second call for the same name returns
      the cached instance, never a fresh one (codingrules section 8.6's "one door" would be
      pointless if every layer above got its own copy of the same provider). `embedder()` caches
      the same way, keyed by `(provider name, model)` instead (see the module docstring above).
    - Offline mode is checked before a factory ever runs (`_check_offline`), so a misconfigured
      remote provider never gets as far as opening a client. An `IN_PROCESS_KINDS` member (only
      `sentence_transformers` today) is provably local regardless of its `base_url`, because it
      never opens one at all.
    - `PENDING_KINDS` names every `ProviderKind` `default_factories()` does not cover, and
      `EMBEDDING_ONLY_KINDS` names every kind with no chat factory at all (excluded from the
      chat-factory completeness test the same way `PENDING_KINDS` is); both are empty or
      one-member sets today and stay so future kinds have a home.
    - `_resolve_api_key` is the only place this module reads `RegistryDeps.environ`; it mirrors
      `hivemind.manifest.env.provider_api_key`'s own derivation (which this module cannot call
      directly, for the same reason it cannot import `ProviderSpec`) so a provider's secret still
      comes from exactly one environment variable, never the config itself (codingrules 13).

See Also:
    - .claude/codingrules.md section 8.6 for "offline is a first-class mode" and "one door".
    - .claude/codingrules.md section 4 for the Layer 1 "llm | manifest" independent-siblings rule
      this module's `ProviderConfig` exists to respect.
    - .claude/codingrules.md section 13 for the environment-variable and SecretStr rules this
      module's `_resolve_api_key` follows.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the embedder() rules above.
    - hivemind.llm.slots for resolve/resolve_key, the functions `bound`/`bound_for_key` call.
    - hivemind.llm.providers.openai_compat for two of the three embedding adapters this wires up.
    - hivemind.llm.embedding for BoundEmbedder, EmbeddingProvider and EmbeddingUnsupportedError.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import SecretStr

from hivemind.common.errors import InvariantViolationError
from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import ModelSlot
from hivemind.llm.capabilities import ProviderCapabilities, ProviderHealth
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
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.provider import LLMProvider
from hivemind.llm.providers.anthropic import AnthropicConfig, AnthropicProvider
from hivemind.llm.providers.openai_compat import (
    OpenAICompatConfig,
    OpenAICompatEmbedding,
    OpenAICompatEmbeddingConfig,
    OpenAICompatProvider,
)
from hivemind.llm.providers.sentence_transformers import (
    SentenceTransformersConfig,
    SentenceTransformersEmbedding,
)
from hivemind.llm.slots import BoundModel, resolve, resolve_key
from waggle.clock import Clock
from waggle.uris import is_loopback_host, is_virtual_cell_gateway_host

# Mirrors hivemind.manifest.schema.llm.ProviderKind member-for-member; llm may not import manifest
# (codingrules section 4), so this is this module's own copy, kept in sync by a dedicated test.
ProviderKind = Literal["anthropic", "openai_compat", "fake", "sentence_transformers"]

# ANTHROPIC's factory is added to default_factories() once its adapter (roadmap step 3.6) lands;
# the completeness test excludes exactly this set from "every ProviderKind must have a factory".
PENDING_KINDS: frozenset[ProviderKind] = frozenset()

# Kinds with no chat factory at all (roadmap 7.1): sentence_transformers only ever serves the
# EMBEDDER slot, so default_factories()'s own completeness test excludes it the same way it
# excludes PENDING_KINDS, and the manifest (hivemind.manifest.schema.llm's own mirror of this set)
# refuses to bind any other slot to one.
EMBEDDING_ONLY_KINDS: frozenset[ProviderKind] = frozenset({"sentence_transformers"})

# Kinds that run inside this process and so are provably local under [llm] offline = true
# regardless of what their base_url holds (an in-process kind opens no base_url at all).
IN_PROCESS_KINDS: frozenset[ProviderKind] = frozenset({"sentence_transformers"})

__all__ = [
    "EMBEDDING_ONLY_KINDS",
    "IN_PROCESS_KINDS",
    "PENDING_KINDS",
    "EmbeddingFactory",
    "MissingDefaultModelError",
    "ProviderConfig",
    "ProviderFactory",
    "ProviderKind",
    "ProviderRegistry",
    "RegistryDeps",
    "apply_overrides",
    "default_embedding_factories",
    "default_factories",
]


class MissingDefaultModelError(InvariantViolationError):
    """Raise when an openai_compat ProviderConfig reaches its factory with no default_model.

    The composition root derives `default_model` from the provider's first `[llm.slots]` row
    before building a registry, so reaching this means Hive code skipped that step: a bug in
    the Hive, not bad operator input (codingrules section 10), and named so the trail can say
    which invariant broke.
    """

    code: ClassVar[str] = "hivemind.llm.missing_default_model"

    def __init__(self, name: str) -> None:
        """Build the error for provider `name`.

        Args:
            name: The `[llm.providers.<name>]` key whose config lacks a default model.
        """
        super().__init__(
            f"[llm.providers.{name}] is kind='openai_compat' but no default_model was derived for "
            "it; the composition root must supply one (e.g. from its first [llm.slots] row)."
        )
        self.name = name


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """One `[llm.providers.<name>]` row, decoupled from `hivemind.manifest.schema.llm.ProviderSpec`.

    Mirrors that model's fields (see the module docstring for why this module cannot import it
    directly); the composition root builds one of these per provider from a loaded HiveManifest.
    """

    kind: ProviderKind  # Which factory in RegistryDeps.factories builds this provider.
    base_url: str  # "" means a hosted API with a vendor-fixed endpoint; non-empty for a local one.
    api_key_env: str | None = None  # HIVEMIND_<NAME>_API_KEY is derived when this is None.
    timeout_s: float = 120.0  # Mirrors manifest.schema.llm.DEFAULT_PROVIDER_TIMEOUT_S's default.
    capability_overrides: Mapping[str, bool | int] = field(default_factory=dict)  # From
    # ProviderSpec.capabilities.as_overrides(): only the fields a manifest author actually set.
    default_model: str | None = None  # Required for kind="openai_compat"; unused by every other.
    # The three fields below mirror hivemind.manifest.schema.llm.EmbeddingOptions (roadmap 7.1);
    # unused by a provider this Hive never asks to embed. embedding_local_files_only is the one
    # the composition root (cli/stores.provider_configs) forces True whenever [llm] offline = true,
    # regardless of what the manifest itself set (an in-process embedder must never reach a model
    # hub on a Hive proven offline).
    embedding_batch_size: int | None = None  # None lets an embedding factory's own default decide.
    embedding_device: str | None = None  # None lets the in-process library choose a device.
    embedding_local_files_only: bool = False  # Never reach a model hub to load an embedder.


@runtime_checkable
class _Closable(Protocol):
    """A provider that owns a connection pool it must close when its Hive shuts down."""

    async def aclose(self) -> None:
        """Close the pool; see `ProviderRegistry.aclose`."""
        ...


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


class EmbeddingFactory(Protocol):
    """Build a live EmbeddingProvider for one specific `(provider name, model)` pair.

    Unlike `ProviderFactory`, this also takes `model`: an embedding adapter is built per model
    (`hivemind.llm.embedding.models`'s own docstring explains why a request carries no model id
    of its own), where a chat provider is built once per provider name and takes a model per call.
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


@dataclass(frozen=True, slots=True)
class RegistryDeps:
    """Collaborators a ProviderRegistry needs beyond the manifest-derived config it is given."""

    factories: Mapping[ProviderKind, ProviderFactory]  # kind -> how to build a provider of it.
    environ: Mapping[str, str]  # Read only by _resolve_api_key (codingrules section 13).
    clock: Clock  # Passed to every factory and, through it, to every constructed provider.
    map: ForageMap | None = None  # Prices resolve()'s BoundModels; None leaves them unpriced.
    # kind -> how to build an EmbeddingProvider of it; defaulted so every existing RegistryDeps
    # call site (built before roadmap 7.1) keeps working unchanged.
    embedding_factories: Mapping[ProviderKind, EmbeddingFactory] = field(
        default_factory=lambda: default_embedding_factories()
    )


class ProviderRegistry:
    """Construct, cache and health-check every configured LLM provider.

    See the module docstring for why this takes `providers`/`bindings`/`offline` as separate,
    already-manifest-derived pieces rather than a `HiveManifest` (or its `LlmSection`) directly.
    """

    def __init__(
        self,
        providers: Mapping[str, ProviderConfig],
        bindings: Iterable[SlotBinding],
        offline: bool,
        deps: RegistryDeps,
    ) -> None:
        """Store this Hive's provider configs and Forage-side slot bindings; construct nothing yet.

        Args:
            providers: Every `[llm.providers.<name>]` row, decoupled (see ProviderConfig).
            bindings: Every `[llm.slots]` row, forage-side (hivemind.forage.map.SlotBinding).
            offline: This Hive's `[llm] offline` flag.
            deps: Factories, environment, clock and an optional Forage map.
        """
        self._providers = dict(providers)
        self._bindings = tuple(bindings)
        self._offline = offline
        self._deps = deps
        self._cache: dict[str, LLMProvider] = {}
        # Keyed by (provider name, model), not by name alone: an embedding adapter is built per
        # model (module docstring's "Key invariants"), unlike a chat provider.
        self._embedding_cache: dict[tuple[str, str], EmbeddingProvider] = {}

    def names(self) -> tuple[str, ...]:
        """Return every `[llm.providers.*]` name this registry knows, in manifest order."""
        return tuple(self._providers.keys())

    def provider(self, name: str) -> LLMProvider:
        """Return the live provider named `name`, constructing and caching it on first use.

        Args:
            name: A `[llm.providers.<name>]` key.

        Returns:
            The same LLMProvider instance on every call for a given `name`.

        Raises:
            UnknownProviderError: No provider is configured under `name`, or its `kind` has no
                factory in `deps.factories` (a kind listed in `PENDING_KINDS`).
            OfflineViolationError: `[llm] offline = true` and this provider's `base_url` is not
                provably loopback.
        """
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        config = self._providers.get(name)
        if config is None:
            raise UnknownProviderError(name)
        factory = self._deps.factories.get(config.kind)
        if factory is None:
            raise UnknownProviderError(name)
        _check_offline(name, config, self._offline)
        api_key = _resolve_api_key(name, config.api_key_env, self._deps.environ)
        instance = factory(name, config, api_key, self._deps.clock)
        self._cache[name] = instance
        return instance

    def bound(self, slot: ModelSlot) -> BoundModel:
        """Resolve `slot` to a BoundModel; see `hivemind.llm.slots.resolve`."""
        return resolve(slot, self._bindings, self.provider, self._deps.map)

    def bound_for_key(self, key: str, slot: ModelSlot) -> BoundModel:
        """Resolve a named binding for `slot`; see `hivemind.llm.slots.resolve_key`."""
        return resolve_key(key, slot, self._bindings, self.provider, self._deps.map)

    def embedder(self, slot: ModelSlot = ModelSlot.EMBEDDER) -> BoundEmbedder:
        """Resolve `slot`'s own `[llm.slots]` chain to a BoundEmbedder (ADR-0032).

        See the module docstring for how this differs from `bound`: a per-(name, model) cache, a
        hard failure only for the primary binding's kind, and a fallback kept only when it serves
        the same model id as the link before it.

        Args:
            slot: The slot to resolve; `ModelSlot.EMBEDDER` in every call the Hive makes today.

        Returns:
            A BoundEmbedder for `slot`, with a `.fallback` chain for every same-model link `bound`
            reaches before the chain runs out, a different model id cuts it, or a link names a
            provider with no embedding factory.

        Raises:
            UnresolvableSlotError: `bindings` has no row for `slot.manifest_key`, or the chain
                cycles.
            UnknownProviderError: The primary binding names a provider `providers` does not know.
            EmbeddingUnsupportedError: The primary binding's provider kind has no embedding
                factory (e.g. `anthropic`).
            OfflineViolationError: `[llm] offline = true` and the primary binding's provider is
                not provably local.
        """
        by_key = {binding.key: binding for binding in self._bindings}
        chain = walk_embedder_chain(slot.manifest_key, by_key)
        primary_config = self._providers.get(chain[0].provider)
        if primary_config is None:
            raise UnknownProviderError(chain[0].provider)
        if primary_config.kind not in self._deps.embedding_factories:
            raise EmbeddingUnsupportedError(chain[0].provider, primary_config.kind)
        return self._bind_embedder(slot, chain, 0)

    async def health(self) -> dict[str, ProviderHealth]:
        """Probe every provider this registry has already constructed.

        Never constructs a provider that has not been used yet: `hive llm providers` shows the
        health of what is actually in play, not every name the manifest happens to list.
        """
        return {name: await instance.health() for name, instance in self._cache.items()}

    async def aclose(self) -> None:
        """Close every provider this registry built that owns a connection pool, then forget them.

        Called once by each composition root as its Hive or command ends. An HTTP adapter keeps
        connections alive to its server between calls, so a registry dropped without this leaves
        open sockets behind (found 2026-09-24, the phase 7 `local_llm` eval's first run on a real
        local server). Closing is found structurally (`_Closable`), not by provider kind: a fake
        or an in-process model has nothing to close and is simply forgotten. A provider asked for
        after this call is built afresh.
        """
        built: list[object] = [*self._cache.values(), *self._embedding_cache.values()]
        self._cache.clear()
        self._embedding_cache.clear()
        # Each adapter that pooled connections closes them; everything else has nothing to close.
        for instance in built:
            if isinstance(instance, _Closable):
                await instance.aclose()

    def _bind_embedder(
        self, slot: ModelSlot, chain: list[SlotBinding], index: int
    ) -> BoundEmbedder:
        """Build `chain[index]` into a BoundEmbedder, recursing for its own fallback.

        `embedder()` has already proved `chain[0]`'s provider and kind are buildable before the
        first call; every later call here builds a link `_next_embedder_link` already proved
        buildable the same way, so `self._providers[binding.provider]` below never misses.
        """
        binding = chain[index]
        config = self._providers[binding.provider]
        return BoundEmbedder(
            slot=slot,
            binding=binding.key,
            provider=self._embedding_provider(binding.provider, binding.model, config),
            model=binding.model,
            cost_per_million_input_usd=embedder_cost(binding, self._deps.map),
            fallback=self._next_embedder_link(slot, chain, index),
        )

    def _next_embedder_link(
        self, slot: ModelSlot, chain: list[SlotBinding], index: int
    ) -> BoundEmbedder | None:
        """Return `chain[index + 1]` bound, or None when the chain ends or ADR-0032 cuts it here.

        Cutting is silent, never a raised error: a fallback is a nicety the primary binding does
        not depend on, so a badly configured or offline fallback should not break a slot whose
        primary binding already resolved cleanly (contrast `embedder()`'s own hard failures on
        the primary link, where there is nothing else to serve the slot at all).
        """
        if index + 1 >= len(chain):
            return None
        next_binding = chain[index + 1]
        if next_binding.model != chain[index].model:
            return None  # ADR-0032: a fallback is followed only when it serves the same model id.
        next_config = self._providers.get(next_binding.provider)
        if next_config is None or next_config.kind not in self._deps.embedding_factories:
            return None  # Unknown provider, or a kind that cannot embed: nothing to build here.
        try:
            _check_offline(next_binding.provider, next_config, self._offline)
        except OfflineViolationError:
            return None  # Same "cannot build, so cut" treatment as an unsupported kind above.
        return self._bind_embedder(slot, chain, index + 1)

    def _embedding_provider(
        self, name: str, model: str, config: ProviderConfig
    ) -> EmbeddingProvider:
        """Return the live embedding provider for `(name, model)`, constructing it on first use.

        Raises:
            OfflineViolationError: `[llm] offline = true` and `config` is not provably local.
        """
        cached = self._embedding_cache.get((name, model))
        if cached is not None:
            return cached
        _check_offline(name, config, self._offline)
        factory = self._deps.embedding_factories[config.kind]
        api_key = _resolve_api_key(name, config.api_key_env, self._deps.environ)
        instance = factory(name, config, model, api_key, self._deps.clock)
        self._embedding_cache[(name, model)] = instance
        return instance


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
    """Return the built-in `kind -> ProviderFactory` table for every adapter this phase ships.

    Covers every `ProviderKind` except `PENDING_KINDS` (currently none) and `EMBEDDING_ONLY_KINDS`
    (`sentence_transformers`, which has no chat factory at all).
    """
    return {
        "fake": _build_fake,
        "openai_compat": _build_openai_compat,
        "anthropic": _build_anthropic,
    }


def default_embedding_factories() -> Mapping[ProviderKind, EmbeddingFactory]:
    """Return the built-in `kind -> EmbeddingFactory` table for every embedding adapter this ships.

    Covers `fake`, `openai_compat` and `sentence_transformers`; `anthropic` has no embedding
    endpoint, so it is absent here the same way `EMBEDDING_ONLY_KINDS` kinds are absent from
    `default_factories()` (ADR-0032).
    """
    return {
        "fake": _build_fake_embedding,
        "openai_compat": _build_openai_compat_embedding,
        "sentence_transformers": _build_sentence_transformers_embedding,
    }


def _check_offline(name: str, config: ProviderConfig, offline: bool) -> None:
    """Raise OfflineViolationError when `offline` and `config` is not provably local.

    Mirrors hivemind.manifest.schema.llm's own (private) loopback rule from the same public
    primitive, `waggle.uris.is_loopback_host`, rather than importing a name manifest does not
    export -- belt and braces on top of the manifest's own load-time check (codingrules 8.6), and
    the only place that also catches a provider kind (ANTHROPIC) whose *default* endpoint is
    remote even when `base_url` is left empty. An `IN_PROCESS_KINDS` member never opens a
    `base_url` at all (roadmap 7.1), so it is provably local by construction, whatever `base_url`
    happens to hold.
    """
    if not offline:
        return
    if config.kind in IN_PROCESS_KINDS:
        return
    if not _is_provably_local(config.base_url):
        raise OfflineViolationError(name, config.base_url)


def _is_provably_local(base_url: str) -> bool:
    """Return True when `base_url` names a loopback host or a Virtual Cell gateway host.

    This registry is shared by the Hive Stand's own composition root and by a Virtual Cell's own
    in-Cell registry (`hivemind.cli.in_cell.providers.build_in_cell_provider_registry`): a
    provider's `base_url` a Cell resolves has already been rewritten to the gateway alias it
    reaches the Hive Stand through (`host.docker.internal`, QEMU's `10.0.2.2`, module docstring's
    own file list), which `is_loopback_host` alone would reject as "not local" -- from inside the
    Cell, that gateway address IS the Hive Stand's own machine (the same reasoning
    `waggle.uris.check_waggle_uri`'s `allow_virtual_cell_gateway_host` already applies to the
    Waggle control link). `is_virtual_cell_gateway_host` is the minimal, documented carve-out for
    that one extra case; every other host still fails exactly as before.
    """
    if not base_url:
        return False  # Empty means a hosted, vendor-fixed endpoint (e.g. Anthropic): never local.
    hostname = urlsplit(base_url).hostname
    if hostname is None:
        return False
    return is_loopback_host(hostname) or is_virtual_cell_gateway_host(hostname)


def _resolve_api_key(
    name: str, api_key_env: str | None, environ: Mapping[str, str]
) -> SecretStr | None:
    """Read one provider's API key from `environ`, mirroring hivemind.manifest.env.provider_api_key.

    That function cannot be called directly here: it takes a ProviderSpec, and llm may not
    import manifest (see the module docstring). This is the same short derivation, kept here so
    `RegistryDeps.environ` stays the only environment read this module performs (codingrules
    section 13: "environment variables are read in exactly one place").
    """
    var_name = api_key_env or f"HIVEMIND_{name.upper()}_API_KEY"
    raw = environ.get(var_name)
    return SecretStr(raw) if raw is not None else None


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
    capabilities = apply_overrides(ProviderCapabilities.full(), config.capability_overrides)
    oc_config = OpenAICompatConfig(
        base_url=config.base_url,
        model=config.default_model,
        api_key=api_key,
        timeout_s=config.timeout_s,
        capabilities=capabilities,
    )
    return OpenAICompatProvider.create(name, oc_config, clock)


def _build_fake_embedding(
    name: str, config: ProviderConfig, model: str, api_key: SecretStr | None, clock: Clock
) -> EmbeddingProvider:
    """Build a FakeEmbedding; `config` and `api_key` are accepted (Protocol shape) and unused.

    The fake hashes text directly (no model-specific behaviour), but still reports `model` as its
    responses' model id, so a fake-backed Hive tags its vectors with the binding's model exactly
    the way a real adapter does (ADR-0032).
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
