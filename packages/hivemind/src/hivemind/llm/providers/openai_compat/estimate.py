"""Estimate a call's token usage from characters when a server reports none.

Some OpenAI-compatible servers (older llama.cpp builds, some proxies, a server run with usage
reporting off) answer with no `usage` block at all. Accounting that silently read this as "zero
tokens" would make a busy local model look idle and let a per-token price apply to nothing, so
this module makes the fallback explicit: tokens are counted as characters divided by
`CHARS_PER_TOKEN_ESTIMATE`, rounded up, and the resulting `Usage` carries `estimated=True` so the
Pheromone Trail (the Hive's append-only audit log) can tell a measurement from a guess. The
estimate counts text, tool names and tool arguments; it ignores images and audio, which a real
tokenizer would weigh differently.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.providers.openai_compat`. Called by
    `mapping.py` (for a missing `usage` on a reply or a stream) and `provider.py` (for
    `count_tokens`' request size). Pure: no I/O, no clock.

Key invariants:
    - An estimated `Usage` always has `estimated=True` and `cost_usd=None`; pricing is the Fanner
      lane's job (`hivemind.llm.fanner.pricing`), never an adapter's.
    - The estimate carries no safety margin: `OpenAICompatProvider.count_tokens` adds its own
      margin for budgeting, while accounting wants the unbiased figure.

See Also:
    - hivemind.llm.providers.openai_compat.mapping for where a missing `usage` is detected.
    - hivemind.llm.providers.openai_compat README, "Token usage", for the operator-facing rule.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from hivemind.llm.models import (
    ContentPart,
    LLMRequest,
    TextPart,
    ToolCallPart,
    Usage,
)

CHARS_PER_TOKEN_ESTIMATE = 4  # Same rough English-text ratio hivemind.llm.fake.count_tokens uses.

__all__ = [
    "CHARS_PER_TOKEN_ESTIMATE",
    "estimated_usage",
    "request_char_count",
    "response_char_count",
    "tokens_from_chars",
]


def tokens_from_chars(chars: int) -> int:
    """Convert a character count to a token estimate, rounding up so any text is >= 1 token.

    Args:
        chars: A non-negative character count.

    Returns:
        `ceil(chars / CHARS_PER_TOKEN_ESTIMATE)`; 0 only for 0 characters.
    """
    return math.ceil(chars / CHARS_PER_TOKEN_ESTIMATE)


def estimated_usage(prompt_chars: int, output_chars: int) -> Usage:
    """Build the `Usage` for a call whose server reported none.

    Args:
        prompt_chars: Characters sent (`request_char_count`).
        output_chars: Characters received (`response_char_count`, or the streamed total).

    Returns:
        A `Usage` with token counts estimated from characters, `estimated=True` and no cost.
    """
    return Usage(
        input_tokens=tokens_from_chars(prompt_chars),
        output_tokens=tokens_from_chars(output_chars),
        cost_usd=None,  # Priced later, once, by the Fanner lane.
        estimated=True,
    )


def request_char_count(request: LLMRequest) -> int:
    """Sum the character count of `request`'s system prompt, every text part and every tool.

    Deliberately ignores image and tool-call/tool-result content, which a real tokenizer would
    weigh differently and this estimate does not attempt.
    """
    total = len(request.system) if request.system is not None else 0
    # Every message turn contributes its text parts; other part kinds are not characters.
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, TextPart):
                total += len(part.text)
    # A tool definition is sent on every request, so its name, description and schema count.
    for tool in request.tools:
        total += len(tool.name) + len(tool.description) + len(str(tool.parameters))
    return total


def response_char_count(parts: Iterable[ContentPart]) -> int:
    """Sum the characters a reply produced: its text, plus each tool call's name and arguments."""
    total = 0
    for part in parts:
        if isinstance(part, TextPart):
            total += len(part.text)
        elif isinstance(part, ToolCallPart):
            # str(arguments) stands in for the wire's JSON string; the lengths agree closely enough
            # for an estimate that is flagged as one.
            total += len(part.call.name) + len(str(part.call.arguments))
    return total
