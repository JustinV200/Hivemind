"""Tests for hivemind.llm.registry's TRANSCRIBER binding: ProviderRegistry.transcriber.

Fits into the Hive:
    Mirrors src/hivemind/llm/registry.py (codingrules section 3), split out of test_registry.py
    by feature (codingrules 5.1), like test_registry_close.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.registry for the module under test.
    - unit/llm/transcription/test_binding.py for the chain resolution underneath.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import get_args

import pytest
from builders.forage import make_source
from builders.llm import (
    bindings_from_manifest,
    make_binding,
    make_provider_config,
    make_registry_deps,
    provider_configs_from_manifest,
)
from pydantic import SecretStr

from hivemind.forage.map import ForageMap
from hivemind.forage.models import ModelCost
from hivemind.forage.slots import ModelSlot
from hivemind.llm.errors import OfflineViolationError, UnknownProviderError
from hivemind.llm.providers.openai_compat import OpenAICompatTranscription
from hivemind.llm.registry import (
    ProviderConfig,
    ProviderKind,
    ProviderRegistry,
    default_transcriber_factories,
)
from hivemind.llm.transcription import (
    FakeTranscription,
    TranscriptionProvider,
    TranscriptionUnsupportedError,
)
from hivemind.manifest import load_manifest
from waggle.clock import Clock, FakeClock

_REPO_ROOT = Path(__file__).resolve().parents[5]
_MANIFESTS_DIR = _REPO_ROOT / "docs" / "manifests"
_LOCAL_URL = "http://127.0.0.1:9/v1"  # Loopback, and nothing listens on port 9.
_TRANSCRIBER_ROW = make_binding(key="transcriber", provider="ears", model="test-model")


@dataclass
class _RecordingFactory:
    """A TranscriberFactory that records every call and builds a FakeTranscription."""

    calls: list[tuple[str, ProviderConfig, str, SecretStr | None]] = field(default_factory=list)

    def __call__(
        self,
        name: str,
        config: ProviderConfig,
        model: str,
        api_key: SecretStr | None,
        clock: Clock,
    ) -> TranscriptionProvider:
        self.calls.append((name, config, model, api_key))
        return FakeTranscription(name=name, clock=clock)


def _registry(
    kind: ProviderKind = "fake", *, offline: bool = False, **deps: object
) -> ProviderRegistry:
    """Build a registry with one provider, "ears", of `kind`, bound to TRANSCRIBER."""
    config = make_provider_config(kind=kind, base_url=_LOCAL_URL)
    return ProviderRegistry(
        {"ears": config}, [_TRANSCRIBER_ROW], offline=offline, deps=make_registry_deps(None, **deps)
    )


def test_every_provider_kind_either_transcribes_or_is_the_one_that_cannot() -> None:
    factories = default_transcriber_factories()

    assert set(factories) == {"fake", "openai_compat"}
    assert set(get_args(ProviderKind)) - set(factories) == {"anthropic"}


def test_transcriber_binds_a_fake_provider_to_a_fake_transcription() -> None:
    bound = _registry("fake").transcriber()

    assert bound.slot is ModelSlot.TRANSCRIBER
    assert bound.binding == "transcriber"
    assert isinstance(bound.provider, FakeTranscription)
    assert bound.provider.name == "ears"
    assert bound.model == "test-model"


def test_transcriber_hands_its_factory_the_rows_model_and_the_providers_key() -> None:
    factory = _RecordingFactory()
    registry = _registry(
        "openai_compat",
        transcribers={"openai_compat": factory},
        environ={"HIVEMIND_EARS_API_KEY": "sk-test"},
    )

    registry.transcriber()

    name, config, model, api_key = factory.calls[0]
    assert (name, config.base_url, model) == ("ears", _LOCAL_URL, "test-model")
    assert api_key is not None
    assert api_key.get_secret_value() == "sk-test"


async def test_the_default_openai_compat_factory_builds_the_http_adapter() -> None:
    registry = _registry("openai_compat")

    bound = registry.transcriber()

    assert isinstance(bound.provider, OpenAICompatTranscription)
    await registry.aclose()


def test_a_kind_that_cannot_transcribe_is_refused_at_binding_before_any_factory_runs() -> None:
    factory = _RecordingFactory()
    registry = _registry("anthropic", transcribers={"fake": factory})

    with pytest.raises(TranscriptionUnsupportedError) as excinfo:
        registry.transcriber()

    assert (excinfo.value.provider, excinfo.value.kind) == ("ears", "anthropic")
    assert excinfo.value.binding == "transcriber"
    assert "[llm.slots.transcriber]" in str(excinfo.value)
    assert factory.calls == []


def test_a_fallback_that_cannot_transcribe_refuses_the_whole_binding() -> None:
    providers = {
        "ears": make_provider_config(kind="fake"),
        "chat": make_provider_config(kind="anthropic"),
    }
    rows = [
        make_binding(key="transcriber", provider="ears", fallback="chat_ears"),
        make_binding(key="chat_ears", provider="chat"),
    ]
    registry = ProviderRegistry(providers, rows, offline=False, deps=make_registry_deps())

    with pytest.raises(TranscriptionUnsupportedError) as excinfo:
        registry.transcriber()

    assert excinfo.value.binding == "chat_ears"


def test_a_row_naming_no_configured_provider_is_unknown() -> None:
    registry = ProviderRegistry({}, [_TRANSCRIBER_ROW], offline=False, deps=make_registry_deps())

    with pytest.raises(UnknownProviderError):
        registry.transcriber()


def test_offline_mode_refuses_a_remote_transcriber_before_its_factory_runs() -> None:
    factory = _RecordingFactory()
    config = make_provider_config(kind="openai_compat", base_url="https://speech.example.com/v1")
    registry = ProviderRegistry(
        {"ears": config},
        [_TRANSCRIBER_ROW],
        offline=True,
        deps=make_registry_deps(transcribers={"openai_compat": factory}),
    )

    with pytest.raises(OfflineViolationError):
        registry.transcriber()

    assert factory.calls == []


def test_transcription_providers_are_cached_per_provider_and_model() -> None:
    factory = _RecordingFactory()
    providers = {"ears": make_provider_config(kind="fake")}
    rows = [
        make_binding(key="transcriber", provider="ears", model="small", fallback="big_ears"),
        make_binding(key="big_ears", provider="ears", model="large"),
    ]
    deps = make_registry_deps(transcribers={"fake": factory})
    registry = ProviderRegistry(providers, rows, offline=False, deps=deps)

    first = registry.transcriber()
    second = registry.transcriber()

    assert [model for _, _, model, _ in factory.calls] == ["small", "large"]
    assert second.provider is first.provider
    assert first.fallback is not None
    assert first.fallback.provider is not first.provider


def test_the_forage_map_prices_the_transcriber_per_audio_minute() -> None:
    source = make_source(
        provider="ears", model="test-model", cost=ModelCost(cost_per_audio_minute_usd=0.006)
    )
    registry = _registry("fake", map=ForageMap([source], clock=FakeClock()))

    assert registry.transcriber().cost_per_audio_minute_usd == 0.006


async def test_aclose_closes_the_transcribers_it_built_as_well() -> None:
    registry = _registry("fake")
    provider = registry.transcriber().provider

    await registry.aclose()

    assert isinstance(provider, FakeTranscription)
    assert provider.is_closed
    assert registry.transcriber().provider is not provider


# ──────────────────────────────────────────────────────────────────────────────
# The shipped example manifests
# ──────────────────────────────────────────────────────────────────────────────


def test_the_minimal_manifests_transcriber_is_refused_since_it_binds_a_chat_only_kind() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")
    registry = ProviderRegistry(
        provider_configs_from_manifest(manifest),
        bindings_from_manifest(manifest).values(),
        manifest.llm.offline,
        make_registry_deps(),
    )

    with pytest.raises(TranscriptionUnsupportedError):
        registry.transcriber()


@pytest.mark.parametrize("name", ["local.toml", "full.toml"])
async def test_a_manifest_binding_a_local_server_gets_the_http_adapter(name: str) -> None:
    manifest = load_manifest(_MANIFESTS_DIR / name)
    bindings = bindings_from_manifest(manifest)
    registry = ProviderRegistry(
        provider_configs_from_manifest(manifest),
        bindings.values(),
        manifest.llm.offline,
        make_registry_deps(),
    )

    bound = registry.transcriber()

    assert isinstance(bound.provider, OpenAICompatTranscription)
    assert bound.model == bindings["transcriber"].model
    await registry.aclose()
