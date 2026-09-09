"""Translate between HiveMind's LLM boundary and the OpenAI-compatible chat-completions wire.

This is the ONE file in the whole workspace where an OpenAI wire field name (`messages`,
`tool_calls`, `finish_reason`, `delta`, ...) is allowed to appear (codingrules section 8.6: "each
adapter has a mapping.py that converts in both directions and is the only file where vendor field
names appear"). `request_to_json` builds a `/chat/completions` request body from an `LLMRequest`;
`response_from_json` builds an `LLMResponse` from a non-streaming reply; `StreamState` accumulates
a streamed reply's chunks (OpenAI-compatible servers split a tool call's arguments across many
SSE events, all sharing one integer `index`, so nothing about a streamed tool call is complete
until the event that also carries `finish_reason` arrives). Every function here is pure: no I/O,
no httpx, so `hivemind.llm.providers.openai_compat.client` and `.provider` are the only callers.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.
    openai_compat`. Called by `OpenAICompatProvider` (`provider.py`) for every `complete` and
    `stream` call. Calls into `hivemind.llm.models`, `hivemind.llm.capabilities`,
    `hivemind.llm.errors` and `hivemind.forage.slots.Effort` only.

Key invariants:
    - A request is refused, never silently degraded: an `ImagePart` reaching a provider whose
      `capabilities.vision` is False raises `ProviderRequestError` rather than being dropped and
      sent anyway (a caller that ignores the missing content is worse than one that finds out).
    - `response_from_json`/`StreamState` raise `MalformedOutputError`, never a bare
      `json.JSONDecodeError` or `KeyError`, when a tool call's `arguments` string does not parse:
      the ladders (`hivemind.llm.ladders`) catch `LLMError`, not stdlib exceptions.
    - `StreamState` is single-use: one instance per `stream()` call, never shared across requests.

See Also:
    - .claude/codingrules.md section 8.6 for the "one door" / "our types at the boundary" rules
      this module exists to satisfy.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - hivemind.llm.providers.openai_compat.provider for OpenAICompatProvider, this module's caller.
    - hivemind.llm.providers.openai_compat.client for the HTTP layer that carries these bodies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import JsonValue

from hivemind.forage.slots import Effort
from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import MalformedOutputError, ProviderRequestError
from hivemind.llm.models import (
    ContentPart,
    ImagePart,
    JsonObject,
    LLMChunk,
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

# Effort.MEDIUM/LOW/HIGH -> the wire's own three-level `reasoning_effort` strings.
_REASONING_EFFORT_WIRE: dict[Effort, str] = {
    Effort.LOW: "low",
    Effort.MEDIUM: "medium",
    Effort.HIGH: "high",
}
# The wire's four finish reasons this adapter recognises -> our StopReason; anything else (a
# server-specific extension) falls back to END_TURN rather than raising, since a stop reason we
# do not recognise is still better handled as "it stopped" than as a hard failure.
_FINISH_REASON_TO_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "length": StopReason.MAX_TOKENS,
    "tool_calls": StopReason.TOOL_USE,
    "content_filter": StopReason.REFUSAL,
}

__all__ = ["StreamState", "request_to_json", "response_from_json"]


def _as_dict(value: JsonValue | None) -> dict[str, JsonValue]:
    """Return `value` unchanged if it is a JSON object, else an empty one.

    Used everywhere this module reads a nested wire object defensively: a server's reply is
    untrusted input (codingrules section 15), so a field that is missing or the wrong shape
    becomes "empty" rather than an `AttributeError`/`KeyError` crashing the mapping.
    """
    return value if isinstance(value, dict) else {}


def _as_list(value: JsonValue | None) -> list[JsonValue]:
    """Return `value` unchanged if it is a JSON array, else an empty one. See `_as_dict`."""
    return value if isinstance(value, list) else []


# ──────────────────────────────────────────────────────────────────────────────
# Request mapping: LLMRequest -> chat-completions JSON body
# ──────────────────────────────────────────────────────────────────────────────


def request_to_json(
    request: LLMRequest, *, model: str, capabilities: ProviderCapabilities, provider: str
) -> JsonObject:
    """Build a `/chat/completions` request body from `request`.

    Args:
        request: The call to make.
        model: The wire `model` field: `request.model` when the call gate stamped one from the
            binding, else this adapter's configured default (`OpenAICompatConfig.model`).
        capabilities: The provider's declared capabilities; gates every optional wire field so a
            weak local model is never sent something it cannot honour.
        provider: The manifest provider name, for a `ProviderRequestError` raised on unsupported
            content (an image on a `vision=False` provider).

    Returns:
        A JSON-serialisable body ready for `client.post_json`/`client.stream_sse`.

    Raises:
        ProviderRequestError: `request` carries an `ImagePart` but `capabilities.vision` is False.
    """
    body: dict[str, JsonValue] = {
        "model": model,
        "messages": _messages_to_wire(request, capabilities, provider),
        "max_tokens": request.max_output_tokens,
    }
    if request.tools:
        body["tools"] = [_tool_definition_to_wire(tool) for tool in request.tools]
        # A prompted-only model would misread an unsolicited tool_choice hint, so only send it
        # when the provider actually has a native tool-call protocol to honour it with.
        if capabilities.native_tool_calls:
            body["tool_choice"] = "auto"
    response_format = _response_format_to_wire(request, capabilities)
    if response_format is not None:
        body["response_format"] = response_format
    if capabilities.reasoning_control:
        body["reasoning_effort"] = _REASONING_EFFORT_WIRE[request.effort]
    if request.stop_sequences:
        stop_list: list[JsonValue] = list(request.stop_sequences)
        body["stop"] = stop_list
    if request.temperature is not None:
        body["temperature"] = request.temperature
    return body


def _messages_to_wire(
    request: LLMRequest, capabilities: ProviderCapabilities, provider: str
) -> list[JsonValue]:
    """Flatten every `Message` into one or more wire messages, folding in the system prompt.

    A `system_role`-capable server gets a dedicated `role: "system"` message first; otherwise the
    system text is prepended to the first message's own text content (this adapter's brief:
    "the system text prepended to the first user message"), since every request the ladders build
    today opens with a user turn.
    """
    wire: list[JsonValue] = []
    leading_system = request.system
    if leading_system is not None and capabilities.system_role:
        wire.append({"role": "system", "content": leading_system})
        leading_system = None
    for index, message in enumerate(request.messages):
        prefix = leading_system if index == 0 and leading_system is not None else None
        wire.extend(_message_to_wire(message, capabilities, provider, text_prefix=prefix))
    return wire


def _message_to_wire(
    message: Message,
    capabilities: ProviderCapabilities,
    provider: str,
    *,
    text_prefix: str | None,
) -> list[JsonValue]:
    """Map one internal `Message` to one or more wire messages.

    A `ToolResultPart` becomes its own `role: "tool"` wire message: OpenAI's wire has no concept
    of a tool result living inside another turn's content array. Text, images and the model's own
    tool calls all stay on one wire message for this turn.
    """
    content_parts: list[JsonValue] = []
    tool_calls: list[JsonValue] = []
    tool_results: list[JsonValue] = []
    if text_prefix:
        content_parts.append({"type": "text", "text": text_prefix})
    for part in message.parts:
        if isinstance(part, TextPart):
            content_parts.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            content_parts.append(_image_part_to_wire(part, capabilities, provider))
        elif isinstance(part, ToolCallPart):
            tool_calls.append(_tool_call_to_wire(part.call))
        elif isinstance(part, ToolResultPart):
            tool_results.append(_tool_result_to_wire(part))
    wire: list[JsonValue] = []
    if content_parts or tool_calls:
        turn: dict[str, JsonValue] = {"role": message.role.value}
        if content_parts:
            turn["content"] = content_parts
        if tool_calls:
            turn["tool_calls"] = tool_calls
        wire.append(turn)
    wire.extend(tool_results)
    return wire


def _image_part_to_wire(
    part: ImagePart, capabilities: ProviderCapabilities, provider: str
) -> JsonValue:
    """Map an `ImagePart` to an `image_url` data-URL content part.

    Raises:
        ProviderRequestError: `capabilities.vision` is False -- refuse rather than silently drop
            the image and send a request the caller believes still carries it.
    """
    if not capabilities.vision:
        raise ProviderRequestError(
            provider,
            status_code=400,
            error_type="unsupported_content",
            detail="this provider's capabilities declare vision=False; an ImagePart was in the "
            "request and cannot be sent",
        )
    data_url = f"data:{part.media_type};base64,{part.data_base64}"
    return {"type": "image_url", "image_url": {"url": data_url}}


def _tool_call_to_wire(call: ToolCall) -> JsonValue:
    """Map a model-issued `ToolCall` (echoed back on a replayed assistant turn) to wire shape."""
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
    }


def _tool_result_to_wire(part: ToolResultPart) -> JsonValue:
    """Map a `ToolResultPart` to a `role: "tool"` wire message.

    The wire has no `is_error` flag on a tool message; folding it into the text keeps the model
    aware of the failure instead of it disappearing at the boundary.
    """
    content = f"ERROR: {part.content}" if part.is_error else part.content
    return {"role": "tool", "tool_call_id": part.call_id, "content": content}


def _tool_definition_to_wire(tool: ToolDefinition) -> JsonValue:
    """Map a `ToolDefinition` to the wire's `{"type": "function", "function": {...}}` shape."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _response_format_to_wire(
    request: LLMRequest, capabilities: ProviderCapabilities
) -> JsonValue | None:
    """Choose `response_format` per the schema/json-mode/none ladder of ADR-0009.

    Native schema enforcement first, then plain JSON mode, then nothing (a prompted-only model
    gets no wire hint at all; `hivemind.llm.ladders` carries that rung instead).
    """
    if request.response_schema is None:
        return None
    if capabilities.schema_output:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": request.response_schema,
                "strict": True,
            },
        }
    if capabilities.json_mode:
        return {"type": "json_object"}
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Response mapping: chat-completions JSON -> LLMResponse (non-streaming)
# ──────────────────────────────────────────────────────────────────────────────


def response_from_json(payload: JsonObject, *, provider: str) -> LLMResponse:
    """Build an `LLMResponse` from a non-streaming `/chat/completions` reply.

    Args:
        payload: The parsed JSON body `client.post_json` returned.
        provider: The manifest provider name, for a `MalformedOutputError` raised on a reply with
            no `choices` or an unparseable tool-call `arguments` string.

    Returns:
        The mapped LLMResponse.

    Raises:
        MalformedOutputError: `payload` has no `choices`, or a tool call's `arguments` string is
            not valid JSON.
    """
    choices = _as_list(payload.get("choices"))
    if not choices:
        raise MalformedOutputError(provider, raw=json.dumps(payload)[:200], attempts=1)
    choice = _as_dict(choices[0])
    message = _as_dict(choice.get("message"))
    parts: list[ContentPart] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        parts.append(TextPart(text=content))
    for raw_call in _as_list(message.get("tool_calls")):
        parts.append(ToolCallPart(call=_tool_call_from_wire(raw_call, provider)))
    stop_reason = _stop_reason_from_wire(choice.get("finish_reason"))
    model = payload.get("model")
    return LLMResponse(
        parts=tuple(parts),
        stop_reason=stop_reason,
        usage=_usage_from_wire(payload.get("usage")),
        model=model if isinstance(model, str) else "",
    )


def _tool_call_from_wire(raw: JsonValue, provider: str) -> ToolCall:
    """Map one wire `tool_calls[*]` entry to a `ToolCall`, parsing its JSON `arguments` string.

    Raises:
        MalformedOutputError: `arguments` is present but is not valid JSON.
    """
    raw_dict = _as_dict(raw)
    function = _as_dict(raw_dict.get("function"))
    raw_arguments = function.get("arguments", "{}")
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else {}
    except json.JSONDecodeError as exc:
        raise MalformedOutputError(provider, raw=str(raw_arguments)[:200], attempts=1) from exc
    call_id = raw_dict.get("id")
    name = function.get("name")
    return ToolCall(
        id=call_id if isinstance(call_id, str) else "",
        name=name if isinstance(name, str) else "",
        arguments=arguments if isinstance(arguments, dict) else {},
    )


def _usage_from_wire(raw: JsonValue | None) -> Usage:
    """Map the wire's `usage` object to a `Usage`; a missing or malformed one becomes all-zero."""
    raw_dict = _as_dict(raw)
    details = _as_dict(raw_dict.get("prompt_tokens_details"))
    cached = details.get("cached_tokens", 0)
    input_tokens = raw_dict.get("prompt_tokens", 0)
    output_tokens = raw_dict.get("completion_tokens", 0)
    return Usage(
        input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
        output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
        cached_tokens=cached if isinstance(cached, int) else 0,
        cost_usd=None,  # This adapter never prices a call; BoundModel's cost fields do that.
    )


def _stop_reason_from_wire(finish_reason: JsonValue | None) -> StopReason:
    """Map the wire's `finish_reason` string to a `StopReason`, defaulting to END_TURN."""
    if isinstance(finish_reason, str) and finish_reason in _FINISH_REASON_TO_STOP_REASON:
        return _FINISH_REASON_TO_STOP_REASON[finish_reason]
    return StopReason.END_TURN


# ──────────────────────────────────────────────────────────────────────────────
# Streaming: accumulate SSE chunks into LLMChunks
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _PartialToolCall:
    """One tool call's pieces, accumulated across several streamed deltas sharing one `index`."""

    id: str = ""
    name: str = ""
    arguments_json: str = ""

    def to_tool_call(self, provider: str) -> ToolCall:
        """Parse the accumulated `arguments_json` and return the completed `ToolCall`.

        Raises:
            MalformedOutputError: the accumulated `arguments_json` is not valid JSON.
        """
        if not self.arguments_json:
            return ToolCall(id=self.id, name=self.name, arguments={})
        try:
            arguments = json.loads(self.arguments_json)
        except json.JSONDecodeError as exc:
            raise MalformedOutputError(provider, raw=self.arguments_json[:200], attempts=1) from exc
        return ToolCall(id=self.id, name=self.name, arguments=arguments)


@dataclass
class StreamState:
    """Accumulate one `stream()` call's SSE chunks into `LLMChunk`s.

    One instance per streamed request (`OpenAICompatProvider.stream` owns it); never shared or
    reused. `absorb` is called once per parsed SSE `data:` object and returns the zero or more
    `LLMChunk`s that chunk completes; `finalize` is called once after the SSE stream itself ends,
    for the (uncommon, but real for a server that ignores `stream_options.include_usage`) case
    where a `stop_reason` was seen but no trailing usage-only chunk ever arrived to carry it.
    """

    provider: str
    _pieces: dict[int, _PartialToolCall] = field(default_factory=dict)
    _pending_stop_reason: StopReason | None = field(default=None)
    _stop_reason_flushed: bool = field(default=False)

    def absorb(self, chunk: JsonObject) -> tuple[LLMChunk, ...]:
        """Fold one parsed SSE `data:` object in and return the LLMChunks it completes."""
        choices = _as_list(chunk.get("choices"))
        if not choices:
            # A trailing usage-only chunk: empty `choices`, `usage` present (stream_options.
            # include_usage's last line). This is the normal place the final chunk is emitted.
            usage = chunk.get("usage")
            if usage is None:
                return ()
            self._stop_reason_flushed = True
            return (LLMChunk(usage=_usage_from_wire(usage), stop_reason=self._pending_stop_reason),)
        choice = _as_dict(choices[0])
        delta = _as_dict(choice.get("delta"))
        out: list[LLMChunk] = []
        text = delta.get("content")
        if isinstance(text, str) and text:
            out.append(LLMChunk(text=text))
        for piece in _as_list(delta.get("tool_calls")):
            self._absorb_tool_call_piece(piece)
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str):
            self._pending_stop_reason = _stop_reason_from_wire(finish_reason)
            if finish_reason == "tool_calls":
                out.extend(self._drain_finished_tool_calls())
        return tuple(out)

    def finalize(self) -> LLMChunk | None:
        """Return a trailing final chunk if the stream ended with no usage-only chunk for it."""
        if self._pending_stop_reason is None or self._stop_reason_flushed:
            return None
        return LLMChunk(stop_reason=self._pending_stop_reason)

    def _absorb_tool_call_piece(self, piece: JsonValue) -> None:
        """Merge one `delta.tool_calls[*]` wire entry into its accumulating `_PartialToolCall`."""
        piece_dict = _as_dict(piece)
        raw_index = piece_dict.get("index", 0)
        index = raw_index if isinstance(raw_index, int) else 0
        partial = self._pieces.setdefault(index, _PartialToolCall())
        call_id = piece_dict.get("id")
        if isinstance(call_id, str) and call_id:
            partial.id = call_id
        function = _as_dict(piece_dict.get("function"))
        name = function.get("name")
        if isinstance(name, str) and name:
            partial.name = name
        arguments_piece = function.get("arguments")
        if isinstance(arguments_piece, str):
            partial.arguments_json += arguments_piece

    def _drain_finished_tool_calls(self) -> tuple[LLMChunk, ...]:
        """Emit one LLMChunk per accumulated tool call, in index order, and clear the buffer."""
        chunks = tuple(
            LLMChunk(tool_call=piece.to_tool_call(self.provider))
            for _, piece in sorted(self._pieces.items(), key=lambda item: item[0])
        )
        self._pieces.clear()
        return chunks
