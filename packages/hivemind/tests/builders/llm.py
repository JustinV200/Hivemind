"""Build valid hivemind.llm test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5: "Builders return real,
validated models; they never bypass validation to save time"), with sensible defaults for every
field a test does not care about. `text_response`/`tool_call_response` are re-exported from
`hivemind.llm.fake` so a test only needs one import line (`from builders.llm import ...`) for
every llm test-data need, scripted `FakeLLMProvider` responses included.

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
"""

from __future__ import annotations

from hivemind.forage.slots import ModelSlot
from hivemind.llm.fake import text_response, tool_call_response
from hivemind.llm.models import (
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    StopReason,
    ToolDefinition,
    Usage,
)

__all__ = [
    "make_request",
    "make_response",
    "make_tool",
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
