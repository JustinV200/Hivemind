"""Provide OllamaManager: list, pull, keep alive and health for an Ollama server.

Ollama's OpenAI-compatible door (what `OpenAICompatProvider` speaks) can chat and list ids but
cannot download a model or choose how long it stays in memory; its native API can. This manager is
the optional, Ollama-only management half of roadmap step 8.2: it is detected by probing the
native API (`GET /api/version` answering with a `version`), never by a provider name, so any
server that answers that way is managed and every other server (vLLM, llama.cpp, LM Studio) is
left to the generic `/models` health probe the chat adapter already has (ADR-0044).

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.providers.ollama`. Built by the
    composition root (`hivemind.cli.llm`) from a provider's `base_url`; calls `OllamaClient` only.

Key invariants:
    - `detect` and `open` never raise: an unreachable or non-Ollama server is simply `False` or
      `None`, so a health table can ask about every provider without special cases.
    - Every other method raises a typed `hivemind.llm.errors.LLMError` on failure, never an
      `httpx.*` exception or a raw `ValidationError`.
    - No port, host or model id is a default here: everything comes from the caller.

See Also:
    - hivemind.llm.providers.ollama.client for the HTTP layer and the base-URL derivation.
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for the Ollama-only scope.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TypeVar

import httpx
from pydantic import BaseModel, JsonValue, ValidationError

from hivemind.llm.errors import LLMError, MalformedOutputError
from hivemind.llm.models import JsonObject
from hivemind.llm.providers.ollama.client import OllamaClient, native_base_url
from hivemind.llm.providers.ollama.models import (
    OllamaHealth,
    OllamaModel,
    OllamaProcesses,
    OllamaTags,
    OllamaVersion,
)
from hivemind.llm.providers.ollama.protocol import PullProgress

VERSION_PATH = "/api/version"  # Answering this with a `version` is what makes a server Ollama.
TAGS_PATH = "/api/tags"  # Installed models.
PS_PATH = "/api/ps"  # Models loaded in memory.
PULL_PATH = "/api/pull"  # Download a model, streaming progress.
GENERATE_PATH = "/api/generate"  # With no prompt it only loads a model and sets its keep-alive.
DETECT_TIMEOUT_S = 2.0  # A probe of a local server answers at once; a slow one is not Ollama.
PULL_SILENCE_TIMEOUT_S = 300.0  # A large layer may pause this long between progress lines.
LOAD_TIMEOUT_S = 600.0  # Reading a large model from disk into memory can take minutes.
FOREVER = -1  # keep_alive value Ollama reads as "until the server stops".

WireModelT = TypeVar("WireModelT", bound=BaseModel)

__all__ = ["OllamaManager"]


class OllamaManager:
    """Manage one Ollama server's models through its native API."""

    def __init__(self, client: OllamaClient, provider: str) -> None:
        """Wrap a client.

        Args:
            client: The native-API client, built on the server's native base URL.
            provider: The manifest provider name, for error messages.
        """
        self._client = client
        self._provider = provider

    @classmethod
    def create(
        cls,
        provider: str,
        base_url: str,
        timeout_s: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> OllamaManager:
        """Build a manager for the server a provider's `base_url` points at.

        Args:
            provider: The manifest provider name.
            base_url: The provider's `base_url`; a trailing `/v1` is dropped to reach the native
                API on the same host and port.
            timeout_s: Per-request timeout for list, health and probe calls.
            transport: A custom httpx transport, for tests (`httpx.MockTransport`).
        """
        http = httpx.AsyncClient(
            base_url=native_base_url(base_url), timeout=timeout_s, transport=transport
        )
        return cls(OllamaClient(http, provider), provider)

    @classmethod
    async def detect(
        cls,
        base_url: str,
        *,
        timeout_s: float = DETECT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> bool:
        """Return whether the server at `base_url` answers Ollama's native version probe.

        Args:
            base_url: A provider's `base_url`.
            timeout_s: How long to wait for the probe; a local server answers in milliseconds.
            transport: A custom httpx transport, for tests.

        Returns:
            True only for a server whose `/api/version` answers 200 with a `version`; False for
            an unreachable, slow or different server. Never raises.
        """
        manager = cls.create("probe", base_url, timeout_s, transport=transport)
        try:
            return await manager.is_ollama()
        finally:
            await manager.aclose()

    @classmethod
    async def open(cls, provider: str, base_url: str, timeout_s: float) -> OllamaManager | None:
        """Return a manager for `base_url` if it is an Ollama server, else None.

        This is the production `ManagerOpener`: the caller owns the result and must `aclose` it.
        """
        manager = cls.create(provider, base_url, timeout_s)
        found = False
        try:
            found = await manager.is_ollama()
        finally:
            # Closed on "not Ollama" and on anything unexpected the probe let through (a
            # cancellation, say): the caller only ever owns a manager this method returned.
            if not found:
                await manager.aclose()
        return manager if found else None

    async def is_ollama(self) -> bool:
        """Probe the native version endpoint; False on any failure rather than raising."""
        try:
            await self._get(VERSION_PATH, OllamaVersion)
        except (LLMError, ValidationError):
            # Unreachable, a non-2xx, or a body without `version`: not an Ollama server.
            return False
        return True

    async def list_models(self) -> tuple[OllamaModel, ...]:
        """Return the models installed on the server, as `GET /api/tags` lists them.

        Raises:
            ProviderUnavailableError: The server is unreachable or answered 5xx.
            MalformedOutputError: The reply was not the expected shape.
        """
        return (await self._get(TAGS_PATH, OllamaTags)).models

    async def pull(self, model: str) -> AsyncIterator[PullProgress]:
        """Download `model`, yielding each progress line the server streams.

        Args:
            model: The model tag to install.

        Yields:
            `PullProgress` lines in order; the last has `status == "success"` on a completed pull.

        Raises:
            ProviderRequestError: The server refused the model (unknown tag) mid-stream or up front.
            ProviderUnavailableError: The server is unreachable or went away mid-download.
        """
        body: JsonObject = {"model": model, "stream": True}
        async for line in self._client.stream_ndjson(
            PULL_PATH, body, timeout_s=PULL_SILENCE_TIMEOUT_S
        ):
            progress = _progress_from_wire(line)
            if progress is not None:
                yield progress

    async def keep_alive(self, model: str, duration_s: int) -> None:
        """Load `model` and have the server keep it in memory for `duration_s` seconds.

        Args:
            model: An installed model tag.
            duration_s: Seconds to hold it; 0 unloads it now; -1 holds it until the server stops.

        Raises:
            ValueError: `duration_s` is below -1.
            ProviderRequestError: The server does not have `model`.
            ProviderUnavailableError: The server is unreachable.
        """
        if duration_s < FOREVER:
            raise ValueError(
                f"duration_s must be -1 (forever), 0 (unload) or positive: {duration_s}"
            )
        # A generate call with no prompt loads the model and applies keep_alive without generating.
        body: JsonObject = {"model": model, "keep_alive": duration_s, "stream": False}
        await self._client.post_json(GENERATE_PATH, body, timeout_s=LOAD_TIMEOUT_S)

    async def health(self) -> OllamaHealth:
        """Return the server's version and what it has loaded in memory.

        Raises:
            ProviderUnavailableError: The server is unreachable or answered 5xx.
            MalformedOutputError: A reply was not the expected shape.
        """
        version = await self._get(VERSION_PATH, OllamaVersion)
        processes = await self._get(PS_PATH, OllamaProcesses)
        return OllamaHealth(version=version.version, loaded=processes.models)

    async def aclose(self) -> None:
        """Close the HTTP client and its pooled connections."""
        await self._client.aclose()

    async def _get(self, path: str, model: type[WireModelT]) -> WireModelT:
        """GET `path` and validate it as `model`, or raise MalformedOutputError."""
        payload = await self._client.get_json(path)
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise MalformedOutputError(
                self._provider, raw=json.dumps(payload)[:200], attempts=1
            ) from exc


def _progress_from_wire(line: JsonObject) -> PullProgress | None:
    """Map one streamed pull line to a `PullProgress`; None for a line with no status."""
    status = line.get("status")
    if not isinstance(status, str):
        return None
    return PullProgress(
        status=status,
        digest=_text(line.get("digest")),
        total=_count(line.get("total")),
        completed=_count(line.get("completed")),
    )


def _text(value: JsonValue | None) -> str | None:
    """Return `value` if it is a string, else None."""
    return value if isinstance(value, str) else None


def _count(value: JsonValue | None) -> int | None:
    """Return `value` if it is a non-negative integer byte count, else None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
