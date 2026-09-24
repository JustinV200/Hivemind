"""Define OpenAICompatTranscription: speech to text over the /audio/transcriptions wire.

Hosted Whisper APIs and local speech servers (faster-whisper-server, whisper.cpp's server, vLLM
serving a Whisper model) all accept the same multipart `POST /audio/transcriptions`, so one
adapter covers them all (ADR-0033) -- the transcription counterpart of the chat side's
`OpenAICompatProvider`, and named for the wire rather than any one vendor for the same reason.
`OpenAICompatTranscriptionConfig` is one `[llm.providers.<name>]` row of `kind = "openai_compat"`
bound on `ModelSlot.TRANSCRIBER` (the model slot that hears), plus that binding's model id;
`OpenAICompatTranscription` is the `TranscriptionProvider` built from it. It declares no native
streaming (the wire returns one reply per upload), so `stream` buffers and uploads once.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside
    `hivemind.llm.providers.openai_compat.transcription`. Built by the provider registry
    (`hivemind.llm.registry`), called through a transcription gate. Calls into this
    sub-package's `client` and `mapping`, `hivemind.llm.capabilities`,
    `hivemind.llm.transcription` and `waggle.clock`; `httpx` only here and in `client`.

Key invariants:
    - The API key, when set, lives in a `SecretStr` and is read once, in `create()`, to build the
      one `Authorization` header; it never reaches a repr, a log or an error (codingrules 13).
    - `transcribe` refuses what `check_request` rules out before any bytes are uploaded.
    - `health()` never raises: every failure becomes a reading.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the decision this implements.
    - hivemind.llm.providers.openai_compat.provider for the chat adapter on the same servers.
    - hivemind.llm.transcription.provider for the TranscriptionProvider Protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.providers.openai_compat.transcription import mapping
from hivemind.llm.providers.openai_compat.transcription.client import TranscriptionClient
from hivemind.llm.transcription import (
    AudioChunk,
    AudioClip,
    Transcript,
    TranscriptionCapabilities,
    TranscriptSegment,
    check_request,
    stream_by_buffering,
)
from waggle.clock import Clock

# Relative to the base URL, which already ends in the server's own "/v1" (codingrules section 13's
# manifest convention), exactly as the chat adapter's own paths are.
TRANSCRIPTIONS_PATH = "/audio/transcriptions"
MODELS_PATH = "/models"  # The health probe: the one cheap GET these servers commonly answer.
_HEALTHY_STATUS = 200  # /models answered normally.
_UNAUTHORIZED_STATUS = 401  # The server answered but refuses these credentials: as good as down.

__all__ = [
    "MODELS_PATH",
    "TRANSCRIPTIONS_PATH",
    "OpenAICompatTranscription",
    "OpenAICompatTranscriptionConfig",
]


class OpenAICompatTranscriptionConfig(BaseModel):
    """One `[llm.providers.<name>]` row of kind `openai_compat`, as a transcriber, validated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(
        description="The server's base URL including its own version segment, e.g. "
        "'http://127.0.0.1:PORT/v1'; every request path here is relative to it."
    )
    model: str = Field(
        min_length=1, description="The model id every upload names; from the manifest binding."
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="A bearer token for a server that needs one; never logged or repr'd.",
    )
    timeout_s: float = Field(
        gt=0, description="The client's timeout per request, in seconds (codingrules 11)."
    )
    capabilities: TranscriptionCapabilities = Field(
        default_factory=TranscriptionCapabilities,
        description="What this server declares: no native streaming, phrase-level segments and "
        "ten-minute clips unless overridden.",
    )


class OpenAICompatTranscription:
    """Transcribe over `POST /audio/transcriptions`: the TranscriptionProvider for the wire.

    Never branch on `self.name`: behaviour differs only by `capabilities`.
    """

    def __init__(
        self,
        name: str,
        config: OpenAICompatTranscriptionConfig,
        client: httpx.AsyncClient,
        clock: Clock,
    ) -> None:
        """Wrap an already-constructed httpx client; prefer `create()` outside of tests.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            client: Carries the base URL, the bearer header and the timeout; a test injects one
                built on `httpx.MockTransport`.
            clock: Source of `health()`'s `checked_at`.
        """
        self._name = name
        self._config = config
        self._http = client
        self._clock = clock
        self._client = TranscriptionClient(client, name)

    @classmethod
    def create(
        cls, name: str, config: OpenAICompatTranscriptionConfig, clock: Clock
    ) -> OpenAICompatTranscription:
        """Build the httpx client from `config` and return a provider wrapping it.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            clock: Source of `health()`'s `checked_at`.

        Returns:
            A provider ready to transcribe; nothing is sent until the first call.
        """
        headers: dict[str, str] = {}
        if config.api_key is not None:
            # .get_secret_value() is the only place the key ever leaves its SecretStr.
            headers["Authorization"] = f"Bearer {config.api_key.get_secret_value()}"
        http = httpx.AsyncClient(
            base_url=config.base_url, timeout=config.timeout_s, headers=headers
        )
        return cls(name, config, http, clock)

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key; never branch on it."""
        return self._name

    @property
    def capabilities(self) -> TranscriptionCapabilities:
        """Return this provider's declared capabilities, from its config."""
        return self._config.capabilities

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        """Upload `clip` and map the reply; see `TranscriptionProvider.transcribe`."""
        # Refuse what the declaration rules out before a single byte is uploaded.
        check_request(self._name, self._config.capabilities, clip, language)
        # External await: the upload plus the server's transcription, bounded by the client's
        # timeout; a timeout surfaces as ProviderUnavailableError from the client.
        payload = await self._client.post_multipart(
            TRANSCRIPTIONS_PATH,
            mapping.request_form(self._config.model, language),
            mapping.request_files(clip),
        )
        return mapping.transcript_from_json(
            payload, clip=clip, language=language, provider=self._name
        )

    def stream(
        self, chunks: AsyncIterator[AudioChunk], language: str | None = None
    ) -> AsyncIterator[TranscriptSegment]:
        """Buffer `chunks` and upload once; see `TranscriptionProvider.stream`.

        The wire answers one upload with one reply, so this adapter declares no native streaming
        and takes the shared buffered path (ADR-0033).
        """
        return stream_by_buffering(self.transcribe, chunks, language, self._name)

    async def health(self) -> ProviderHealth:
        """GET `/models` directly for its raw status; see `TranscriptionProvider.health`.

        Talks to httpx rather than the client so a 5xx (DEGRADED: the server answered) stays
        distinct from a transport failure (DOWN: nothing answered).
        """
        try:
            # External await: one small GET, bounded by the client's own timeout.
            response = await self._http.get(MODELS_PATH)
        except httpx.TransportError as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return ProviderHealth(
                state=HealthState.DOWN, detail=detail, checked_at=self._clock.now()
            )
        state, detail = _health_from_status(response.status_code)
        return ProviderHealth(state=state, detail=detail, checked_at=self._clock.now())


def _health_from_status(status: int) -> tuple[HealthState, str]:
    """Map `/models`'s status to a reading: 200 HEALTHY, 401 DOWN, anything else DEGRADED.

    A server without a `/models` route (some speech-only servers) therefore reads DEGRADED, not
    DOWN: it answered, and its transcriptions may still work.
    """
    if status == _HEALTHY_STATUS:
        return HealthState.HEALTHY, "ok"
    if status == _UNAUTHORIZED_STATUS:
        return HealthState.DOWN, "unauthorized (401)"
    return HealthState.DEGRADED, f"status {status}"
