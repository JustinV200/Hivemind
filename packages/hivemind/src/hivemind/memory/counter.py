"""Define TokenCounter, and its two implementations: an estimate and a provider-backed count.

`hivemind.memory.hot_state.packing.assemble` must know, before it ever calls a model, how many
tokens the prompt it is building will cost -- codingrules section 8.9: "Before every call the
assembled prompt is token-counted (provider count where available, else an estimate with margin)".
`TokenCounter` is the seam that lets `assemble` stay agnostic to which kind of counting is
available: `EstimateCounter` is the always-available fallback (a plain character-per-token ratio
with a safety margin, the same shape codingrules section 8.6 describes for a provider whose
`capabilities.token_counting` is False); `ProviderCounter` wraps a real `LLMProvider` and falls
back to another `TokenCounter` (typically an `EstimateCounter`) whenever the provider itself
returns `None` (`LLMProvider.count_tokens`'s documented "cannot estimate" case).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Used by hivemind.memory.hot_state.
    packing.assemble, always through the TokenCounter protocol so it never depends on which
    implementation backs a given ModelSlot. Calls into hivemind.llm (LLMProvider, LLMRequest,
    Message, Role) and hivemind.forage (ModelSlot) only.

Key invariants:
    - EstimateCounter.count never awaits anything real; it is pure arithmetic wrapped in an
      async signature so it satisfies the TokenCounter protocol interchangeably with
      ProviderCounter.
    - ProviderCounter.count returns the provider's own count whenever it is not None; the fallback
      counter only runs when the provider itself reports it cannot count (codingrules section
      8.6: "None means 'unknown', not zero").

See Also:
    - .claude/codingrules.md section 8.9 for the token-counting rule this module implements.
    - .claude/codingrules.md section 8.6 for LLMProvider.count_tokens's "None means unknown" rule.
    - hivemind.memory.hot_state.packing for assemble, the one caller.
    - hivemind.llm.provider for LLMProvider, the protocol ProviderCounter wraps.
"""

from __future__ import annotations

import math
from typing import Protocol

from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMProvider, LLMRequest, Message, Role

CHARS_PER_TOKEN_ESTIMATE = 4  # A common rule of thumb for English prose across many tokenizers.
ESTIMATE_MARGIN = 1.15  # 15% headroom: an estimate that runs low risks an overflowing prompt;
# one that runs a little high only costs a slightly smaller hot-state section.
# A token-counting-only call never needs real output; LLMRequest.max_output_tokens must be > 0,
# so this is the smallest legal value, never sent to a provider (count_tokens never generates).
MIN_COUNT_REQUEST_OUTPUT_TOKENS = 1

__all__ = [
    "CHARS_PER_TOKEN_ESTIMATE",
    "ESTIMATE_MARGIN",
    "MIN_COUNT_REQUEST_OUTPUT_TOKENS",
    "EstimateCounter",
    "ProviderCounter",
    "TokenCounter",
]


class TokenCounter(Protocol):
    """Estimate or count how many tokens a piece of text will cost."""

    async def count(self, text: str) -> int:
        """Return `text`'s token count.

        Args:
            text: The text to count. Never a whole request; callers count each candidate section
                on its own so a hot-state packer can compare it against a remaining budget.

        Returns:
            The token count: exact when backed by a provider, an estimate otherwise.
        """
        ...


class EstimateCounter:
    """A provider-independent token estimate: characters divided by a ratio, plus a margin.

    Always available, so it is the fallback `ProviderCounter` reaches for and the only counter a
    caller needs when no provider is bound yet (codingrules section 8.9: "an estimate with
    margin").
    """

    def __init__(
        self,
        chars_per_token: int = CHARS_PER_TOKEN_ESTIMATE,
        margin: float = ESTIMATE_MARGIN,
    ) -> None:
        """Create an estimator.

        Args:
            chars_per_token: Assumed characters per token; smaller values estimate more tokens
                for the same text (a more conservative estimate).
            margin: Multiplier applied to the raw character-ratio estimate, so a slightly wrong
                ratio still leans toward overestimating rather than under.
        """
        self._chars_per_token = chars_per_token
        self._margin = margin

    async def count(self, text: str) -> int:
        """Return `len(text) / chars_per_token`, scaled by `margin` and rounded up.

        Args:
            text: The text to estimate.

        Returns:
            The estimated token count; never negative, since `len(text)` never is.
        """
        # Pure arithmetic behind an async signature: no I/O, but the shape must match
        # TokenCounter's protocol so this and ProviderCounter are interchangeable.
        return math.ceil((len(text) / self._chars_per_token) * self._margin)


class ProviderCounter:
    """A TokenCounter backed by a real LLMProvider, falling back when it cannot count.

    Builds a single-message LLMRequest on the given slot and asks the provider to count it;
    when the provider returns None (its capabilities declare no token counting, or this call
    could not be estimated), the fallback counter answers instead.
    """

    def __init__(self, provider: LLMProvider, slot: ModelSlot, fallback: TokenCounter) -> None:
        """Wrap `provider` for token counting, with `fallback` for when it cannot answer.

        Args:
            provider: The provider to ask first.
            slot: The ModelSlot the eventual real call will use; carried on the probe request so
                a provider whose counting depends on model choice counts against the right one.
            fallback: Used whenever `provider.count_tokens` returns None.
        """
        self._provider = provider
        self._slot = slot
        self._fallback = fallback

    async def count(self, text: str) -> int:
        """Ask the provider to count `text`; fall back when it cannot.

        Args:
            text: The text to count.

        Returns:
            The provider's own count when it has one; otherwise the fallback counter's estimate.
        """
        request = LLMRequest(
            slot=self._slot,
            system=None,
            messages=(Message.text(Role.USER, text),),
            max_output_tokens=MIN_COUNT_REQUEST_OUTPUT_TOKENS,
        )
        # External await: a provider's own token-counting endpoint, typically a fast, cheap call
        # (no generation happens); a caller wanting a timeout wraps this counter, not the reverse.
        counted = await self._provider.count_tokens(request)
        if counted is not None:
            return counted
        return await self._fallback.count(text)
