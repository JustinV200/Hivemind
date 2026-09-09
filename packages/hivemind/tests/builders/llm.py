"""Build valid hivemind.llm test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5: "Builders return real,
validated models; they never bypass validation to save time"), with sensible defaults for every
field a test does not care about. `text_response`/`tool_call_response` are re-exported from
`hivemind.llm.fake` so a test only needs one import line (`from builders.llm import ...`) for
every llm test-data need, scripted `FakeLLMProvider` responses included. `make_bound` (roadmap
step 3.5) builds a `BoundModel` over a `FakeLLMProvider`, since `hivemind.llm.slots.resolve` is
not what the ladder tests need: they need a live binding to script and inspect, not a chain to
resolve one from. `make_fanner_deps` (roadmap step 3.12a) builds a `FannerDeps` over an empty
`ForageMap`, sharing one clock between the two so a test's own `FakeClock.advance()` calls affect
both. `make_binding`, `make_provider_config` and `make_registry_deps` (roadmap step 3.4) build the
forage-side and registry-side inputs `resolve`/`resolve_key`/`ProviderRegistry` take instead of
the manifest's own `LlmSection`/`ProviderSpec` (`hivemind.llm` may not import `hivemind.manifest`,
codingrules section 4); `bindings_from_manifest`/`provider_configs_from_manifest` convert a real,
loaded `HiveManifest` into those same inputs, for the tests that resolve against
`docs/manifests/*.toml` the way the CLI composition root (roadmap step 3.21) eventually will.
Test code is not bound by that layering rule (only `hivemind.llm`'s own source is), so this
module is a safe place for the conversion to live.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/llm.

Key invariants:
    - Every builder's model name and tool/slot defaults are neutral test values ("test-model",
      "test_tool"), never a real provider's model id (codingrules section 8.6: a model id in code
      outside `manifest/`/`docs/` is a lint failure, so test fixtures use a neutral name too).

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.llm.models for LLMRequest, LLMResponse, ToolDefinition and their fields.
    - hivemind.llm.fake for text_response and tool_call_response.
    - hivemind.llm.slots for BoundModel and resolve/resolve_key, the values these builders feed.
    - hivemind.llm.registry for ProviderRegistry, ProviderConfig and RegistryDeps.
    - hivemind.llm.fanner for FannerDeps, the value make_fanner_deps builds.
"""

from __future__ import annotations

import dataclasses

from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.fake import FakeLLMProvider, text_response, tool_call_response
from hivemind.llm.fanner import FannerDeps, NullLlmEventRecorder
from hivemind.llm.models import (
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    StopReason,
    ToolCall,
    ToolDefinition,
    Usage,
)
from hivemind.llm.registry import ProviderConfig, RegistryDeps, default_factories
from hivemind.llm.slots import BoundModel
from hivemind.manifest import HiveManifest
from waggle.clock import Clock, FakeClock

__all__ = [
    "bindings_from_manifest",
    "make_binding",
    "make_bound",
    "make_fanner_deps",
    "make_provider_config",
    "make_registry_deps",
    "make_request",
    "make_response",
    "make_tool",
    "make_tool_call",
    "provider_configs_from_manifest",
    "text_response",
    "tool_call_response",
]


def make_tool(**overrides: object) -> ToolDefinition:
    """Build a valid ToolDefinition, filling in every required field with a plain default.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated ToolDefinition.
    """
    fields: dict[str, object] = {
        "name": "test_tool",
        "description": "A tool used only by tests.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }
    fields.update(overrides)
    return ToolDefinition(**fields)


def make_request(**overrides: object) -> LLMRequest:
    """Build a valid LLMRequest, filling in every required field with a plain default.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated LLMRequest: one user text turn, on ModelSlot.WORKER, no tools.
    """
    fields: dict[str, object] = {
        "slot": ModelSlot.WORKER,
        "messages": (Message.text(Role.USER, "Say hello."),),
        "max_output_tokens": 1024,
    }
    fields.update(overrides)
    return LLMRequest(**fields)


def make_response(**overrides: object) -> LLMResponse:
    """Build a valid LLMResponse, filling in every required field with a plain default.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated LLMResponse: one text part, END_TURN, a small nonzero Usage.
    """
    fields: dict[str, object] = {
        "parts": (Message.text(Role.ASSISTANT, "Hello.").parts[0],),
        "stop_reason": StopReason.END_TURN,
        "usage": Usage(input_tokens=10, output_tokens=5),
        "model": "test-model",
    }
    fields.update(overrides)
    return LLMResponse(**fields)


def make_tool_call(**overrides: object) -> ToolCall:
    """Build a valid ToolCall, filling in every required field with a plain default.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated ToolCall naming `make_tool`'s default tool, with no arguments.
    """
    fields: dict[str, object] = {"id": "call_1", "name": "test_tool", "arguments": {}}
    fields.update(overrides)
    return ToolCall(**fields)


def make_bound(**overrides: object) -> BoundModel:
    """Build a valid BoundModel over a fresh FakeLLMProvider, for the degradation ladders to call.

    `**overrides: object` (rather than one named parameter per field) keeps this a
    one-parameter function under codingrules 5.1's parameter limit; mypy's `dataclasses.replace`
    special-casing checks each keyword against `BoundModel`'s real field types, which a
    `dict[str, object]` spread can never satisfy statically, so the one broad ignore below covers
    a call every test in this module's callers makes correctly at runtime.

    Args:
        **overrides: Field values that replace the defaults below, most commonly `provider` (to
            script responses on it) and `fallback` (to build a chain).

    Returns:
        A validated BoundModel bound to `ModelSlot.WORKER` on a full-capability FakeLLMProvider.
    """
    base = BoundModel(
        slot=ModelSlot.WORKER,
        binding="worker",
        provider=FakeLLMProvider(),
        model="test-model",
        effort=Effort.MEDIUM,
        context_window=128_000,
        cost_per_million_input_usd=1.5,
        cost_per_million_output_usd=7.5,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def make_fanner_deps(clock: Clock | None = None, **overrides: object) -> FannerDeps:
    """Build a valid FannerDeps: an empty ForageMap, no seat/rate-limit overrides, nothing recorded.

    Args:
        clock: Shared by the built ForageMap and the returned FannerDeps itself, so a test's own
            `FakeClock.advance()` calls affect both; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below, including `clock` itself.

    Returns:
        A FannerDeps with no map sources, every provider defaulting to DEFAULT_SEATS and no rate
        limit, and a NullLlmEventRecorder.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "map": ForageMap([], clock=active_clock),
        "seats": {},
        "limits": {},
        "clock": active_clock,
        "recorder": NullLlmEventRecorder(),
    }
    fields.update(overrides)
    return FannerDeps(**fields)  # type: ignore[arg-type]


def make_binding(**overrides: object) -> SlotBinding:
    """Build a valid forage-side SlotBinding, filling in every required field with a plain default.

    The row `hivemind.llm.slots.resolve`/`resolve_key` walk (see that module's docstring for why
    this is `hivemind.forage.map.SlotBinding`, not the manifest's own row shape).

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated SlotBinding: key/provider "worker"/"fake", model "test-model", no fallback.
    """
    fields: dict[str, object] = {
        "key": "worker",
        "provider": "fake",
        "model": "test-model",
        "fallback": None,
        "effort": Effort.MEDIUM,
    }
    fields.update(overrides)
    return SlotBinding(**fields)


def make_provider_config(**overrides: object) -> ProviderConfig:
    """Build a valid ProviderConfig, filling in every field with a plain default.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A ProviderConfig for a `kind="fake"` provider with no capability overrides.
    """
    fields: dict[str, object] = {
        "kind": "fake",
        "base_url": "",
        "api_key_env": None,
        "timeout_s": 120.0,
        "capability_overrides": {},
        "default_model": None,
    }
    fields.update(overrides)
    return ProviderConfig(**fields)  # type: ignore[arg-type]


def make_registry_deps(clock: Clock | None = None, **overrides: object) -> RegistryDeps:
    """Build a valid RegistryDeps: the default factories, an empty environ, no Forage map.

    Args:
        clock: The registry deps' own clock; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below, including `clock` itself.

    Returns:
        A RegistryDeps a ProviderRegistry can be built with directly.
    """
    fields: dict[str, object] = {
        "factories": default_factories(),
        "environ": {},
        "clock": clock if clock is not None else FakeClock(),
        "map": None,
    }
    fields.update(overrides)
    return RegistryDeps(**fields)  # type: ignore[arg-type]


def bindings_from_manifest(manifest: HiveManifest) -> dict[str, SlotBinding]:
    """Convert a loaded HiveManifest's `[llm.slots]` table into forage-side SlotBinding rows.

    A preview of the conversion the CLI composition root (roadmap step 3.21) will do once, since
    `hivemind.llm` cannot import `hivemind.manifest` (codingrules section 4) to do it itself.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        Every `[llm.slots]` row, keyed by its own manifest key, as a forage `SlotBinding`.
    """
    return {
        key: SlotBinding(
            key=key,
            provider=row.provider,
            model=row.model,
            fallback=row.fallback,
            effort=row.effort,
        )
        for key, row in manifest.llm.slots.items()
    }


def provider_configs_from_manifest(manifest: HiveManifest) -> dict[str, ProviderConfig]:
    """Convert a loaded HiveManifest's `[llm.providers]` table into ProviderConfig rows.

    `ProviderConfig.default_model` (needed only by an `openai_compat` provider's config, which has
    no manifest-level default of its own) is derived as the model of that provider's first
    `[llm.slots]` row -- a reasonable default for a manifest with one local model per server, and
    exactly what every shipped example manifest (`docs/manifests/*.toml`) has.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        Every `[llm.providers]` row, keyed by its own manifest name, as a ProviderConfig.
    """
    default_models = _first_model_by_provider(manifest)
    return {
        name: ProviderConfig(
            kind=spec.kind,
            base_url=spec.base_url,
            api_key_env=spec.api_key_env,
            timeout_s=spec.timeout_s,
            capability_overrides=spec.capabilities.as_overrides(),
            default_model=default_models.get(name),
        )
        for name, spec in manifest.llm.providers.items()
    }


def _first_model_by_provider(manifest: HiveManifest) -> dict[str, str]:
    """Return the first `[llm.slots]` row's model id seen for each provider name, in table order."""
    defaults: dict[str, str] = {}
    for row in manifest.llm.slots.values():
        defaults.setdefault(row.provider, row.model)
    return defaults
