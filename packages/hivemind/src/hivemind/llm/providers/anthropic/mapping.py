"""Translate between HiveMind's LLM boundary and the Anthropic Messages API wire.

This is one of two files (with its `streaming.py` sibling) where an Anthropic wire field name
(`system`, `content_block`, `input_schema`, `stop_reason`, `output_config`, ...) is allowed to
appear (codingrules section 8.6: "each adapter has a mapping.py that converts in both directions
and is the only file where vendor field names appear"; codingrules 5.2 lets a module that outgrows
the line limit split into a sibling inside its own package, which is what `streaming.py` is for
the streamed half of this same responsibility -- see that module's docstring). `to_create_params`
builds the request body `messages.create`/`messages.stream` both accept; `to_count_params` builds
the smaller body `messages.count_tokens` accepts; `from_message` builds an `LLMResponse` from a
completed `anthropic.types.Message`, whether that came from `create()` directly or from a
finished stream's `get_final_message()`. Every function here is pure: no I/O, no SDK call, so
`hivemind.llm.providers.anthropic.client` is the only module that actually talks to the network.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.anthropic`.
    Called by `AnthropicProvider` (`provider.py`) for every `complete`, `stream` and
    `count_tokens` call. Calls into `hivemind.llm.models`, `hivemind.llm.capabilities`,
    `hivemind.llm.errors`, `hivemind.forage.slots.Effort` and the `anthropic` SDK's types only.

Key invariants:
    - A request is refused, never silently degraded: an `ImagePart` reaching a provider whose
      `capabilities.vision` is False raises `ProviderRequestError` instead of being dropped and
      sent anyway (a caller that ignores the missing content is worse than one that finds out).
    - A tool's JSON schema always leaves this module with `additionalProperties: false` and a
      `required` list, because `strict: True` (roadmap step 3.6) requires both and a caller's own
      `ToolDefinition.parameters` is not expected to carry them already.
    - `from_message` never raises on a tool call's `input`: the SDK has already parsed it as JSON
      by the time this module sees it (unlike the OpenAI-compatible wire's raw argument string),
      so no `MalformedOutputError` path exists here.

See Also:
    - .claude/codingrules.md section 8.6 for the "one door" / "our types at the boundary" rules
      this module exists to satisfy.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - hivemind.llm.providers.anthropic.streaming for the streamed half of this mapping.
    - hivemind.llm.providers.anthropic.provider for AnthropicProvider, this module's caller.
"""

from __future__ import annotations

from typing import cast

import anthropic.types as at
from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.forage.slots import Effort
from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.models import (
    ContentPart,
    ImagePart,
    JsonObject,
    LLMRequest,
    LLMResponse,
    Message,
    StopReason,
    TextPart,
    ToolCall,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
    Usage,
)

log = get_logger(__name__)

# Effort.LOW/MEDIUM/HIGH -> output_config.effort's own three-level strings (per the SDK).
_EFFORT_WIRE: dict[Effort, str] = {Effort.LOW: "low", Effort.MEDIUM: "medium", Effort.HIGH: "high"}
# Every stop_reason value this adapter has seen, mapped to a StopReason (hivemind.llm.models).
# "pause_turn" only ever comes from a server tool (per the SDK); this adapter declares none, so it
# folds to END_TURN like any other normal stop. "model_context_window_exceeded" is a wire value
# newer than the SDK survey this adapter was built against; it folds to MAX_TOKENS (the closest
# member -- the call ran out of room) rather than growing hivemind.llm.models.StopReason for a
# case only this adapter currently produces.
_STOP_REASON_WIRE: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "max_tokens": StopReason.MAX_TOKENS,
    "tool_use": StopReason.TOOL_USE,
    "stop_sequence": StopReason.STOP_SEQUENCE,
    "refusal": StopReason.REFUSAL,
    "pause_turn": StopReason.END_TURN,
    "model_context_window_exceeded": StopReason.MAX_TOKENS,
}
# Synthesized status for a request this module refuses before any HTTP call is made: no real
# response backs either of these, so there is no genuine status code to report.
MISSING_MODEL_STATUS_CODE = 400  # request.model was None; the call gate must stamp one first.
UNSUPPORTED_CONTENT_STATUS_CODE = 400  # An ImagePart reached a provider with vision=False.

__all__ = ["from_message", "to_count_params", "to_create_params"]


def to_create_params(
    request: LLMRequest, capabilities: ProviderCapabilities, *, provider: str
) -> JsonObject:
    """Build the request body `messages.create`/`messages.stream` both accept.

    Args:
        request: The call to make; `request.model` is the wire model id, stamped by the call
            gate from the BoundModel this request resolved to.
        capabilities: This binding's declared capabilities; gates every optional wire field
            (tools, thinking, output_config, vision) so a reduced-capability configuration is
            never sent something it did not declare.
        provider: The manifest provider name, for a `ProviderRequestError` raised on a missing
            model id or unsupported content.

    Returns:
        A JSON-serialisable body ready for `AnthropicClient.create`/`.stream`.

    Raises:
        ProviderRequestError: `request.model` is None, or `request` carries an `ImagePart` while
            `capabilities.vision` is False.
    """
    model = _require_model(request, provider)
    body: JsonObject = {
        "model": model,
        "max_tokens": request.max_output_tokens,
        "messages": [_message_to_wire(m, capabilities, provider) for m in request.messages],
    }
    if request.system is not None:
        body["system"] = _system_to_wire(request.system)
    if capabilities.native_tool_calls and request.tools:
        body["tools"] = [_tool_definition_to_wire(t) for t in request.tools]
        body["tool_choice"] = {"type": "auto"}
    output_config = _output_config_to_wire(request, capabilities)
    if output_config:
        body["output_config"] = output_config
    if capabilities.reasoning_control:
        body["thinking"] = {"type": "adaptive", "display": "summarized"}
    if request.stop_sequences:
        body["stop_sequences"] = list(request.stop_sequences)
    if request.temperature is not None:
        body["temperature"] = request.temperature
    return body


def to_count_params(
    request: LLMRequest, capabilities: ProviderCapabilities, *, provider: str
) -> JsonObject:
    """Build the smaller body `messages.count_tokens` accepts (no `max_tokens`, no `thinking`).

    Args:
        request: The call whose token count to estimate.
        capabilities: Gates `tools` the same way `to_create_params` does, so the estimate reflects
            what a real call on this binding would actually send.
        provider: The manifest provider name, for a `ProviderRequestError` on a missing model id.

    Returns:
        A JSON-serialisable body ready for `AnthropicClient.count_tokens`.

    Raises:
        ProviderRequestError: `request.model` is None.
    """
    model = _require_model(request, provider)
    params: JsonObject = {
        "model": model,
        "messages": [_message_to_wire(m, capabilities, provider) for m in request.messages],
    }
    if request.system is not None:
        params["system"] = _system_to_wire(request.system)
    if capabilities.native_tool_calls and request.tools:
        params["tools"] = [_tool_definition_to_wire(t) for t in request.tools]
    return params


def from_message(message: at.Message, *, provider: str) -> LLMResponse:
    """Build an `LLMResponse` from a completed `anthropic.types.Message`.

    Args:
        message: The SDK's own parsed response, from `create()` or from a finished stream's
            `get_final_message()`.
        provider: The manifest provider name, for the debug log line a refusal writes.

    Returns:
        The mapped LLMResponse. A `thinking` block's summary becomes `reasoning_summary` and is
        never echoed back onto a later request (episodes are stateless, codingrules section 8.8).
    """
    parts: list[ContentPart] = []
    reasoning_summary: str | None = None
    for block in message.content:
        if block.type == "text":
            parts.append(TextPart(text=block.text))
        elif block.type == "thinking":
            reasoning_summary = block.thinking
        elif block.type == "tool_use":
            parts.append(ToolCallPart(call=_tool_call_from_block(block)))
        # Any other block type (redacted_thinking, a server-tool result, ...) is silently
        # skipped: this adapter declares no server tools (per the SDK), so none should arrive.
    stop_reason = (
        _STOP_REASON_WIRE.get(message.stop_reason, StopReason.END_TURN)
        if message.stop_reason is not None
        else StopReason.END_TURN
    )
    if stop_reason is StopReason.REFUSAL:
        category = message.stop_details.category if message.stop_details is not None else None
        # codingrules 8.6: log at debug and return REFUSAL rather than raising here --
        # the caller (a ladder) decides whether a refusal is fatal for its own retry policy.
        log.debug("llm.anthropic.refusal", provider=provider, category=category)
    return LLMResponse(
        parts=tuple(parts),
        stop_reason=stop_reason,
        usage=_usage_from_wire(message.usage),
        model=message.model,
        reasoning_summary=reasoning_summary,
    )


def _require_model(request: LLMRequest, provider: str) -> str:
    """Return `request.model`, or raise when the call gate never stamped one.

    Raises:
        ProviderRequestError: `request.model` is None.
    """
    if request.model is None:
        raise ProviderRequestError(
            provider,
            MISSING_MODEL_STATUS_CODE,
            error_type="missing_model",
            detail="request.model is None; the call gate must stamp a model id before this "
            "adapter is reached",
        )
    return request.model


def _system_to_wire(system: str) -> list[JsonValue]:
    """Wrap the system prompt as a one-block list carrying the prompt-caching breakpoint.

    The stable prefix on every call through one binding is tools -> system -> messages (per
    the SDK), so the whole system prompt -- never a timestamp or other per-call text -- always
    carries the breakpoint; there is nothing more stable later in the same request to split it
    from.
    """
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _message_to_wire(
    message: Message, capabilities: ProviderCapabilities, provider: str
) -> JsonValue:
    """Map one internal `Message` to one wire message.

    Unlike the OpenAI-compatible wire, a `ToolResultPart` needs no message of its own here: a
    `tool_result` content block lives inline in the same content array as everything else, so
    every internal `Message` maps to exactly one wire message.
    """
    return {
        "role": message.role.value,
        "content": [_content_part_to_block(part, capabilities, provider) for part in message.parts],
    }


def _content_part_to_block(
    part: ContentPart, capabilities: ProviderCapabilities, provider: str
) -> JsonValue:
    """Map one `ContentPart` to one wire content block.

    Raises:
        ProviderRequestError: `part` is an `ImagePart` and `capabilities.vision` is False.
    """
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return _image_part_to_block(part, capabilities, provider)
    if isinstance(part, ToolCallPart):
        return {
            "type": "tool_use",
            "id": part.call.id,
            "name": part.call.name,
            "input": part.call.arguments,
        }
    return _tool_result_to_block(part)


def _image_part_to_block(
    part: ImagePart, capabilities: ProviderCapabilities, provider: str
) -> JsonValue:
    """Map an `ImagePart` to a base64 `image` content block.

    Raises:
        ProviderRequestError: `capabilities.vision` is False -- refuse rather than silently drop
            the image and send a request the caller believes still carries it.
    """
    if not capabilities.vision:
        raise ProviderRequestError(
            provider,
            UNSUPPORTED_CONTENT_STATUS_CODE,
            error_type="unsupported_content",
            detail="this provider's capabilities declare vision=False; an ImagePart was in the "
            "request and cannot be sent",
        )
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": part.media_type, "data": part.data_base64},
    }


def _tool_result_to_block(part: ToolResultPart) -> JsonValue:
    """Map a `ToolResultPart` to a `tool_result` content block."""
    return {
        "type": "tool_result",
        "tool_use_id": part.call_id,
        "content": part.content,
        "is_error": part.is_error,
    }


def _tool_definition_to_wire(tool: ToolDefinition) -> JsonValue:
    """Map a `ToolDefinition` to a `strict: True` tool.

    `strict` mode requires `additionalProperties: false` and a `required` list in the schema
    (per the SDK); this adds both when the caller's own `parameters` schema left them out, rather
    than asking every `ToolDefinition` author to remember to.
    """
    schema = dict(tool.parameters)
    schema.setdefault("additionalProperties", False)
    schema.setdefault("required", [])
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": schema,
        "strict": True,
    }


def _output_config_to_wire(request: LLMRequest, capabilities: ProviderCapabilities) -> JsonObject:
    """Build `output_config`'s `effort` and `format` fields, each behind its own capability.

    `effort` is grouped with `capabilities.reasoning_control` (the knob over how much hidden
    reasoning the model does) rather than sent unconditionally, so `ProviderCapabilities.none()`
    -- a plain-text model with no reasoning knob at all -- gets no `output_config` field it did
    not declare, matching the capability-honesty rule this whole module follows.
    """
    config: JsonObject = {}
    if capabilities.reasoning_control:
        config["effort"] = _EFFORT_WIRE[request.effort]
    if request.response_schema is not None and capabilities.schema_output:
        config["format"] = {"type": "json_schema", "schema": request.response_schema}
    return config


def _tool_call_from_block(block: at.ToolUseBlock) -> ToolCall:
    """Map an SDK `ToolUseBlock` to a `ToolCall`.

    Unlike the OpenAI-compatible wire's raw JSON argument string, `block.input` is already
    parsed by the SDK's own decoder; the cast below only satisfies mypy's invariant-dict check
    (`ToolCall.arguments` re-validates the value at construction regardless).
    """
    return ToolCall(id=block.id, name=block.name, arguments=cast(JsonObject, dict(block.input)))


def _usage_from_wire(usage: at.Usage) -> Usage:
    """Map the SDK's `Usage` to ours; a cache read becomes `cached_tokens`, cost stays unpriced."""
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cache_read_input_tokens or 0,
        cost_usd=None,  # This adapter never prices a call; BoundModel's cost fields do that.
    )
