"""Tests for hivemind.manifest.schema.llm.providers: capability overrides, rows and bindings.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/llm/providers.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.llm.providers for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.forage import Effort
from hivemind.manifest.schema.llm import (
    IN_PROCESS_KINDS,
    CapabilityOverrides,
    EmbeddingOptions,
    ProviderSpec,
    SlotBinding,
)


def test_capability_overrides_default_to_none() -> None:
    overrides = CapabilityOverrides()

    assert overrides.as_overrides() == {}


def test_capability_overrides_as_overrides_returns_only_set_fields() -> None:
    overrides = CapabilityOverrides(native_tool_calls=False, context_window=8192)

    assert overrides.as_overrides() == {"native_tool_calls": False, "context_window": 8192}


def test_capability_overrides_carry_an_audio_override_when_set() -> None:
    assert CapabilityOverrides(audio=True).as_overrides() == {"audio": True}


def test_provider_spec_defaults_to_hosted_with_no_key_override() -> None:
    spec = ProviderSpec(kind="anthropic")

    assert spec.base_url == ""
    assert spec.api_key_env is None
    assert spec.capabilities.as_overrides() == {}


def test_provider_spec_rejects_an_api_key_env_shaped_like_a_literal_secret() -> None:
    with pytest.raises(ValidationError):
        ProviderSpec(kind="anthropic", api_key_env="sk-ant-not-a-variable-name")


def test_provider_spec_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        ProviderSpec(kind="does_not_exist")


def test_provider_spec_accepts_the_sentence_transformers_kind() -> None:
    spec = ProviderSpec(kind="sentence_transformers")

    assert spec.kind == "sentence_transformers"


def test_provider_spec_defaults_to_an_empty_embedding_options() -> None:
    spec = ProviderSpec(kind="fake")

    assert spec.embedding == EmbeddingOptions()
    assert spec.embedding.batch_size is None
    assert spec.embedding.device is None
    assert spec.embedding.local_files_only is False


def test_embedding_options_round_trips_and_is_frozen() -> None:
    options = EmbeddingOptions(batch_size=16, device="cuda", local_files_only=True)

    restored = EmbeddingOptions.model_validate_json(options.model_dump_json())

    assert restored == options
    with pytest.raises(ValidationError, match="frozen"):
        options.batch_size = 1  # The assignment is the test.


def test_embedding_options_rejects_a_non_positive_batch_size() -> None:
    with pytest.raises(ValidationError):
        EmbeddingOptions(batch_size=0)


def test_slot_binding_defaults_to_medium_effort_and_no_fallback() -> None:
    binding = SlotBinding(provider="anthropic", model="test-model")

    assert binding.effort is Effort.MEDIUM
    assert binding.fallback is None
    assert binding.max_output_tokens is None


def test_provider_spec_accepts_whisper_local_with_no_base_url() -> None:
    spec = ProviderSpec(kind="whisper_local")

    assert spec.kind in IN_PROCESS_KINDS
    assert spec.base_url == ""


def test_provider_spec_refuses_a_base_url_on_an_in_process_kind() -> None:
    with pytest.raises(ValidationError, match="runs in process"):
        ProviderSpec(kind="whisper_local", base_url="http://127.0.0.1:9000/v1")


def test_provider_spec_carries_a_prompt_overlay_name_and_rejects_a_path() -> None:
    # Roadmap 8.4: the overlay is named like any manifest key, so it can never escape overlays/.
    assert ProviderSpec(kind="anthropic").prompt_overlay is None
    assert (
        ProviderSpec(kind="anthropic", prompt_overlay="small_local").prompt_overlay == "small_local"
    )
    with pytest.raises(ValidationError):
        ProviderSpec(kind="anthropic", prompt_overlay="../escape")
