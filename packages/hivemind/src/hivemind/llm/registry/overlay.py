"""Wrap an LLMProvider so a prompt overlay is applied to every request it sees.

An **overlay** (a small per-model addendum to the shared prompts, ``hivemind.llm.prompts.
overlay``) belongs at the provider door, not at a call site (ADR-0044): the registry wraps a
provider whose manifest row names an overlay, and every Worker, Warden and ladder that reaches
that provider through a ``BoundModel`` gets the rewritten system prompt without knowing. The
wrapper is otherwise invisible: it forwards ``name``, ``capabilities``, ``count_tokens``,
``health`` and ``aclose`` untouched, so the registry's structural close and the Fanner's
capability checks keep working.

Fits into the Hive:
    Layer 1 (foundational services), inside hivemind.llm. Built by ``ProviderRegistry.provider``
    when ``ProviderConfig.prompt_overlay`` is set; calls into ``hivemind.llm.prompts.overlay``.

Key invariants:
    - Only ``request.system`` is ever rewritten; every other field and every response is passed
      through exactly as the wrapped provider produced it.
    - The wrapped provider is closed exactly when the wrapper is (``aclose`` forwards).

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md decision 6.
    - hivemind.llm.prompts.overlay for the overlay value and the pure rewrite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from hivemind.llm.capabilities import ProviderCapabilities, ProviderHealth
from hivemind.llm.models import LLMChunk, LLMRequest, LLMResponse
from hivemind.llm.prompts.overlay import Overlay, apply_overlay
from hivemind.llm.provider import LLMProvider

__all__ = ["OverlayingProvider"]


class OverlayingProvider:
    """An LLMProvider that forwards to another one after applying a prompt overlay."""

    def __init__(self, inner: LLMProvider, overlay: Overlay) -> None:
        """Wrap `inner`.

        Args:
            inner: The provider the registry built for the row.
            overlay: The overlay the row selected, already loaded (so a bad name failed earlier).
        """
        self._inner = inner
        self._overlay = overlay

    @property
    def name(self) -> str:
        """Return the wrapped provider's name; the overlay never changes identity."""
        return self._inner.name

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return the wrapped provider's capabilities."""
        return self._inner.capabilities

    @property
    def overlay(self) -> Overlay:
        """Return the overlay applied to this provider's requests."""
        return self._overlay

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run `request` through the overlay, then on the wrapped provider."""
        # External await: the wrapped provider's own latency class and timeouts apply unchanged.
        return await self._inner.complete(apply_overlay(self._overlay, request))

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Stream the overlaid request from the wrapped provider."""
        return self._inner.stream(apply_overlay(self._overlay, request))

    async def count_tokens(self, request: LLMRequest) -> int | None:
        """Estimate the overlaid request, since that is what the provider will actually receive."""
        return await self._inner.count_tokens(apply_overlay(self._overlay, request))

    async def health(self) -> ProviderHealth:
        """Return the wrapped provider's health."""
        return await self._inner.health()

    async def aclose(self) -> None:
        """Close the wrapped provider; idempotent because the wrapped provider's close is."""
        await self._inner.aclose()
