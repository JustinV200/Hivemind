"""Define CallGate: the one seam every degradation-ladder call passes through.

A ladder (`hivemind.llm.ladders`, the fallback logic that lets a Worker, a Warden or the Queen
call a model without knowing its provider's exact capabilities) never calls
`bound.provider.complete` itself. It calls a `CallGate` instead. Today that gate is
`DirectCallGate`, a one-line pass-through; roadmap step 3.12a adds the Fanner (the seat meter
every model call passes through, `hivemind.llm.fanner.Fanner`), which will implement this same
Protocol to enforce a Forage grant and measure call latency before the request ever reaches the
provider. Because `complete_structured` and `run_tool_loop` (`hivemind.llm.ladders.structured`,
`hivemind.llm.ladders.tools`) already call through this seam, wiring the Fanner in later is a
composition-root change, not a change to either ladder.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.ladders`. Called by
    `complete_structured` and `run_tool_loop`; implemented by `DirectCallGate` here and, in a
    later roadmap step, by the Fanner. Calls into `hivemind.llm.models` and `hivemind.llm.slots`
    only.

Key invariants:
    - `DirectCallGate.complete` adds no behaviour of its own: it is the ladder's default when no
      gate is injected, so every ladder test that does not care about metering can ignore this
      seam entirely.
    - A `CallGate` never decides whether to fall back to `bound.fallback`; that decision (and the
      `LadderObserver` note that records it) belongs to the ladder itself, which is the only
      caller that also knows which rung or protocol it was attempting.

See Also:
    - .claude/codingrules.md section 8.6 for "degrade by ladder, in one place".
    - docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md for the decision this
      seam is part of.
    - hivemind.llm.ladders.structured and hivemind.llm.ladders.tools for the two callers.
    - hivemind.llm.slots for BoundModel, the value every CallGate call is made through.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.llm.models import LLMRequest, LLMResponse
from hivemind.llm.slots import BoundModel

__all__ = ["CallGate", "DirectCallGate"]


class CallGate(Protocol):
    """Make one model call through a BoundModel; the seam a metering layer can sit behind."""

    async def complete(self, bound: BoundModel, request: LLMRequest) -> LLMResponse:
        """Run `request` against `bound` and return the completed response.

        Args:
            bound: The binding to call; `bound.provider` and `bound.model` decide who answers.
            request: The call to make.

        Returns:
            The completed LLMResponse.

        Raises:
            RateLimitedError: The provider refused the call due to a rate limit.
            ProviderUnavailableError: The provider could not be reached, or is refusing all calls.
            ContextTooLongError: `request` does not fit `bound`'s context window.
        """
        ...


class DirectCallGate:
    """Call `bound.provider.complete` with no metering; every ladder's default gate."""

    async def complete(self, bound: BoundModel, request: LLMRequest) -> LLMResponse:
        """Call straight through to the provider; see `CallGate.complete` for the full contract."""
        # No seat check, no latency measurement: those belong to the Fanner (roadmap 3.12a), which
        # implements this same Protocol once it exists. A caller with no gate gets exactly the
        # provider's own behaviour, unmetered. The binding, not the caller, knows which model id
        # the provider should run, so the gate stamps it here (a copy: requests are frozen).
        return await bound.provider.complete(request.model_copy(update={"model": bound.model}))
