"""Tests for hivemind.manifest.schema.llm: CapabilityOverrides through LlmSection's validators.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/llm.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.llm for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.forage import Effort, ModelSlot
from hivemind.manifest.schema.llm import CapabilityOverrides, LlmSection, ProviderSpec, SlotBinding

# Every ModelSlot bound to one provider, the minimum LlmSection.slots that passes the
# completeness validator; individual tests mutate a copy of this.
_ALL_SLOTS_ON_ONE_PROVIDER = {
    slot.manifest_key: {"provider": "anthropic", "model": "test-model"} for slot in ModelSlot
}


def _providers(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"anthropic": {"kind": "anthropic"}}
    base.update(overrides)
    return base


def test_capability_overrides_default_to_none() -> None:
    overrides = CapabilityOverrides()

    assert overrides.as_overrides() == {}


def test_capability_overrides_as_overrides_returns_only_set_fields() -> None:
    overrides = CapabilityOverrides(native_tool_calls=False, context_window=8192)

    assert overrides.as_overrides() == {"native_tool_calls": False, "context_window": 8192}


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


def test_slot_binding_defaults_to_medium_effort_and_no_fallback() -> None:
    binding = SlotBinding(provider="anthropic", model="test-model")

    assert binding.effort is Effort.MEDIUM
    assert binding.fallback is None
    assert binding.max_output_tokens is None


def test_llm_section_accepts_every_slot_bound_to_one_provider() -> None:
    section = LlmSection(providers=_providers(), slots=_ALL_SLOTS_ON_ONE_PROVIDER)

    assert set(section.slots) == {slot.manifest_key for slot in ModelSlot}


def test_llm_section_rejects_a_missing_slot() -> None:
    slots = dict(_ALL_SLOTS_ON_ONE_PROVIDER)
    del slots["judge"]

    with pytest.raises(ValidationError, match="judge"):
        LlmSection(providers=_providers(), slots=slots)


def test_llm_section_rejects_a_binding_with_an_unknown_provider() -> None:
    slots = dict(_ALL_SLOTS_ON_ONE_PROVIDER)
    slots["worker"] = {"provider": "does_not_exist", "model": "test-model"}

    with pytest.raises(ValidationError, match="does_not_exist"):
        LlmSection(providers=_providers(), slots=slots)


def test_llm_section_rejects_a_fallback_naming_a_missing_key() -> None:
    slots = dict(_ALL_SLOTS_ON_ONE_PROVIDER)
    slots["worker"] = {
        "provider": "anthropic",
        "model": "test-model",
        "fallback": "no_such_binding",
    }

    with pytest.raises(ValidationError, match="no_such_binding"):
        LlmSection(providers=_providers(), slots=slots)


def test_llm_section_rejects_a_fallback_cycle() -> None:
    slots = dict(_ALL_SLOTS_ON_ONE_PROVIDER)
    slots["worker"] = {"provider": "anthropic", "model": "test-model", "fallback": "ripener"}
    slots["ripener"] = {"provider": "anthropic", "model": "test-model", "fallback": "worker"}

    with pytest.raises(ValidationError, match="cycles"):
        LlmSection(providers=_providers(), slots=slots)


def test_llm_section_accepts_a_named_binding_used_only_as_a_fallback_target() -> None:
    slots = dict(_ALL_SLOTS_ON_ONE_PROVIDER)
    slots["worker"] = {"provider": "anthropic", "model": "test-model", "fallback": "local_worker"}
    slots["local_worker"] = {"provider": "anthropic", "model": "test-model"}

    section = LlmSection(providers=_providers(), slots=slots)

    assert "local_worker" in section.slots


def test_llm_section_rejects_a_mixed_case_slot_key() -> None:
    slots = dict(_ALL_SLOTS_ON_ONE_PROVIDER)
    slots["Queen"] = slots.pop("queen")

    with pytest.raises(ValidationError):
        LlmSection(providers=_providers(), slots=slots)


def test_llm_section_offline_accepts_a_loopback_provider() -> None:
    slots = {slot.manifest_key: {"provider": "local", "model": "test-model"} for slot in ModelSlot}
    providers = {
        "local": {"kind": "openai_compat", "base_url": "http://127.0.0.1:11434/v1"},
    }

    section = LlmSection(offline=True, providers=providers, slots=slots)

    assert section.offline is True


def test_llm_section_offline_rejects_a_hosted_provider() -> None:
    with pytest.raises(ValidationError, match="not provably local"):
        LlmSection(offline=True, providers=_providers(), slots=_ALL_SLOTS_ON_ONE_PROVIDER)


def test_llm_section_offline_rejects_a_non_loopback_provider() -> None:
    slots = {slot.manifest_key: {"provider": "remote", "model": "test-model"} for slot in ModelSlot}
    providers = {
        "remote": {"kind": "openai_compat", "base_url": "http://example.org:11434/v1"},
    }

    with pytest.raises(ValidationError, match="not provably local"):
        LlmSection(offline=True, providers=providers, slots=slots)


def test_llm_section_is_frozen_and_forbids_extras() -> None:
    section = LlmSection(providers=_providers(), slots=_ALL_SLOTS_ON_ONE_PROVIDER)

    with pytest.raises(ValidationError, match="frozen"):
        section.offline = True  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        LlmSection.model_validate({**section.model_dump(), "nope": 1})
