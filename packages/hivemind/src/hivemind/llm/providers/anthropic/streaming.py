"""Accumulate a live Anthropic message stream into HiveMind LLMChunks.

Split out of `mapping.py` (codingrules section 5.2: a module that outgrows the line limit splits
into a sibling inside its own package) because the streamed half of the Anthropic-to-HiveMind
mapping carries its own vendor field names (`TextEvent`, `.text`, `.thinking`) alongside the
request/response mapping `mapping.py` already carries, and together the two would not fit under
one file's 300-line limit. `accumulate` reads the events `AnthropicClient.stream` yields --
`anthropic.MessageStreamEvent`, the SDK's own high-level streaming helper's union of raw
SSE-shaped events (`message_start`, `content_block_start`, ...) and synthesized convenience
events (a `TextEvent` carrying a plain `.text` string, an `InputJsonEvent`, ...) -- emitting one
text `LLMChunk` per `TextEvent` as it arrives; when the stream's own already-accumulated final
`Message` arrives (the last item `AnthropicClient.stream` ever yields), this module hands it to
`mapping.from_message` and emits the tool-call and final chunks from that -- exactly the shape
`hivemind.llm.fake.FakeLLMProvider.stream` already uses (text pieces, then tool-call chunks, then
one final chunk carrying usage and stop_reason), so every caller of `LLMProvider.stream` sees the
same chunk ordering regardless of which provider is behind the call.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.anthropic`.
    Called by `AnthropicProvider.stream` (`provider.py`). Calls into `hivemind.llm.providers.
    anthropic.mapping`, `hivemind.llm.models` and the `anthropic` SDK's types only.

Key invariants:
    - Never accumulates a tool call's arguments itself: the SDK's own `AsyncMessageStream`
      already does that internally (`get_final_message()`), so this module only reads its result
      rather than re-implementing per-block JSON-delta accumulation.
    - Yields nothing for a `ThinkingEvent`: `LLMChunk` (`hivemind.llm.models`) has no reasoning
      field, so a thinking summary reaches the caller only on the final `LLMResponse` proper
      (via `mapping.from_message`), never mid-stream.

See Also:
    - hivemind.llm.providers.anthropic.mapping for the shared (non-streamed) half of this mapping.
    - hivemind.llm.providers.anthropic.client for AnthropicClient.stream, this module's caller.
    - hivemind.llm.fake for FakeLLMProvider.stream, the chunk shape this module matches.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import anthropic
import anthropic.types as at

from hivemind.llm.models import LLMChunk
from hivemind.llm.providers.anthropic import mapping

__all__ = ["accumulate"]


async def accumulate(
    events: AsyncIterator[anthropic.MessageStreamEvent | at.Message], *, provider: str
) -> AsyncIterator[LLMChunk]:
    """Turn one stream call's events into `LLMChunk`s.

    Args:
        events: `AnthropicClient.stream`'s own async iterator: zero or more `MessageStreamEvent`s,
            immediately followed by the stream's fully accumulated final `Message`.
        provider: The manifest provider name, threaded through to `mapping.from_message` for its
            refusal debug log.

    Yields:
        One `LLMChunk(text=...)` per text delta, in the order they arrive; then one
        `LLMChunk(tool_call=...)` per tool call the final message carries; then one final chunk
        carrying `usage` and `stop_reason`.
    """
    async for event in events:
        if isinstance(event, at.Message):
            # AnthropicClient.stream's own contract: this is always the last item it yields, so
            # every remaining line below runs exactly once and the final `return` is reachable
            # only from here -- there is nothing left to iterate afterwards regardless.
            response = mapping.from_message(event, provider=provider)
            for call in response.tool_calls:
                yield LLMChunk(tool_call=call)
            yield LLMChunk(usage=response.usage, stop_reason=response.stop_reason)
            return
        text = _text_delta(event)
        if text:
            yield LLMChunk(text=text)


def _text_delta(event: anthropic.MessageStreamEvent) -> str | None:
    """Return a `TextEvent`'s text, or None for any other event shape.

    A `TextEvent` (the SDK's own synthesized convenience event) carries the new text delta
    directly on `.text`; using it instead of digging into a raw `content_block_delta`'s nested
    `.delta.text_delta.text` is what the SDK's high-level `messages.stream()` helper is for.
    """
    if event.type == "text":
        return event.text
    return None
