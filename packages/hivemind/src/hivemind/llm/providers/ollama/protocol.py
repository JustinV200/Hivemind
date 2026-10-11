"""Define ModelServerManager: what the CLI may ask of a local model server beyond chat.

Every local server speaks the OpenAI-compatible door through one adapter, but only some can also be
managed: listing what is installed, downloading a model, and holding one in memory. Today that is
Ollama alone (ADR-0044), yet the CLI must not import Ollama's concrete manager to test its own
commands, so the capability is a Protocol (codingrules 8.1) with `OllamaManager` and the in-memory
`FakeOllamaManager` as its implementations. `PullProgress` is the small value a pull streams.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.providers.ollama`. Implemented by
    `manage.OllamaManager` and `fake.FakeOllamaManager`; consumed by `hivemind.cli.llm`.

Key invariants:
    - A manager owns one HTTP connection pool; `aclose` must be called once when done.
    - `pull` is an async iterator whose last item has `status == "success"` when the model is
      fully installed; a failure raises a typed `hivemind.llm.errors.LLMError` instead.

See Also:
    - hivemind.llm.providers.ollama.manage for the real implementation.
    - hivemind.llm.providers.ollama.fake for the in-memory one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from hivemind.llm.providers.ollama.models import OllamaHealth, OllamaModel

__all__ = ["SUCCESS_STATUS", "ManagerOpener", "ModelServerManager", "PullProgress"]

SUCCESS_STATUS = "success"  # The status line Ollama ends a completed pull with.


@dataclass(frozen=True, slots=True)
class PullProgress:
    """One progress line of a model download."""

    status: str  # What the server is doing: "pulling manifest", "pulling <layer>", "success", ...
    digest: str | None  # The layer being downloaded, when the line is about one.
    total: int | None  # Bytes in that layer, when the server knows.
    completed: int | None  # Bytes of that layer received so far, when the server says.

    @property
    def fraction(self) -> float | None:
        """Return the layer's completion in [0, 1], or None when the line carries no byte counts."""
        if self.total is None or self.completed is None or self.total <= 0:
            return None
        return min(self.completed / self.total, 1.0)


class ModelServerManager(Protocol):
    """Manage the models on one local server: list, pull, hold in memory, report health."""

    async def list_models(self) -> tuple[OllamaModel, ...]:
        """Return the models installed on the server."""
        ...

    def pull(self, model: str) -> AsyncIterator[PullProgress]:
        """Download `model`, yielding progress lines until its final `success` line."""
        ...

    async def keep_alive(self, model: str, duration_s: int) -> None:
        """Load `model` and ask the server to keep it in memory for `duration_s` seconds.

        Args:
            model: An installed model's tag.
            duration_s: Seconds to hold it; 0 unloads it now, -1 holds it until the server stops.
        """
        ...

    async def health(self) -> OllamaHealth:
        """Return the server's version and the models loaded in memory right now."""
        ...

    async def aclose(self) -> None:
        """Release the manager's connections."""
        ...


# Opens a manager for (provider name, base_url, timeout_s), or returns None when the server at
# `base_url` is not one this package can manage. The CLI takes it as its only seam to a server.
ManagerOpener = Callable[[str, str, float], Awaitable[ModelServerManager | None]]
