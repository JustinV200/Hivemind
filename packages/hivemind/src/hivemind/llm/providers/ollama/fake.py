"""Provide FakeOllamaManager: an in-memory ModelServerManager for tests and demo paths.

The CLI's `hive llm models|pull|keep-alive` talk to a `ModelServerManager`; this fake is the
honest stand-in that needs no server, port or network (codingrules 14.4: a fake lives beside the
Protocol it implements). It holds a list of installed models, replays a scripted pull, records
every keep-alive it was asked for, and can be told to fail so a caller's error path is exercised.
`opener` wraps one fake as the `ManagerOpener` a command takes, so a test hands the CLI "an Ollama
server" (or, with `None`, "not an Ollama server") in one line.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.providers.ollama`. Used by the CLI's
    tests and `hive doctor`-style demo paths; shipped code, held to the same standard as the real
    manager.

Key invariants:
    - Behaves like `OllamaManager` at the Protocol level: a failure is a typed `LLMError`, `pull`
      ends with a `success` line, and a closed manager is recorded as closed.
    - A model a pull completes is added to the installed list, so `list_models` after a `pull`
      shows it, as it would on a real server.

See Also:
    - hivemind.llm.providers.ollama.protocol for the Protocol this implements.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from hivemind.llm.errors import LLMError
from hivemind.llm.providers.ollama.models import OllamaHealth, OllamaLoadedModel, OllamaModel
from hivemind.llm.providers.ollama.protocol import (
    SUCCESS_STATUS,
    ManagerOpener,
    ModelServerManager,
    PullProgress,
)

FAKE_VERSION = "0.0.0-fake"  # The version string the fake reports; nothing reads it as a release.

__all__ = ["FAKE_VERSION", "FakeOllamaManager"]


class FakeOllamaManager:
    """An in-memory model server manager with scripted pull progress and recorded calls."""

    def __init__(
        self,
        models: Sequence[OllamaModel] = (),
        pull_script: Sequence[PullProgress] = (),
        failure: LLMError | None = None,
    ) -> None:
        """Build a fake.

        Args:
            models: The models "installed" at the start.
            pull_script: The progress lines every `pull` yields before its `success` line.
            failure: When set, every operation raises it, as an unreachable server would.
        """
        self.installed: list[OllamaModel] = list(models)
        self.keep_alive_calls: list[tuple[str, int]] = []
        self.pulled: list[str] = []
        self.closed = False
        self._pull_script = tuple(pull_script)
        self._failure = failure

    async def list_models(self) -> tuple[OllamaModel, ...]:
        """Return the installed models, or raise the scripted failure."""
        self._maybe_fail()
        return tuple(self.installed)

    async def pull(self, model: str) -> AsyncIterator[PullProgress]:
        """Replay the scripted progress, then `success`, and install `model`."""
        self._maybe_fail()
        for line in self._pull_script:
            yield line
        self.pulled.append(model)
        if all(existing.name != model for existing in self.installed):
            self.installed.append(OllamaModel(name=model))
        yield PullProgress(status=SUCCESS_STATUS, digest=None, total=None, completed=None)

    async def keep_alive(self, model: str, duration_s: int) -> None:
        """Record the request; raise the scripted failure if one is set."""
        self._maybe_fail()
        self.keep_alive_calls.append((model, duration_s))

    async def health(self) -> OllamaHealth:
        """Report a healthy fake whose loaded models are those whose last keep-alive was not 0."""
        self._maybe_fail()
        # The latest request for a model decides whether it is resident; 0 unloads it.
        latest: dict[str, int] = dict(self.keep_alive_calls)
        loaded = tuple(
            OllamaLoadedModel(name=name) for name, seconds in sorted(latest.items()) if seconds != 0
        )
        return OllamaHealth(version=FAKE_VERSION, loaded=loaded)

    async def aclose(self) -> None:
        """Record that the manager was released."""
        self.closed = True

    def _maybe_fail(self) -> None:
        """Raise the scripted failure, if any."""
        if self._failure is not None:
            raise self._failure


def opener(manager: FakeOllamaManager | None) -> ManagerOpener:
    """Wrap `manager` as the `ManagerOpener` a command takes; None means "not an Ollama server"."""

    async def open_manager(
        provider: str, base_url: str, timeout_s: float
    ) -> ModelServerManager | None:
        """Return the fake whatever the provider, as a test's one server."""
        return manager

    return open_manager
