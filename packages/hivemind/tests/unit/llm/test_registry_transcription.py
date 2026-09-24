"""Tests for hivemind.llm.registry's transcription half: transcriber() and bound_transcriber().

Split from test_registry.py by feature (codingrules 5.1: a test module may split one source
module's tests by feature), mirroring src/hivemind/llm/registry.py like its sibling.

Fits into the Hive:
    Mirrors src/hivemind/llm/registry.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.registry for the module under test.
    - unit.llm.test_registry for the chat half's tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.llm import (
    bindings_from_manifest,
    make_binding,
    make_provider_config,
    make_registry_deps,
    provider_configs_from_manifest,
)

from hivemind.llm.errors import OfflineViolationError, UnknownProviderError
from hivemind.llm.providers.openai_compat import OpenAICompatTranscription
from hivemind.llm.providers.whisper import WhisperLocalTranscription
from hivemind.llm.registry import (
    ProviderConfig,
    ProviderRegistry,
    TranscriptionBuild,
    TranscriptionUnsupportedError,
    default_transcription_factories,
)
from hivemind.llm.transcription import FakeTranscription, TranscriptionProvider
from hivemind.manifest import load_manifest
from waggle.clock import FakeClock

# Same depth as test_registry.py's own _REPO_ROOT.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MANIFESTS_DIR = _REPO_ROOT / "docs" / "manifests"
_LOOPBACK_URL = "http://127.0.0.1:8080/v1"


def _registry(providers: dict[str, ProviderConfig], offline: bool = False) -> ProviderRegistry:
    """Build a registry over `providers` with the default factory tables and no bindings."""
    return ProviderRegistry(providers, [], offline=offline, deps=make_registry_deps())


def _build(kind: str, offline: bool = False, **config: object) -> TranscriptionBuild:
    """Build the TranscriptionBuild a factory of `kind` receives for model "speech-model"."""
    return TranscriptionBuild(
        name="ears",
        config=make_provider_config(kind=kind, **config),
        model="speech-model",
        api_key=None,
        clock=FakeClock(),
        offline=offline,
    )


def test_transcriber_builds_once_per_provider_and_model_pair() -> None:
    registry = _registry({"ears": make_provider_config(kind="fake")})

    first = registry.transcriber("ears", "speech-small")
    again = registry.transcriber("ears", "speech-small")
    other = registry.transcriber("ears", "speech-large")

    assert first is again
    assert other is not first
    assert isinstance(first, FakeTranscription)


def test_transcriber_raises_for_an_unconfigured_name() -> None:
    with pytest.raises(UnknownProviderError):
        _registry({}).transcriber("nobody", "speech-model")


def test_transcriber_refuses_a_kind_that_cannot_transcribe_and_names_it() -> None:
    registry = _registry({"hosted": make_provider_config(kind="anthropic")})

    with pytest.raises(TranscriptionUnsupportedError, match="anthropic") as caught:
        registry.transcriber("hosted", "speech-model")

    assert caught.value.kind == "anthropic"
    assert caught.value.provider == "hosted"


def test_an_in_process_transcriber_needs_no_base_url_while_offline() -> None:
    registry = _registry({"whisper": make_provider_config(kind="whisper_local")}, offline=True)

    assert isinstance(registry.transcriber("whisper", "speech-model"), WhisperLocalTranscription)


def test_a_server_transcriber_is_refused_offline_at_a_non_loopback_url() -> None:
    config = make_provider_config(kind="openai_compat", base_url="http://example.com/v1")
    registry = _registry({"hosted": config}, offline=True)

    with pytest.raises(OfflineViolationError):
        registry.transcriber("hosted", "speech-model")


def test_a_server_transcriber_is_built_offline_at_a_loopback_url() -> None:
    config = make_provider_config(kind="openai_compat", base_url=_LOOPBACK_URL)
    registry = _registry({"local": config}, offline=True)

    assert isinstance(registry.transcriber("local", "speech-model"), OpenAICompatTranscription)


def test_the_whisper_factory_loads_from_disk_only_when_offline() -> None:
    factory = default_transcription_factories()["whisper_local"]

    offline = factory(_build("whisper_local", offline=True, timeout_s=30.0))
    online = factory(_build("whisper_local", offline=False))

    assert isinstance(offline, WhisperLocalTranscription)
    assert isinstance(online, WhisperLocalTranscription)
    assert offline._config.local_files_only is True
    assert offline._config.timeout_s == 30.0
    assert offline._config.model == "speech-model"
    assert online._config.local_files_only is False


def test_the_openai_compat_factory_serves_the_bindings_model_not_the_default_model() -> None:
    build = _build("openai_compat", base_url=_LOOPBACK_URL, default_model="chat-model")

    transcriber = default_transcription_factories()["openai_compat"](build)

    assert isinstance(transcriber, OpenAICompatTranscription)
    assert transcriber._config.model == "speech-model"


def test_a_custom_transcription_factory_receives_the_offline_flag_and_the_model() -> None:
    seen: list[TranscriptionBuild] = []

    def factory(build: TranscriptionBuild) -> TranscriptionProvider:
        seen.append(build)
        return FakeTranscription(name=build.name)

    deps = make_registry_deps(transcription_factories={"fake": factory})
    # A fake is not an in-process kind, so offline it still needs a provably local base_url.
    providers = {"ears": make_provider_config(kind="fake", base_url=_LOOPBACK_URL)}
    ProviderRegistry(providers, [], offline=True, deps=deps).transcriber("ears", "speech-model")

    assert (seen[0].name, seen[0].model, seen[0].offline) == ("ears", "speech-model", True)


def test_bound_transcriber_resolves_the_slot_through_the_registrys_own_lookup() -> None:
    bindings = [make_binding(key="transcriber", provider="ears", model="speech-model")]
    providers = {"ears": make_provider_config(kind="fake")}
    registry = ProviderRegistry(providers, bindings, offline=False, deps=make_registry_deps())

    bound = registry.bound_transcriber()

    assert bound.provider is registry.transcriber("ears", "speech-model")
    assert bound.binding == "transcriber"


async def test_transcription_health_probes_only_what_was_built() -> None:
    registry = _registry({"ears": make_provider_config(kind="fake")})
    assert await registry.transcription_health() == {}

    registry.transcriber("ears", "speech-model")
    readings = await registry.transcription_health()

    assert list(readings) == [("ears", "speech-model")]


def test_the_full_manifests_transcriber_is_an_openai_compatible_server() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "full.toml")
    registry = ProviderRegistry(
        provider_configs_from_manifest(manifest),
        bindings_from_manifest(manifest).values(),
        offline=manifest.llm.offline,
        deps=make_registry_deps(),
    )

    bound = registry.bound_transcriber()

    assert isinstance(bound.provider, OpenAICompatTranscription)
    assert bound.model == manifest.llm.slots["transcriber"].model


def test_the_minimal_manifests_anthropic_transcriber_is_refused_at_first_use() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")
    registry = ProviderRegistry(
        provider_configs_from_manifest(manifest),
        bindings_from_manifest(manifest).values(),
        offline=manifest.llm.offline,
        deps=make_registry_deps(),
    )

    with pytest.raises(TranscriptionUnsupportedError):
        registry.bound_transcriber()
