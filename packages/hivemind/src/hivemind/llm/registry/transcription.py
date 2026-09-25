"""Define the transcription door: build, cache and probe one transcriber per (provider, model).

Roadmap step 6.5a (ADR-0033) adds the slot that hears, `ModelSlot.TRANSCRIBER`, whose providers
are `hivemind.llm.transcription.TranscriptionProvider`s, not `LLMProvider`s: they come from their
own `kind -> TranscriptionFactory` table (`default_transcription_factories()`: `fake`,
`openai_compat` and the transcription-only `whisper_local`), built lazily and cached like chat
providers but keyed by provider name *and* model id, because a transcriber instance serves one
model (an in-process Whisper holds exactly one loaded model). A chat-only kind (`anthropic`) in a
transcriber binding raises `TranscriptionUnsupportedError`, naming the kind. A kind in
`IN_PROCESS_KINDS` runs its model in this very process, so offline mode needs no `base_url` to
prove it local, and its downloads are switched off instead (`WhisperConfig.local_files_only`).

Fits into the Hive:
    Layer 1 (foundational services). `TranscriberDoor` is owned by
    `hivemind.llm.registry.provider_registry.ProviderRegistry`, which serves `transcriber()`,
    `bound_transcriber()` and `transcription_health()` through it. Calls into
    `hivemind.llm.transcription`, `hivemind.llm.providers.openai_compat`,
    `hivemind.llm.providers.whisper` and this package's `config`.

Key invariants:
    - `TranscriberDoor.get(name, model)` constructs at most once per (name, model) pair; chat and
      transcription instances of one provider name are separate objects.
    - Offline mode is checked before a factory ever runs, and the factory gets the offline flag
      too, so an in-process kind can refuse to download weights while the Hive is offline.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the transcription factories.
    - hivemind.llm.transcription.binding for resolve_transcriber, which `bound_transcriber` calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from hivemind.llm.capabilities import ProviderHealth
from hivemind.llm.errors import UnknownProviderError
from hivemind.llm.providers.openai_compat import (
    OpenAICompatTranscription,
    OpenAICompatTranscriptionConfig,
)
from hivemind.llm.providers.whisper import WhisperConfig, WhisperLocalTranscription
from hivemind.llm.registry.config import (
    ProviderConfig,
    ProviderKind,
    TranscriptionUnsupportedError,
    _check_offline,
    _DoorContext,
    _resolve_api_key,
)
from hivemind.llm.transcription import FakeTranscription, TranscriptionProvider
from waggle.clock import Clock

__all__ = [
    "TranscriberDoor",
    "TranscriptionBuild",
    "TranscriptionFactory",
    "default_transcription_factories",
]


@dataclass(frozen=True, slots=True)
class TranscriptionBuild:
    """Everything a TranscriptionFactory needs to build one transcriber (codingrules 5.1)."""

    name: str  # The manifest's [llm.providers.<name>] key.
    config: ProviderConfig  # That provider's row.
    model: str  # The model id the binding names; one transcriber instance serves one model.
    api_key: SecretStr | None  # The resolved secret, or None when the provider needs none.
    clock: Clock  # Passed through to the provider's own health() readings.
    offline: bool  # [llm] offline: an in-process kind then loads weights from disk only.


class TranscriptionFactory(Protocol):
    """Build a live TranscriptionProvider for one (provider, model) pair."""

    def __call__(self, build: TranscriptionBuild) -> TranscriptionProvider:
        """Return a new transcriber instance for `build.name` serving `build.model`.

        Args:
            build: The provider's row, the model id, its secret, the clock and the offline flag.
        """
        ...


class TranscriberDoor:
    """The registry's transcription half: one cached transcriber per (provider name, model)."""

    def __init__(
        self, context: _DoorContext, factories: Mapping[ProviderKind, TranscriptionFactory]
    ) -> None:
        """Remember the rows and factories; build nothing until a binding asks.

        Args:
            context: The owning registry's provider rows, offline flag, environment and clock.
            factories: kind -> how to build a transcriber of it.
        """
        self._context = context
        self._factories = factories
        self._built: dict[tuple[str, str], TranscriptionProvider] = {}

    def get(self, name: str, model: str) -> TranscriptionProvider:
        """Return the live transcriber for `model` on `name`; see `ProviderRegistry.transcriber`.

        Raises:
            UnknownProviderError: No provider is configured under `name`.
            TranscriptionUnsupportedError: `name`'s kind has no transcription factory.
            OfflineViolationError: `[llm] offline = true` and `name` is neither in process nor
                at a provably loopback `base_url`.
        """
        cached = self._built.get((name, model))
        if cached is not None:
            return cached  # Built before: one instance per pair, like one per chat provider.
        config = self._context.providers.get(name)
        if config is None:
            raise UnknownProviderError(name)
        # A kind with no transcription factory (anthropic) cannot hear; say so by kind.
        factory = self._factories.get(config.kind)
        if factory is None:
            raise TranscriptionUnsupportedError(name, config.kind)
        # An in-process model reaches no server (_check_offline exempts it by kind); anything else
        # must be provably local while the Hive is offline.
        _check_offline(name, config, self._context.offline)
        # Build and cache: the factory gets the offline flag too, so an in-process kind can
        # refuse to download weights while the Hive is offline.
        api_key = _resolve_api_key(name, config.api_key_env, self._context.environ)
        build = TranscriptionBuild(
            name, config, model, api_key, self._context.clock, self._context.offline
        )
        instance = factory(build)
        self._built[(name, model)] = instance
        return instance

    async def health(self) -> dict[tuple[str, str], ProviderHealth]:
        """Probe every transcriber built so far, keyed by (name, model); see the registry's own."""
        return {key: await instance.health() for key, instance in self._built.items()}

    def release(self) -> list[TranscriptionProvider]:
        """Forget every transcriber built so far and return them, for the registry to close."""
        built = list(self._built.values())
        self._built.clear()
        return built


def default_transcription_factories() -> Mapping[ProviderKind, TranscriptionFactory]:
    """Return the built-in `kind -> TranscriptionFactory` table for every transcription adapter.

    `anthropic` is absent on purpose: its API takes no audio, so a transcriber binding naming an
    Anthropic provider raises `TranscriptionUnsupportedError` instead of being built; the
    embedding-only `sentence_transformers` is absent for the same reason.
    """
    return {
        "fake": _build_fake_transcription,
        "openai_compat": _build_openai_compat_transcription,
        "whisper_local": _build_whisper_local,
    }


def _build_fake_transcription(build: TranscriptionBuild) -> TranscriptionProvider:
    """Build a FakeTranscription for `build.name`; it answers silence until scripted."""
    return FakeTranscription(name=build.name, clock=build.clock)


def _build_openai_compat_transcription(build: TranscriptionBuild) -> TranscriptionProvider:
    """Build an OpenAICompatTranscription serving `build.model` at the provider's base URL."""
    config = OpenAICompatTranscriptionConfig(
        base_url=build.config.base_url,
        model=build.model,
        api_key=build.api_key,
        timeout_s=build.config.timeout_s,
    )
    return OpenAICompatTranscription.create(build.name, config, build.clock)


def _build_whisper_local(build: TranscriptionBuild) -> TranscriptionProvider:
    """Build a WhisperLocalTranscription for `build.model`; it loads on its first transcription.

    `local_files_only` follows `[llm] offline`: an offline Hive never downloads weights, it only
    loads what is already on disk (codingrules section 8.6's "offline is a first-class mode").
    """
    config = WhisperConfig(
        model=build.model, local_files_only=build.offline, timeout_s=build.config.timeout_s
    )
    return WhisperLocalTranscription(build.name, config, build.clock)
