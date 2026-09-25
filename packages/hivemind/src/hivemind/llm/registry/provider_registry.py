"""Define ProviderRegistry: construct, cache and health-check every configured model provider.

`hivemind.llm.registry` is roadmap step 3.4's other half: `hivemind.llm.slots.resolve` walks a
`ModelSlot` to a `BoundModel` given a live provider lookup; this registry IS that lookup's
production implementation, plus the one place a `kind` string turns into a live provider.
`ProviderRegistry` constructs a provider lazily (on first use, not at startup) and caches it, so
a Hive with nine slots bound to three providers only ever builds three provider objects.

It has three doors, one per kind of model call, each with its own factory table in
`RegistryDeps`: chat (`provider`, `bound`, `bound_for_key`; `hivemind.llm.registry.chat`),
transcription (`transcriber`, `bound_transcriber`, roadmap step 6.5a, ADR-0033;
`hivemind.llm.registry.transcription`) and embedding (`embedder`, roadmap step 7.1, ADR-0036;
`hivemind.llm.registry.embedding`). A chat provider is cached per provider name; a transcriber
and an embedder per (provider name, model), because one such instance serves exactly one model.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Constructed once by the composition root
    (`cli/stores.py`, roadmap step 3.21) and read by every Worker, Warden and the Queen for a
    `BoundModel`, a `BoundTranscriber` or a `BoundEmbedder`, and by `hive llm providers`/`hive llm
    test` for `health()` and the bindings. Calls into `hivemind.forage.map`,
    `hivemind.forage.slots`, `hivemind.llm.capabilities`, `hivemind.llm.embedding`,
    `hivemind.llm.errors`, `hivemind.llm.provider`, `hivemind.llm.slots`,
    `hivemind.llm.transcription`, this package's other modules and `waggle` only -- never
    `hivemind.manifest` (`config`'s module docstring).

Key invariants:
    - `provider(name)` constructs at most once per name: a second call for the same name returns
      the cached instance, never a fresh one (codingrules section 8.6's "one door" would be
      pointless if every layer above got its own copy of the same provider). The transcription
      and embedding doors cache the same way, keyed by (provider name, model) instead.
    - Offline mode is checked before any factory runs (`config._check_offline`), so a
      misconfigured remote provider never gets as far as opening a client.
    - `aclose` closes every built instance that owns a connection pool, found structurally
      (`_Closable`), never by provider kind, and forgets them all.

See Also:
    - .claude/codingrules.md section 8.6 for "offline is a first-class mode" and "one door".
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - hivemind.llm.slots for resolve/resolve_key, the functions `bound`/`bound_for_key` call.
    - hivemind.llm.transcription for resolve_transcriber, which `bound_transcriber` calls.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import ModelSlot
from hivemind.llm.capabilities import ProviderHealth
from hivemind.llm.embedding import BoundEmbedder
from hivemind.llm.errors import UnknownProviderError
from hivemind.llm.provider import LLMProvider
from hivemind.llm.registry.chat import ProviderFactory
from hivemind.llm.registry.config import (
    ProviderConfig,
    ProviderKind,
    _check_offline,
    _DoorContext,
    _resolve_api_key,
)
from hivemind.llm.registry.embedding import (
    EmbedderDoor,
    EmbeddingFactory,
    default_embedding_factories,
)
from hivemind.llm.registry.transcription import (
    TranscriberDoor,
    TranscriptionFactory,
    default_transcription_factories,
)
from hivemind.llm.slots import BoundModel, resolve, resolve_key
from hivemind.llm.transcription import BoundTranscriber, TranscriptionProvider, resolve_transcriber
from waggle.clock import Clock

__all__ = ["ProviderRegistry", "RegistryDeps"]


@dataclass(frozen=True, slots=True)
class RegistryDeps:
    """Collaborators a ProviderRegistry needs beyond the manifest-derived config it is given."""

    factories: Mapping[ProviderKind, ProviderFactory]  # kind -> how to build a chat provider.
    environ: Mapping[str, str]  # Read only by config._resolve_api_key (codingrules section 13).
    clock: Clock  # Passed to every factory and, through it, to every constructed provider.
    map: ForageMap | None = None  # Prices resolve()'s BoundModels; None leaves them unpriced.
    # kind -> how to build a transcriber (roadmap step 6.5a) or an embedder (roadmap step 7.1);
    # the built-in tables unless a composition root or test substitutes its own, so no caller
    # built before either door existed has to name them.
    transcription_factories: Mapping[ProviderKind, TranscriptionFactory] = field(
        default_factory=lambda: default_transcription_factories()
    )
    embedding_factories: Mapping[ProviderKind, EmbeddingFactory] = field(
        default_factory=lambda: default_embedding_factories()
    )


@runtime_checkable
class _Closable(Protocol):
    """A provider that owns a connection pool it must close when its Hive shuts down."""

    async def aclose(self) -> None:
        """Close the pool; see `ProviderRegistry.aclose`."""
        ...


class ProviderRegistry:
    """Construct, cache and health-check every configured model provider, one door per call kind.

    Takes `providers`/`bindings`/`offline` as separate, already-manifest-derived pieces rather than
    a `HiveManifest`, because `hivemind.llm` may not import `hivemind.manifest` (codingrules 4).
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
        # Every door reads the same rows, offline flag, secrets and clock.
        context = _DoorContext(self._providers, offline, deps.environ, deps.clock)
        self._transcribers = TranscriberDoor(context, deps.transcription_factories)
        self._embedders = EmbedderDoor(context, deps.embedding_factories, deps.map)

    def names(self) -> tuple[str, ...]:
        """Return every `[llm.providers.*]` name this registry knows, in manifest order."""
        return tuple(self._providers.keys())

    def provider(self, name: str) -> LLMProvider:
        """Return the live chat provider named `name`, constructing and caching it on first use.

        Raises:
            UnknownProviderError: No provider is configured under `name`, or its `kind` has no
                chat factory in `deps.factories` (a `PENDING_KINDS` member, a transcription-only
                or an embedding-only kind).
            OfflineViolationError: `[llm] offline = true` and this provider is not provably local.
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

    def transcriber(self, name: str, model: str) -> TranscriptionProvider:
        """Return the live transcriber serving `model` on `name`, building it on first use.

        Raises:
            UnknownProviderError: No provider is configured under `name`.
            TranscriptionUnsupportedError: `name`'s kind has no transcription factory.
            OfflineViolationError: `[llm] offline = true` and `name` is neither in process nor
                at a provably loopback `base_url`.
        """
        return self._transcribers.get(name, model)

    def bound_transcriber(self, key: str | None = None) -> BoundTranscriber:
        """Resolve ModelSlot.TRANSCRIBER (or the named `key`) to a BoundTranscriber chain."""
        return resolve_transcriber(self._bindings, self.transcriber, key)

    def embedder(self, slot: ModelSlot = ModelSlot.EMBEDDER) -> BoundEmbedder:
        """Resolve `slot`'s own `[llm.slots]` chain to a same-model BoundEmbedder (ADR-0036).

        Raises:
            UnresolvableSlotError: `bindings` has no row for `slot.manifest_key`, or it cycles.
            UnknownProviderError: The primary binding names a provider no row declares.
            EmbeddingUnsupportedError: The primary binding's kind has no embedding factory.
            OfflineViolationError: `[llm] offline = true` and the primary binding's provider is
                not provably local.
        """
        return self._embedders.bind(slot, self._bindings)

    async def health(self) -> dict[str, ProviderHealth]:
        """Probe every chat provider this registry has already constructed.

        Never constructs a provider that has not been used yet: `hive llm providers` shows the
        health of what is actually in play, not every name the manifest happens to list.
        """
        return {name: await instance.health() for name, instance in self._cache.items()}

    async def transcription_health(self) -> dict[tuple[str, str], ProviderHealth]:
        """Probe every transcriber this registry has already built, keyed by (name, model).

        Kept apart from `health()` because one provider name may have a chat instance and
        several transcriber instances, each with its own reading.
        """
        return await self._transcribers.health()

    async def aclose(self) -> None:
        """Close every provider this registry built that owns a connection pool, then forget them.

        Called once by each composition root as its Hive or command ends. An HTTP adapter keeps
        connections alive to its server between calls, so a registry dropped without this leaves
        open sockets behind (found 2026-09-24, the phase 7 `local_llm` eval's first run on a real
        local server). Closing is found structurally (`_Closable`), not by provider kind: a fake
        or an in-process model has nothing to close and is simply forgotten. A provider asked for
        after this call is built afresh.
        """
        built: list[object] = [
            *self._cache.values(),
            *self._transcribers.release(),
            *self._embedders.release(),
        ]
        self._cache.clear()
        # Each adapter that pooled connections closes them; everything else has nothing to close.
        for instance in built:
            if isinstance(instance, _Closable):
                await instance.aclose()
