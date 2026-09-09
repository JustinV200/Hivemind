"""Define LLMProvider: the one protocol every model call in the Hive goes through.

Codingrules section 8.6: "One door. LLMProvider... is the only way any code talks to a model.
Vendor SDKs and model-server HTTP clients are imported only inside llm/providers/<name>/." This
module is that door: a `typing.Protocol` (structural, per codingrules section 8.1) with no logic
of its own, implemented by `hivemind.llm.providers.anthropic.AnthropicProvider`, `hivemind.llm.
providers.openai_compat.OpenAICompatProvider` (later roadmap steps) and `hivemind.llm.fake.
FakeLLMProvider` today. `stream` is declared with a plain `def`, not `async def`, so an
implementation may write it as an `async def` generator function (`async def stream(...): ...
yield ...`): calling such a function returns an async generator immediately, without running any
of its body, which is exactly what an `AsyncIterator[LLMChunk]`-returning callable needs to be.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by workers, wardens and queen
    whenever a bee is allowed to think with a model, always through a `hivemind.llm.slots.
    BoundModel` rather than directly. Implementations call into their own `llm/providers/<name>/`
    only; this module itself calls into nothing beyond the standard library.

Key invariants:
    - `name` is the manifest's `[llm.providers.<name>]` key; codingrules section 8.6 forbids
      branching on it (`if provider.name == "anthropic"` is a review rejection) -- it exists for
      logging, the Pheromone Trail and error messages, never for control flow.
    - Every method that can fail typed raises a subclass of `hivemind.llm.errors.LLMError`, never
      a vendor SDK exception: SDK exceptions are caught and translated inside each adapter's own
      `llm/providers/<name>/` package.
    - `count_tokens` returns `None`, not a wrong number, when the provider cannot estimate before
      sending; callers must treat `None` as "unknown", never as zero.

See Also:
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule this module follows.
    - .claude/codingrules.md section 8.6 for the "one door" rule this protocol is.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - hivemind.llm.models for LLMRequest, LLMResponse and LLMChunk, the types crossing this door.
    - hivemind.llm.capabilities for ProviderCapabilities and ProviderHealth.
    - hivemind.llm.fake for FakeLLMProvider, the reference implementation used in tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from hivemind.llm.capabilities import ProviderCapabilities, ProviderHealth
from hivemind.llm.models import LLMChunk, LLMRequest, LLMResponse

__all__ = ["LLMProvider"]


class LLMProvider(Protocol):
    """Talk to one model provider: complete, stream, count tokens, and report health.

    Implementations must be safe to call concurrently: a Warden may run several Workers' calls
    against the same bound provider at once.
    """

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key for this provider.

        For identification only (logging, the Pheromone Trail, error messages); never branch on
        this value (codingrules section 8.6).
        """
        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return this provider's declared capabilities.

        Fixed for the life of the provider instance; a manifest override
        (`[llm.providers.<name>.capabilities]`) is applied once, at construction.
        """
        ...

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run `request` to completion and return the full response.

        Args:
            request: The call to make. `request.slot` is informational for this provider (the
                binding is already decided by the caller); everything else in `request` shapes
                the call itself.

        Returns:
            The completed LLMResponse.

        Raises:
            RateLimitedError: The provider refused the call due to a rate limit.
            ProviderUnavailableError: The provider could not be reached, or is refusing all calls.
            ContextTooLongError: `request` does not fit the provider's context window.
            RefusedError: The model declined to answer.
            MalformedOutputError: A ladder built on this provider exhausted its retries; raised by
                the ladder, not directly by most providers, but the type belongs to this module's
                error family so a caller catches `LLMError` for either source.
        """
        ...

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Run `request`, yielding incremental chunks as they become available.

        Args:
            request: The call to make; same contract as `complete`.

        Returns:
            An async iterator of LLMChunk. The final chunk carries `stop_reason` and the call's
            total `usage`; earlier chunks carry `text` and/or `tool_call` deltas. A provider whose
            `capabilities.streaming` is False still implements this by yielding exactly one chunk
            with the whole response.

        Raises:
            RateLimitedError: The provider refused the call due to a rate limit.
            ProviderUnavailableError: The provider could not be reached, or is refusing all calls.
            ContextTooLongError: `request` does not fit the provider's context window.
        """
        ...

    async def count_tokens(self, request: LLMRequest) -> int | None:
        """Estimate `request`'s token count without sending it.

        Args:
            request: The call whose token count to estimate.

        Returns:
            The estimated token count, or None when this provider cannot estimate one before
            sending (`capabilities.token_counting` is False). None means "unknown", not zero.
        """
        ...

    async def health(self) -> ProviderHealth:
        """Return this provider's current health.

        Returns:
            A fresh ProviderHealth reading. Never cached across the life of the provider instance
            longer than the caller's own polling interval decides; health is re-probed in memory
            only (Appendix C: "Provider health... In memory, re-probed on start").
        """
        ...
