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
`docs/manifests/*.toml`. Roadmap step 3.21 shipped that conversion for real as
`hivemind.cli.stores.slot_bindings`/`provider_configs`; both functions here are now thin
dict-shaped wrappers around those, kept so existing call sites do not all need to switch from a
dict lookup to a tuple.
`judge_approve_response` answers a JUDGE-slot review with APPROVE, for a fake that a Warden's
model-backed judge shares with the rest of a script.

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
    - hivemind.cli.stores for slot_bindings and provider_configs, the production functions
      bindings_from_manifest/provider_configs_from_manifest now wrap.
"""

from __future__ import annotations

import dataclasses
import json

from hivemind.cli.stores import provider_configs, slot_bindings
from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.embedding import BoundEmbedder, EmbeddingRequest, EmbeddingResponse, FakeEmbedding
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

_EMBED_MODEL_ID = "test-embed-model"  # A neutral embedding model id (codingrules 8.6).

__all__ = [
    "bindings_from_manifest",
    "judge_approve_response",
    "make_binding",
    "make_bound",
    "make_bound_embedder",
    "make_embed_request",
    "make_embed_response",
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


def judge_approve_response(request: LLMRequest) -> LLMResponse:
    """Answer a JUDGE-slot review call with an unconditional APPROVE, on whichever rung.

    A Warden built with a real `hivemind.wardens.judge.ModelJudgeReviewer` over a scripted
    `FakeLLMProvider` sends its reviews to that same fake, so a test's fake must answer
    `ModelSlot.JUDGE` requests too, in the shape `hivemind.wardens.judge._JudgeModelOutput`
    expects, or the gate's JUDGE check would exhaust its ladder and fail closed at every
    `judge = true` tier. The schema travels with a NATIVE or JSON_MODE request; a PROMPTED one
    expects one fenced block.
    """
    verdict = json.dumps({"outcome": "APPROVE", "reasons": [], "notes": ""})
    if request.response_schema is not None:
        return text_response(verdict)
    return text_response(f"```json\n{verdict}\n```")


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


def make_embed_request(**overrides: object) -> EmbeddingRequest:
    """Build a valid EmbeddingRequest, filling in every required field with a plain default.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated EmbeddingRequest carrying one short text.
    """
    fields: dict[str, object] = {"texts": ("hello world",)}
    fields.update(overrides)
    return EmbeddingRequest(**fields)


def make_embed_response(**overrides: object) -> EmbeddingResponse:
    """Build a valid EmbeddingResponse, filling in every required field with a plain default.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated EmbeddingResponse: one 4-dimension vector, a small nonzero Usage.
    """
    fields: dict[str, object] = {
        "vectors": ((0.5, 0.5, 0.5, 0.5),),
        "model": "test-embed-model",
        "dimensions": 4,
        "usage": Usage(input_tokens=3, output_tokens=0),
    }
    fields.update(overrides)
    return EmbeddingResponse(**fields)


def make_bound_embedder(**overrides: object) -> BoundEmbedder:
    """Build a valid BoundEmbedder over a fresh FakeEmbedding, for the Fanner/gate tests to call.

    `**overrides: object` mirrors `make_bound`'s own reasoning (codingrules 5.1's parameter
    limit, `dataclasses.replace`'s keyword typing).

    Args:
        **overrides: Field values that replace the defaults below, most commonly `provider` (to
            script or inspect it) and `fallback` (to build a chain).

    Returns:
        A validated BoundEmbedder bound to `ModelSlot.EMBEDDER` on a fresh FakeEmbedding.
    """
    # The default provider reports the binding's own model id, as every registry-built adapter
    # does (ADR-0036); a caller overriding `model` alone gets a matching fake the same way.
    model = str(overrides.get("model", _EMBED_MODEL_ID))
    base = BoundEmbedder(
        slot=ModelSlot.EMBEDDER,
        binding="embedder",
        provider=FakeEmbedding(clock=FakeClock(), model=model),
        model=model,
        cost_per_million_input_usd=0.5,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


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

    Roadmap step 3.21 shipped this conversion for real as `hivemind.cli.stores.slot_bindings`
    (`hivemind.llm` still cannot import `hivemind.manifest` itself, codingrules section 4); this
    wrapper only re-keys that production function's tuple by its own `.key`, for the tests here
    that want a dict lookup rather than a fallback chain to walk.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        Every `[llm.slots]` row, keyed by its own manifest key, as a forage `SlotBinding`.
    """
    return {binding.key: binding for binding in slot_bindings(manifest)}


def provider_configs_from_manifest(manifest: HiveManifest) -> dict[str, ProviderConfig]:
    """Convert a loaded HiveManifest's `[llm.providers]` table into ProviderConfig rows.

    A thin `dict`-returning wrapper around the production `hivemind.cli.stores.provider_configs`
    (roadmap step 3.21), kept here only so existing tests that already expect a concrete `dict`
    do not all need updating to accept a `Mapping`.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        Every `[llm.providers]` row, keyed by its own manifest name, as a ProviderConfig.
    """
    return dict(provider_configs(manifest))
