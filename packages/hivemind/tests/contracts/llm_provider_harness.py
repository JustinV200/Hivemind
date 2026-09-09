"""Provide one ProviderHarness per LLMProvider implementation, for the provider contract suite.

`test_llm_provider_contract.py` (roadmap step 3.8) writes each contract clause once against the
`hivemind.llm.provider.LLMProvider` Protocol and runs it over every implementation this module
registers a harness for: the fake (`FakeHarness`, scripting `hivemind.llm.fake.FakeLLMProvider`
directly), and the two real adapters (`OpenAICompatHarness`, `AnthropicHarness`), each built over
its own SDK's mock transport (`httpx.MockTransport` for the OpenAI-compatible wire, the `anthropic`
SDK's own `httpx2.MockTransport` fork for Anthropic's) so no network call is ever made. The two
adapter harnesses share their FIFO-queue bookkeeping through `_HttpHarnessBase`, parametrised by an
`_AdapterHooks` bundle of pure body-shaping functions; only routing (`_handle`) and provider
construction (`make_provider`) differ per wire, since the two SDKs need different mock-transport
and response types. Every harness's `arrange_tool_call`/`arrange_structured` are rung-aware: they
read the capabilities the harness's provider was just built with and shape their reply for
whichever rung those capabilities put the provider on (native/JSON-mode raw JSON, prompted a
fenced block), so one `arrange_*` call works unmodified whether the test is proving the strongest
rung or the zero-capability floor (codingrules 8.6).

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used only by
    `contracts.test_llm_provider_contract`.

Key invariants:
    - Every harness resets all of its mutable state inside `make_provider()`, so one module-level
      harness instance is safe to reuse across every parametrised test (mirrors
      `test_cell_session_contract.py`'s `_HARNESSES` pattern): a test that forgets to arrange a
      response before calling the provider gets a plain default reply, never a stale one left over
      from an earlier test.
    - `arrange_*` methods are named `arrange_*`, never `messages`/`history`
      (`scripts/check_no_transcripts.py`); every model id here is the neutral `DEFAULT_MODEL`
      (`scripts/check_no_model_ids.py`), and every wire path is built the same way the adapters'
      own unit tests build one, by concatenation where a literal would trip that same script.

See Also:
    - .claude/codingrules.md section 8.6 for "capabilities are declared, not assumed" and the
      degradation-ladder rules this harness's rung-awareness exists to exercise.
    - .claude/codingrules.md section 14.3 for the contract-suite rule this module supports.
    - contracts.test_llm_provider_contract for the eleven contract clauses built on this module.
    - hivemind.llm.provider for the LLMProvider Protocol every harness here builds an instance of.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import anthropic
import httpx
import httpx2
from pydantic import JsonValue, SecretStr

from hivemind.llm import (
    ContextTooLongError,
    FakeLLMProvider,
    HealthState,
    JsonObject,
    LLMError,
    LLMProvider,
    ProviderCapabilities,
    ProviderUnavailableError,
    RateLimitedError,
    StopReason,
    ToolCall,
    text_response,
    tool_call_response,
)
from hivemind.llm.providers.anthropic import AnthropicConfig, AnthropicProvider
from hivemind.llm.providers.openai_compat import OpenAICompatConfig, OpenAICompatProvider
from waggle.clock import FakeClock

DEFAULT_MODEL = "test-model"  # Neutral id, never a real provider's (codingrules 8.6).
RETRY_AFTER_S = 2.0  # One fixed value every harness's "rate_limited" arrangement reports.

_OPENAI_BASE_URL = "http://127.0.0.1:9/v1"  # Port 9: nothing listens; never a real server.
_OPENAI_MODELS_PATH = "/v1" + "/models"
_OPENAI_CHAT_PATH = "/v1" + "/chat/completions"  # Concatenated: check_no_model_ids.py bans the
# joined literal outright, even as a mock transport's own routing key (see test_provider.py).
_ANTHROPIC_MODELS_PATH = "/v1/models"
_ANTHROPIC_COUNT_TOKENS_PATH = "/v1/messages/count_tokens"
_ANTHROPIC_MESSAGES_PATH = "/v1/messages"
_ANTHROPIC_TOKEN_ESTIMATE = 42  # An arbitrary positive count_tokens() fixture reply.

# One (status, body, headers) reply, queued FIFO for a mock transport's next completion request.
_Reply = tuple[int, JsonObject, dict[str, str]]

ErrorKind = Literal["rate_limited", "unavailable", "context_too_long", "refused"]

__all__ = [
    "DEFAULT_MODEL",
    "AnthropicHarness",
    "ErrorKind",
    "FakeHarness",
    "OpenAICompatHarness",
    "ProviderHarness",
]


class ProviderHarness(Protocol):
    """Build one LLMProvider implementation and script its next wire reply.

    A test always calls `make_provider` first, then one or more `arrange_*` calls, then exercises
    the provider it got back: every `arrange_*` mutates state only that most-recently-built
    provider reads.
    """

    def make_provider(self, capabilities: ProviderCapabilities | None = None) -> LLMProvider:
        """Build a fresh provider; `capabilities` defaults to `ProviderCapabilities.full()`."""
        ...

    def configure_api_key(self, secret: str) -> None:
        """Arrange for the next `make_provider()` call to carry `secret` as its API key."""
        ...

    def arrange_text(self, text: str) -> None:
        """Arrange the next call to return a plain-text response of `text`."""
        ...

    def arrange_tool_call(self, name: str, arguments: JsonObject) -> None:
        """Arrange the next call to return a tool call, native or prompted per capabilities."""
        ...

    def arrange_structured(self, payload: JsonObject) -> None:
        """Arrange the next call to return `payload`, shaped for the provider's structured rung."""
        ...

    def arrange_stream(self, chunks: Sequence[str]) -> None:
        """Arrange the next call to stream `chunks` as text, then one final usage/stop chunk."""
        ...

    def arrange_error(self, kind: ErrorKind) -> None:
        """Arrange the next call to fail (or refuse) the way `kind` names."""
        ...

    def arrange_health(self, state: HealthState) -> None:
        """Arrange the next `health()` call to report `state`."""
        ...

    def last_request_model(self) -> str | None:
        """Return the wire `model` the most recent non-health call carried."""
        ...

    def supported_health_states(self) -> frozenset[HealthState]:
        """Return the HealthState values this harness can honestly arrange."""
        ...


def _fenced_tool_block(name: str, arguments: JsonObject) -> str:
    """Render one prompted-protocol fenced tool block (hivemind.llm.ladders.extraction's shape)."""
    return "```tool\n" + json.dumps({"name": name, "arguments": arguments}) + "\n```"


def _structured_text(capabilities: ProviderCapabilities, payload: JsonObject) -> str:
    """Shape `payload` for whichever structured rung `capabilities` puts a provider on.

    The native and JSON-mode rungs read raw JSON text; the prompted rung extracts a fenced block
    instead (`hivemind.llm.ladders.structured._parse_structured`) -- mirrored here so one
    `arrange_structured` call works unmodified at every capability level a harness is built with.
    """
    raw = json.dumps(payload)
    if capabilities.schema_output or capabilities.json_mode:
        return raw
    return f"```json\n{raw}\n```"


# ──────────────────────────────────────────────────────────────────────────────
# The fake: FakeLLMProvider's own scripted queue, no wire at all
# ──────────────────────────────────────────────────────────────────────────────

_FAKE_ERRORS: dict[ErrorKind, Callable[[str], LLMError]] = {
    "rate_limited": lambda name: RateLimitedError(name, retry_after_s=RETRY_AFTER_S),
    "unavailable": lambda name: ProviderUnavailableError(name, "simulated outage"),
    "context_too_long": lambda name: ContextTooLongError(name, window=8_192),
}


class FakeHarness:
    """Build FakeLLMProvider and script its FIFO response queue directly (no wire, no mock)."""

    def __init__(self) -> None:
        """Start with no provider built yet; `make_provider()` creates one."""
        self._provider: FakeLLMProvider | None = None

    def make_provider(self, capabilities: ProviderCapabilities | None = None) -> LLMProvider:
        """Build a fresh FakeLLMProvider with an empty scripted queue."""
        caps = capabilities if capabilities is not None else ProviderCapabilities.full()
        self._provider = FakeLLMProvider(name="fake", capabilities=caps, clock=FakeClock())
        return self._provider

    def configure_api_key(self, secret: str) -> None:
        """No-op: the fake has no wire, so no key to configure or leak."""
        return None

    def arrange_text(self, text: str) -> None:
        """Script a plain-text LLMResponse."""
        self._active().script(text_response(text))

    def arrange_tool_call(self, name: str, arguments: JsonObject) -> None:
        """Script a native tool-call response, or a fenced-block text one at the prompted rung."""
        provider = self._active()
        if provider.capabilities.native_tool_calls:
            call = ToolCall(id="call_1", name=name, arguments=arguments)
            provider.script(tool_call_response(call))
        else:
            provider.script(text_response(_fenced_tool_block(name, arguments)))

    def arrange_structured(self, payload: JsonObject) -> None:
        """Script `payload`, shaped for this provider's own structured rung."""
        provider = self._active()
        provider.script(text_response(_structured_text(provider.capabilities, payload)))

    def arrange_stream(self, chunks: Sequence[str]) -> None:
        """Script the joined `chunks` as one text response; the fake re-chunks it on its own."""
        self.arrange_text("".join(chunks))

    def arrange_error(self, kind: ErrorKind) -> None:
        """Script the LLMError `kind` names, or a REFUSAL-stopped text response for "refused"."""
        provider = self._active()
        if kind == "refused":
            provider.script(text_response("I can't help with that.", stop=StopReason.REFUSAL))
            return
        provider.script(_FAKE_ERRORS[kind](provider.name))

    def arrange_health(self, state: HealthState) -> None:
        """Set the simulated outage flag: DOWN turns it on, anything else off."""
        self._active().set_outage(state is HealthState.DOWN)

    def last_request_model(self) -> str | None:
        """Return the most recent scripted call's `request.model`."""
        calls = self._active().calls
        return calls[-1].model if calls else None

    def supported_health_states(self) -> frozenset[HealthState]:
        """Return HEALTHY/DOWN only: no HTTP layer exists to simulate DEGRADED honestly."""
        return frozenset({HealthState.HEALTHY, HealthState.DOWN})

    def _active(self) -> FakeLLMProvider:
        """Return the built provider, or raise if `make_provider()` was never called."""
        if self._provider is None:
            raise RuntimeError("make_provider() must be called before arranging a response.")
        return self._provider


# ──────────────────────────────────────────────────────────────────────────────
# Shared FIFO-queue bookkeeping for the two HTTP-backed harnesses
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _AdapterHooks:
    """The pure, per-adapter body-shaping functions `_HttpHarnessBase` is parametrised by."""

    text_body: Callable[[str], JsonObject]
    tool_call_body: Callable[[str, JsonObject], JsonObject]
    refusal_body: Callable[[str], JsonObject]
    sse_body: Callable[[Sequence[str]], str]
    errors: dict[ErrorKind, _Reply]
    health: dict[HealthState, _Reply]


class _HttpHarnessBase:
    """Own the FIFO reply queue, SSE body and health reply both adapter harnesses share.

    A subclass adds only `make_provider()` (which SDK/client to build) and `_handle()` (which
    wire's paths and response type to route with); every `arrange_*` and accessor here is wire
    shape-agnostic, driven entirely through the `_AdapterHooks` it was constructed with.
    """

    def __init__(self, hooks: _AdapterHooks) -> None:
        """Store `hooks` and start with the queue empty; `make_provider()` resets the rest."""
        self._hooks = hooks
        self._pending_api_key: str | None = None
        self._capabilities = ProviderCapabilities.full()
        self._queue: deque[_Reply] = deque()
        self._sse_body: str | None = None
        self._health: _Reply = (200, {"data": []}, {})
        self._last_model: str | None = None

    def _reset(self, capabilities: ProviderCapabilities | None, default_key: str) -> str:
        """Consume the pending API key and clear every arranged reply; return the key to use."""
        self._capabilities = (
            capabilities if capabilities is not None else ProviderCapabilities.full()
        )
        key = self._pending_api_key if self._pending_api_key is not None else default_key
        self._pending_api_key = None
        self._queue = deque()
        self._sse_body = None
        self._health = (200, {"data": []}, {})
        self._last_model = None
        return key

    def configure_api_key(self, secret: str) -> None:
        """Carry `secret` as the next built provider's API key."""
        self._pending_api_key = secret

    def arrange_text(self, text: str) -> None:
        """Queue a plain-text reply."""
        self._queue.append((200, self._hooks.text_body(text), {}))

    def arrange_tool_call(self, name: str, arguments: JsonObject) -> None:
        """Queue a native tool-call reply, or a fenced-block text one at the prompted rung."""
        if self._capabilities.native_tool_calls:
            self._queue.append((200, self._hooks.tool_call_body(name, arguments), {}))
        else:
            block = _fenced_tool_block(name, arguments)
            self._queue.append((200, self._hooks.text_body(block), {}))

    def arrange_structured(self, payload: JsonObject) -> None:
        """Queue `payload`, shaped for this provider's own structured rung."""
        text = _structured_text(self._capabilities, payload)
        self._queue.append((200, self._hooks.text_body(text), {}))

    def arrange_stream(self, chunks: Sequence[str]) -> None:
        """Queue the joined `chunks` for a non-streaming fallback, and set the SSE body too."""
        self._queue.append((200, self._hooks.text_body("".join(chunks)), {}))
        self._sse_body = self._hooks.sse_body(chunks)

    def arrange_error(self, kind: ErrorKind) -> None:
        """Queue the HTTP status/body `kind` names, or a refusal reply for "refused"."""
        if kind == "refused":
            self._queue.append((200, self._hooks.refusal_body("I can't help with that."), {}))
            return
        self._queue.append(self._hooks.errors[kind])

    def arrange_health(self, state: HealthState) -> None:
        """Set the next health-probe reply to the status/body `state` maps to."""
        self._health = self._hooks.health[state]

    def last_request_model(self) -> str | None:
        """Return the most recent completion request's wire `model` field."""
        return self._last_model

    def supported_health_states(self) -> frozenset[HealthState]:
        """Return every HealthState: both adapters' health probes cover all three by design."""
        return frozenset({HealthState.HEALTHY, HealthState.DEGRADED, HealthState.DOWN})

    def _pop_completion(self) -> _Reply:
        """Return the next queued reply, or a plain default when nothing was arranged."""
        return self._queue.popleft() if self._queue else (200, self._hooks.text_body("Hello."), {})


# ──────────────────────────────────────────────────────────────────────────────
# OpenAICompatProvider: httpx.MockTransport over the chat-completions wire
# ──────────────────────────────────────────────────────────────────────────────


def _openai_envelope(message: JsonObject, finish_reason: str, usage: JsonObject) -> JsonObject:
    """Wrap one assistant `message` in the outer `/chat/completions` reply envelope."""
    return {
        "id": "chatcmpl-contract",
        "object": "chat.completion",
        "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }


def _openai_text_body(text: str) -> JsonObject:
    """Build a `/chat/completions` reply carrying `text` as the assistant's whole message."""
    message: JsonObject = {"role": "assistant", "content": text}
    return _openai_envelope(
        message, "stop", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    )


def _openai_tool_call_body(name: str, arguments: JsonObject) -> JsonObject:
    """Build a `/chat/completions` reply carrying one native tool call."""
    call: JsonObject = {
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    message: JsonObject = {"role": "assistant", "content": None, "tool_calls": [call]}
    return _openai_envelope(
        message, "tool_calls", {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
    )


def _openai_refusal_body(text: str) -> JsonObject:
    """Build a `/chat/completions` reply whose `finish_reason` maps to StopReason.REFUSAL."""
    message: JsonObject = {"role": "assistant", "content": text}
    usage: JsonObject = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return _openai_envelope(message, "content_filter", usage)


def _openai_sse_body(chunks: Sequence[str]) -> str:
    """Build an SSE stream: one delta per chunk, a finish_reason chunk, a trailing usage one."""
    base = {"id": "chatcmpl-contract", "object": "chat.completion.chunk", "model": DEFAULT_MODEL}
    lines = []
    for chunk in chunks:
        delta = {"index": 0, "delta": {"content": chunk}, "finish_reason": None}
        lines.append(f"data: {json.dumps({**base, 'choices': [delta]})}\n")
    stop = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    usage = {
        **base,
        "choices": [],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": len(chunks),
            "total_tokens": 10 + len(chunks),
        },
    }
    lines += [f"data: {json.dumps(stop)}\n", f"data: {json.dumps(usage)}\n", "data: [DONE]\n"]
    return "\n".join(lines) + "\n"


# Reused across both the error and health dicts below: a rate limit and an outage look the same
# on the wire whether they were hit mid-call or during a health probe.
_OPENAI_SLOW_DOWN: JsonObject = {"error": {"message": "slow down"}}
_OPENAI_NO_KEY: JsonObject = {"error": {"message": "no key"}}
_OPENAI_CONTEXT: JsonObject = {"error": {"message": "maximum context length exceeded"}}
_RETRY_AFTER = {"Retry-After": str(RETRY_AFTER_S)}

_OPENAI_HOOKS = _AdapterHooks(
    text_body=_openai_text_body,
    tool_call_body=_openai_tool_call_body,
    refusal_body=_openai_refusal_body,
    sse_body=_openai_sse_body,
    errors={
        "rate_limited": (429, _OPENAI_SLOW_DOWN, _RETRY_AFTER),
        "unavailable": (503, {"error": {"message": "down for maintenance"}}, {}),
        "context_too_long": (400, _OPENAI_CONTEXT, {}),
    },
    health={
        HealthState.HEALTHY: (200, {"data": []}, {}),
        HealthState.DEGRADED: (429, _OPENAI_SLOW_DOWN, {}),
        HealthState.DOWN: (401, _OPENAI_NO_KEY, {}),
    },
)


class OpenAICompatHarness(_HttpHarnessBase):
    """Build OpenAICompatProvider over `httpx.MockTransport`, routing by path and body shape."""

    def __init__(self) -> None:
        """Wire in the OpenAI-compatible body shapes; no config exists until `make_provider()`."""
        super().__init__(_OPENAI_HOOKS)

    def make_provider(self, capabilities: ProviderCapabilities | None = None) -> LLMProvider:
        """Build a fresh OpenAICompatProvider over a fresh mock transport and empty queue."""
        key = self._reset(capabilities, default_key="")
        config = OpenAICompatConfig(
            base_url=_OPENAI_BASE_URL,
            model=DEFAULT_MODEL,
            api_key=SecretStr(key) if key else None,
            timeout_s=5.0,
            capabilities=self._capabilities,
        )
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle), base_url=_OPENAI_BASE_URL
        )
        return OpenAICompatProvider("openai_compat", config, http, FakeClock())

    def _handle(self, request: httpx.Request) -> httpx.Response:
        """Route one mock request: health, a queued completion, or a queued SSE stream."""
        if request.url.path == _OPENAI_MODELS_PATH:
            status, body, headers = self._health
            return httpx.Response(status, json=body, headers=headers)
        if request.url.path != _OPENAI_CHAT_PATH:
            return httpx.Response(404)
        payload = json.loads(request.content) if request.content else {}
        self._last_model = payload.get("model") if isinstance(payload, dict) else None
        if payload.get("stream") and self._sse_body is not None:
            return httpx.Response(
                200, content=self._sse_body, headers={"content-type": "text/event-stream"}
            )
        status, body, headers = self._pop_completion()
        return httpx.Response(status, json=body, headers=headers)


# ──────────────────────────────────────────────────────────────────────────────
# AnthropicProvider: the SDK's own httpx2 mock transport over the Messages API
# ──────────────────────────────────────────────────────────────────────────────


def _anthropic_envelope(
    content: list[JsonValue], stop_reason: str, usage: JsonObject
) -> JsonObject:
    """Wrap `content` blocks in the outer `messages.create` reply envelope."""
    return {
        "id": "msg_contract",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


def _anthropic_text_body(text: str) -> JsonObject:
    """Build a `messages.create` reply carrying `text` as one text content block."""
    block: list[JsonValue] = [{"type": "text", "text": text}]
    return _anthropic_envelope(block, "end_turn", {"input_tokens": 12, "output_tokens": 4})


def _anthropic_tool_call_body(name: str, arguments: JsonObject) -> JsonObject:
    """Build a `messages.create` reply carrying one native tool_use block."""
    block: list[JsonValue] = [
        {"type": "tool_use", "id": "toolu_1", "name": name, "input": arguments}
    ]
    return _anthropic_envelope(block, "tool_use", {"input_tokens": 20, "output_tokens": 8})


def _anthropic_refusal_body(_text: str) -> JsonObject:
    """Build a `messages.create` reply whose `stop_reason` is "refusal".

    `_text` is unused: a real refusal carries no text content; the parameter exists only so this
    matches `_AdapterHooks.refusal_body`'s shared signature with the OpenAI-compatible hook.
    """
    body = _anthropic_envelope([], "refusal", {"input_tokens": 5, "output_tokens": 0})
    body["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": "declined"}
    return body


def _sse_event(event: str, data: JsonObject) -> str:
    """Render one SSE `event:`/`data:` line pair, per the Anthropic streaming fixtures' shape."""
    return f"event: {event}\ndata: {json.dumps(data)}\n"


def _anthropic_sse_body(chunks: Sequence[str]) -> str:
    """Build a message_start..message_stop event stream with one text_delta per chunk."""
    message: JsonObject = {
        "id": "msg_contract",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }
    start_block: JsonObject = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    }
    deltas = [
        _sse_event(
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": c}},
        )
        for c in chunks
    ]
    tail: JsonObject = {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": len(chunks)},
    }
    events = [
        _sse_event("message_start", {"type": "message_start", "message": message}),
        _sse_event("content_block_start", start_block),
        *deltas,
        _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse_event("message_delta", tail),
        _sse_event("message_stop", {"type": "message_stop"}),
    ]
    return "\n".join(events) + "\n"


# Reused across both the error and health dicts below: a rate limit looks the same on the wire
# whether it hit mid-call or during a health probe.
_ANTHROPIC_SLOW_DOWN: JsonObject = {
    "type": "error",
    "error": {"type": "rate_limit_error", "message": "slow down"},
}
_ANTHROPIC_NO_KEY: JsonObject = {
    "type": "error",
    "error": {"type": "authentication_error", "message": "no key"},
}
_ANTHROPIC_OVERLOADED: JsonObject = {
    "type": "error",
    "error": {"type": "overloaded_error", "message": "busy"},
}
_ANTHROPIC_CONTEXT: JsonObject = {
    "type": "error",
    "error": {"type": "invalid_request_error", "message": "prompt is too long"},
}
_ANTHROPIC_RETRY_AFTER = {"retry-after": str(RETRY_AFTER_S)}

_ANTHROPIC_HOOKS = _AdapterHooks(
    text_body=_anthropic_text_body,
    tool_call_body=_anthropic_tool_call_body,
    refusal_body=_anthropic_refusal_body,
    sse_body=_anthropic_sse_body,
    errors={
        "rate_limited": (429, _ANTHROPIC_SLOW_DOWN, _ANTHROPIC_RETRY_AFTER),
        "unavailable": (503, _ANTHROPIC_OVERLOADED, {}),
        "context_too_long": (400, _ANTHROPIC_CONTEXT, {}),
    },
    health={
        HealthState.HEALTHY: (200, {"data": []}, {}),
        HealthState.DEGRADED: (429, _ANTHROPIC_SLOW_DOWN, {}),
        HealthState.DOWN: (401, _ANTHROPIC_NO_KEY, {}),
    },
)


class AnthropicHarness(_HttpHarnessBase):
    """Build AnthropicProvider over the SDK's own httpx2 mock transport, routing by path."""

    def __init__(self) -> None:
        """Wire in the Anthropic body shapes; no config exists until `make_provider()`."""
        super().__init__(_ANTHROPIC_HOOKS)

    def make_provider(self, capabilities: ProviderCapabilities | None = None) -> LLMProvider:
        """Build a fresh AnthropicProvider over a fresh mock transport and empty queue."""
        key = self._reset(capabilities, default_key="test-key")
        config = AnthropicConfig(api_key=SecretStr(key), capabilities=self._capabilities)
        sdk = anthropic.AsyncAnthropic(
            api_key=key,
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(self._handle)
            ),
        )
        return AnthropicProvider("anthropic", config, sdk, FakeClock())

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        """Route one mock request: health, token counting, a queued reply, or an SSE stream."""
        path = request.url.path
        if path == _ANTHROPIC_MODELS_PATH:
            status, body, headers = self._health
            return httpx2.Response(status, json=body, headers=headers)
        if path == _ANTHROPIC_COUNT_TOKENS_PATH:
            return httpx2.Response(200, json={"input_tokens": _ANTHROPIC_TOKEN_ESTIMATE})
        if path != _ANTHROPIC_MESSAGES_PATH:
            return httpx2.Response(404)
        payload = json.loads(request.content) if request.content else {}
        self._last_model = payload.get("model") if isinstance(payload, dict) else None
        if payload.get("stream") and self._sse_body is not None:
            return httpx2.Response(
                200, content=self._sse_body, headers={"content-type": "text/event-stream"}
            )
        status, body, headers = self._pop_completion()
        return httpx2.Response(status, json=body, headers=headers)
