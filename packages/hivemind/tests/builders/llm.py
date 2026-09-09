"""Build valid hivemind.llm test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5: "Builders return real,
validated models; they never bypass validation to save time"), with sensible defaults for every
field a test does not care about. `text_response`/`tool_call_response` are re-exported from
`hivemind.llm.fake` so a test only needs one import line (`from builders.llm import ...`) for
every llm test-data need, scripted `FakeLLMProvider` responses included. `make_bound` (roadmap
step 3.5) builds a `BoundModel` over a `FakeLLMProvider`, since `hivemind.llm.slots.resolve`
(roadmap step 3.4, a parallel dispatch) is not what the ladder tests need: they need a live
binding to script and inspect, not a manifest to resolve one from.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/llm.

Key invariants:
    - Every builder's model name and tool/slot defaults are neutral test values ("test-model",
      "test_tool"), never a real provider's model id (codingrules section 8.6's phase-3 brief:
      "Model names in tests are neutral strings").

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.llm.models for LLMRequest, LLMResponse, ToolDefinition and their fields.
    - hivemind.llm.fake for text_response and tool_call_response.
    - hivemind.llm.slots for BoundModel, the value make_bound builds.
"""

from __future__ import annotations

import dataclasses

from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.fake import FakeLLMProvider, text_response, tool_call_response
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
from hivemind.llm.slots import BoundModel

__all__ = [
    "make_bound",
    "make_request",
    "make_response",
    "make_tool",
    "make_tool_call",
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
