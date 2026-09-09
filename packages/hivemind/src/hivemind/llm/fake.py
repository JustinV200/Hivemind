"""Provide FakeLLMProvider: an honest, scriptable LLMProvider for tests and demos.

A fake provider answers from a scripted queue instead of a network call, so a unit test, a `hive
doctor` smoke run, or an e2e demo path can exercise everything above `hivemind.llm` (codingrules
14.4: "fakes live in src/ beside their Protocol") with no provider, no API key and no network.
"Honest" means it never pretends to a capability it does not declare: with `native_tool_calls`
False it strips `ToolCallPart`s from a scripted response even if one was scripted with them, and
with `streaming` False `stream()` still returns an `AsyncIterator[LLMChunk]` (per
`hivemind.llm.provider.LLMProvider`) but yields the whole response as one chunk, exactly like a
real plain-text provider would have to. `text_response`/`tool_call_response` are the module-level
helpers a caller uses to build the canned `LLMResponse`s passed to `script()`.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Used by every test that needs an
    `LLMProvider` without a real one, by `hivemind.llm.slots` demos, and by the phase 3 e2e
    responder (roadmap step 3.22), which scripts plan and tool-call JSON. Calls into
    `hivemind.llm.capabilities`, `hivemind.llm.errors`, `hivemind.llm.models` and `waggle.clock`
    only.

Key invariants:
    - `calls` records every `LLMRequest` this provider actually saw, in call order; never named
      `messages`/`history` (`scripts/check_no_transcripts.py` does not allowlist this file, unlike
      `hivemind.llm.models` and `hivemind.llm.tools`).
    - The scripted queue is FIFO: `script(a, b)` then two calls return `a` then `b`. An `LLMError`
      instance in the queue is raised, not returned, on its turn.
    - An empty queue raises `ProviderUnavailableError`, a typed `LLMError`, never `IndexError`: a
      caller that forgot to script enough responses gets the same error shape a real outage would
      produce.
    - `set_outage(True)` makes every `complete`/`stream` call raise `ProviderUnavailableError`
      before touching the scripted queue or `calls`, and makes `health()` report `HealthState.DOWN`.

See Also:
    - .claude/codingrules.md section 14.4 for the fakes-over-mocks rule this module follows.
    - .claude/codingrules.md section 8.6 for the capability-honesty rule "core code branches on
      capabilities, never on provider name" this fake exists to let callers actually exercise.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this supports.
    - hivemind.llm.provider for the LLMProvider protocol this class implements.
    - hivemind.llm.models for LLMRequest, LLMResponse, LLMChunk and the content part types.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Callable

from hivemind.llm.capabilities import HealthState, ProviderCapabilities, ProviderHealth
from hivemind.llm.errors import LLMError, ProviderUnavailableError
from hivemind.llm.models import (
    LLMChunk,
    LLMRequest,
    LLMResponse,
    StopReason,
    TextPart,
    ToolCall,
    ToolCallPart,
    Usage,
)
from waggle.clock import Clock, FakeClock

FAKE_MODEL_ID = "fake-model"  # A neutral model id (codingrules 8.6): never a real provider's id.
TEXT_STREAM_CHUNK_COUNT = (
    3  # Enough pieces to prove a consumer reassembles a stream; few, so tests stay fast.
)
CHARS_PER_TOKEN_ESTIMATE = (
    4  # The usual English chars-per-token rule of thumb; the fake never tokenizes.
)

# A responder computes the next LLMResponse for a request; the default one (`_pop_scripted`)
# ignores the request and pops the scripted queue, but a caller may pass its own for a fake that
# answers based on the request's content (e.g. an e2e responder keyed on request.tools).
Responder = Callable[[LLMRequest], LLMResponse]

__all__ = [
    "CHARS_PER_TOKEN_ESTIMATE",
    "FAKE_MODEL_ID",
    "TEXT_STREAM_CHUNK_COUNT",
    "FakeLLMProvider",
    "Responder",
    "text_response",
    "tool_call_response",
]


class FakeLLMProvider:
    """A scriptable, honestly-capability-limited LLMProvider for tests and demos.

    Not thread- or task-safe against concurrent scripting: `script()` and the calls it feeds are
    meant to be set up by one test before use, then read by the code under test, the same pattern
    `hivemind.pheromone.trail.memory.MemoryPheromoneTrail` documents for its own internal state.
    """

    def __init__(
        self,
        name: str = "fake",
        capabilities: ProviderCapabilities | None = None,
        responder: Responder | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Create a FakeLLMProvider with nothing scripted yet.

        Args:
            name: This provider's manifest-style name; "fake" by default.
            capabilities: The capability set to declare; `ProviderCapabilities.full()` when
                omitted, so a test opts into a limited set explicitly with `.none()` or a custom
                value rather than getting one implicitly.
            responder: How to answer a call; the default pops the scripted queue (see `script`).
            clock: Source of `health()`'s `checked_at`; a fresh FakeClock when omitted.
        """
        self._name = name
        self._capabilities = (
            capabilities if capabilities is not None else ProviderCapabilities.full()
        )
        self._responder: Responder = responder if responder is not None else self._pop_scripted
        self._clock: Clock = clock if clock is not None else FakeClock()
        self._script: deque[LLMResponse | LLMError] = deque()
        self._is_down = False
        self.calls: list[LLMRequest] = []

    @property
    def name(self) -> str:
        """Return this provider's name; see `LLMProvider.name`."""
        return self._name

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return this provider's declared capabilities; see `LLMProvider.capabilities`."""
        return self._capabilities

    def script(self, *responses: LLMResponse | LLMError) -> None:
        """Queue responses (or errors) to return in order, one per call.

        Args:
            responses: Appended to the FIFO queue `complete`/`stream` draw from by default. An
                `LLMError` instance is raised, not returned, when its turn comes.
        """
        self._script.extend(responses)

    def set_outage(self, is_down: bool) -> None:
        """Simulate the provider being entirely down (or recovered).

        Args:
            is_down: When True, every subsequent `complete`/`stream` call raises
                `ProviderUnavailableError` before consuming the scripted queue, and `health()`
                reports `HealthState.DOWN`.
        """
        self._is_down = is_down

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the next scripted response for `request`; see `LLMProvider.complete`."""
        self._check_outage()
        self.calls.append(request)
        return _honour_capabilities(self._responder(request), self._capabilities)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Yield the next scripted response as chunks; see `LLMProvider.stream`.

        Declared `async def` with `yield` so calling it returns an async generator immediately,
        satisfying the `AsyncIterator[LLMChunk]` contract `LLMProvider.stream` promises without
        the body running until the caller actually iterates (module docstring's "honest" claim
        starts here: the outage check and the scripted pop happen on the first `__anext__`, the
        same moment a real provider would first touch the network).
        """
        self._check_outage()
        self.calls.append(request)
        response = _honour_capabilities(self._responder(request), self._capabilities)
        if not self._capabilities.streaming:
            # A plain-text provider still implements stream(): one chunk, the whole response.
            yield LLMChunk(
                text=response.text or None,
                usage=response.usage,
                stop_reason=response.stop_reason,
            )
            return
        for piece in _chunk_text(response.text, TEXT_STREAM_CHUNK_COUNT):
            yield LLMChunk(text=piece)
        for call in response.tool_calls:
            yield LLMChunk(tool_call=call)
        yield LLMChunk(usage=response.usage, stop_reason=response.stop_reason)

    async def count_tokens(self, request: LLMRequest) -> int | None:
        """Estimate `request`'s token count; see `LLMProvider.count_tokens`.

        Returns:
            `len(text) // CHARS_PER_TOKEN_ESTIMATE` over the request's system prompt and every
            TextPart, or None when `capabilities.token_counting` is False (this fake never
            pretends to count when it has declared it cannot).
        """
        if not self._capabilities.token_counting:
            return None
        return _request_char_count(request) // CHARS_PER_TOKEN_ESTIMATE

    async def health(self) -> ProviderHealth:
        """Return DOWN while an outage is simulated, HEALTHY otherwise; see `LLMProvider.health`."""
        if self._is_down:
            return ProviderHealth(
                state=HealthState.DOWN, detail="set_outage(True)", checked_at=self._clock.now()
            )
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=self._clock.now())

    def _check_outage(self) -> None:
        """Raise ProviderUnavailableError when `set_outage(True)` is in effect."""
        if self._is_down:
            raise ProviderUnavailableError(
                self._name, "an outage is simulated via set_outage(True)"
            )

    def _pop_scripted(self, request: LLMRequest) -> LLMResponse:
        """Return (or raise) the next queued script entry; the default `Responder`.

        Raises:
            ProviderUnavailableError: The scripted queue is empty; a caller must `script()` more
                responses before making another call.
        """
        if not self._script:
            raise ProviderUnavailableError(
                self._name,
                "the scripted response queue ran dry: call script(...) with more "
                "responses before this call",
            )
        item = self._script.popleft()
        if isinstance(item, LLMError):
            raise item
        return item


def text_response(
    text: str, *, stop: StopReason = StopReason.END_TURN, usage: Usage | None = None
) -> LLMResponse:
    """Build a plain-text LLMResponse for `FakeLLMProvider.script`.

    Args:
        text: The response's text.
        stop: Why the (simulated) call stopped; END_TURN by default.
        usage: Token accounting to report; a zero Usage when omitted.

    Returns:
        A validated LLMResponse with one TextPart.
    """
    return LLMResponse(
        parts=(TextPart(text=text),),
        stop_reason=stop,
        usage=usage if usage is not None else Usage(input_tokens=0, output_tokens=0),
        model=FAKE_MODEL_ID,
    )


def tool_call_response(*calls: ToolCall) -> LLMResponse:
    """Build a tool-call LLMResponse for `FakeLLMProvider.script`.

    Args:
        calls: The tool calls the scripted response should carry, in order.

    Returns:
        A validated LLMResponse with one ToolCallPart per call and stop_reason TOOL_USE.
    """
    return LLMResponse(
        parts=tuple(ToolCallPart(call=call) for call in calls),
        stop_reason=StopReason.TOOL_USE,
        usage=Usage(input_tokens=0, output_tokens=0),
        model=FAKE_MODEL_ID,
    )


def _honour_capabilities(response: LLMResponse, capabilities: ProviderCapabilities) -> LLMResponse:
    """Strip parts a declared capability set says this provider cannot produce.

    Args:
        response: The scripted (or responder-built) response, as-is.
        capabilities: This provider's declared capabilities.

    Returns:
        `response` unchanged when nothing needs dropping; otherwise a copy with every
        `ToolCallPart` removed, because a provider with `native_tool_calls=False` never returns
        one even when a test script asked for it (module docstring: "honest" capability
        behaviour). `response_schema` needs no handling here: this fake never attempts to enforce
        one either way, so `schema_output=False` changes nothing to ignore.
    """
    if capabilities.native_tool_calls:
        return response
    kept = tuple(part for part in response.parts if not isinstance(part, ToolCallPart))
    if kept == response.parts:
        return response
    return response.model_copy(update={"parts": kept})


def _chunk_text(text: str, chunk_count: int) -> tuple[str, ...]:
    """Split `text` into roughly `chunk_count` pieces, in order, dropping none of it.

    Args:
        text: The text to split; may be empty.
        chunk_count: How many pieces to aim for.

    Returns:
        An empty tuple for empty text; otherwise up to `chunk_count` non-empty pieces whose
        concatenation equals `text` exactly.
    """
    if not text:
        return ()
    # Ceiling division so `chunk_count` pieces are never exceeded even when len(text) does not
    # divide evenly; the last piece simply carries the remainder.
    size = -(-len(text) // chunk_count)
    return tuple(text[start : start + size] for start in range(0, len(text), size))


def _request_char_count(request: LLMRequest) -> int:
    """Return the total character count of `request`'s system prompt and every TextPart.

    Used only by `count_tokens`'s estimate; not a real tokenizer, so it never counts image or
    tool-call/tool-result content, which a real provider's tokenizer would weigh differently.
    """
    system_chars = len(request.system) if request.system is not None else 0
    message_chars = sum(
        len(part.text)
        for message in request.messages
        for part in message.parts
        if isinstance(part, TextPart)
    )
    return system_chars + message_chars
