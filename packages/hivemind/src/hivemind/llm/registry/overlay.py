"""Wrap an LLMProvider so a prompt overlay is applied to every request it sees.

An **overlay** (a small per-model addendum to the shared prompts, ``hivemind.llm.prompts.
overlay``) belongs at the provider door, not at a call site (ADR-0044): the registry wraps a
provider whose manifest row names an overlay, and every Worker, Warden and ladder that reaches
that provider through a ``BoundModel`` gets the rewritten system prompt without knowing. The
wrapper is otherwise invisible: it forwards ``name``, ``capabilities``, ``count_tokens``,
``health`` and ``aclose`` untouched, so the registry's structural close and the Fanner's
capability checks keep working, and it forwards the optional, request-free capability methods a
provider may offer (``list_models``) exactly when the wrapped provider has them, so a caller that
probes for one structurally (``isinstance(provider, SomeProtocol)``) sees what the wrapped
provider offers. They are copied onto the instance rather than reached through ``__getattr__``
because a ``runtime_checkable`` Protocol probe reads attributes statically and never calls
``__getattr__``, so a lazy forward would hide the capability from the very probe it is for.

Fits into the Hive:
    Layer 1 (foundational services), inside hivemind.llm. Built by ``ProviderRegistry.provider``
    when ``ProviderConfig.prompt_overlay`` is set; calls into ``hivemind.llm.prompts.overlay``.

Key invariants:
    - Only ``request.system`` is ever rewritten; every other field and every response is passed
      through exactly as the wrapped provider produced it.
    - The wrapped provider is closed exactly when the wrapper is (``aclose`` forwards).
    - The wrapper never claims a capability the wrapped provider lacks: an optional method the
      wrapped provider does not have is missing on the wrapper too, so a structural probe
      answers the same on both.

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md decision 6.
    - hivemind.llm.prompts.overlay for the overlay value and the pure rewrite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from hivemind.llm.capabilities import ProviderCapabilities, ProviderHealth
from hivemind.llm.models import LLMChunk, LLMRequest, LLMResponse
from hivemind.llm.prompts.overlay import Overlay, apply_overlay
from hivemind.llm.provider import LLMProvider

__all__ = ["OPTIONAL_CAPABILITY_METHODS", "OverlayingProvider"]

# The request-free methods a provider may offer beyond the `LLMProvider` protocol, which callers
# probe for structurally (`hive llm models` for `list_models`). Each is forwarded untouched when
# the wrapped provider has it: there is no request in it to overlay.
OPTIONAL_CAPABILITY_METHODS: tuple[str, ...] = ("list_models",)


class OverlayingProvider:
    """An LLMProvider that forwards to another one after applying a prompt overlay."""

    # Present on an instance exactly when the wrapped provider has it (see `__init__`); the
    # annotation alone adds nothing to the class, so a structural probe stays honest.
    list_models: Callable[[], Awaitable[tuple[str, ...]]]

    def __init__(self, inner: LLMProvider, overlay: Overlay) -> None:
        """Wrap `inner`.

        Args:
            inner: The provider the registry built for the row.
            overlay: The overlay the row selected, already loaded (so a bad name failed earlier).
        """
        self._inner = inner
        self._overlay = overlay
        # Copy each optional capability the wrapped provider offers onto this instance, so a
        # `runtime_checkable` probe (which reads attributes statically) finds it here too.
        for name in OPTIONAL_CAPABILITY_METHODS:
            method = getattr(inner, name, None)
            if callable(method):
                setattr(self, name, method)

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
