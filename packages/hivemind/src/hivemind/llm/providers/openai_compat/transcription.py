"""Define OpenAICompatTranscription: speak the OpenAI-compatible audio transcription wire.

Hosted speech-to-text APIs and local Whisper servers (faster-whisper-server, LocalAI, vLLM and
others) share one HTTP shape: a multipart `POST {base_url}/audio/transcriptions` carrying the file,
the model id, an optional language and a `response_format`, answered with JSON. This module is the
`TranscriptionProvider` for that wire, so moving the TRANSCRIBER slot between a hosted API and a
server on the Hive Stand is a manifest change (codingrules section 8.6). It asks for
`verbose_json`, whose segments carry timestamps, and reads a plain `json` answer (text only) just
as well, so a server that ignores `response_format` still yields a transcript, with no segments.
The wire's handful of field names live here rather than in `mapping.py` (the chat wire's): one
request shape and one response shape, read by nothing else, beside the one class that sends them.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.providers.openai_compat`. Built by the
    registry's transcriber factory (`hivemind.llm.registry`) for a `kind = "openai_compat"`
    provider bound to TRANSCRIBER; called through `hivemind.llm.fanner.MeteredTranscriber`.
    Calls `hivemind.llm.providers.openai_compat.client` for the HTTP mechanics and error mapping,
    `hivemind.llm.transcription` for the models, and `httpx` directly only to build and close
    its client and to probe health.

Key invariants:
    - The API key is a `SecretStr`; only `create()` reads it, to build the one `Authorization`
      header, exactly as the chat adapter does (codingrules section 13).
    - Every failure is typed (`hivemind.llm.errors`): unreachable, a 5xx or a timeout is
      `ProviderUnavailableError`; a 429 `RateLimitedError`; any other 4xx, a format the provider
      does not decode (415) or an unreadable answer `ProviderRequestError`.
    - Every call is bounded twice: httpx's per-phase timeout (`CONNECT_TIMEOUT_S` to connect,
      the config's `timeout_s` for the rest) and a whole-call `asyncio.timeout(timeout_s)`, since
      a server that drips its answer could otherwise stretch past every per-read limit.
    - `health()` never raises; `aclose()` closes the one httpx client and is idempotent.
    - No audio byte and no transcript word is ever logged or put in an error message.

See Also:
    - .claude/codingrules.md section 8.6 for the provider independence rules.
    - hivemind.llm.transcription.provider for the protocol this implements.
    - hivemind.llm.providers.openai_compat.client for post_form and the error mapping.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, ValidationError

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.errors import ProviderRequestError, ProviderUnavailableError
from hivemind.llm.models import JsonObject
from hivemind.llm.providers.openai_compat.client import OpenAICompatClient
from hivemind.llm.transcription import (
    MAX_LANGUAGE_CHARS,
    AudioChunk,
    AudioClip,
    Transcript,
    TranscriptionCapabilities,
    TranscriptSegment,
    clip_from_chunks,
    normalise_language,
)
from waggle.clock import Clock

# Relative to a base_url that already ends in its API version segment (".../v1"), as the chat
# adapter's paths are.
TRANSCRIPTIONS_PATH = "/audio/transcriptions"
MODELS_PATH = "/models"
DEFAULT_TRANSCRIPTION_TIMEOUT_S = 120.0  # A two-minute clip on a CPU server takes about as long.
CONNECT_TIMEOUT_S = 10.0  # A server that cannot accept a connection in 10 s is down, not busy.
HEALTH_TIMEOUT_S = 10.0  # A liveness probe answers fast or reads as down; it never transcribes.
UNSUPPORTED_MEDIA_TYPE_STATUS = 415  # What a server answers a format it cannot decode with.
_VERBOSE_FORMAT = "verbose_json"  # The response format whose answer carries timed segments.
_UPLOAD_STEM = "clip"  # The upload's filename before its extension; servers read the extension.
_UNAUTHORIZED_STATUSES = frozenset({401, 403})  # Credentials refused: as unusable as no answer.
_OK_STATUS = 200

__all__ = [
    "CONNECT_TIMEOUT_S",
    "DEFAULT_TRANSCRIPTION_TIMEOUT_S",
    "HEALTH_TIMEOUT_S",
    "TRANSCRIPTIONS_PATH",
    "OpenAICompatTranscription",
    "OpenAICompatTranscriptionConfig",
]


class OpenAICompatTranscriptionConfig(BaseModel):
    """One `kind = "openai_compat"` provider serving the TRANSCRIBER slot, validated.

    `model` comes from the `[llm.slots]` row that binds it, not the provider row: one server can
    host a chat model and a speech model, and each binding names its own.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(description="The server's base URL, including its API version segment.")
    model: str = Field(description="The speech model id sent with every upload.")
    api_key: SecretStr | None = Field(
        default=None,
        description="A bearer token for servers that need one; None for a local server. Never "
        "logged or repr'd (SecretStr).",
    )
    timeout_s: float = Field(
        default=DEFAULT_TRANSCRIPTION_TIMEOUT_S,
        gt=0,
        description="The longest one transcription may take, in seconds, end to end.",
    )
    capabilities: TranscriptionCapabilities = Field(
        default_factory=TranscriptionCapabilities.full,
        description="What this server can do; a verbose_json server has every capability.",
    )


class OpenAICompatTranscription:
    """The `TranscriptionProvider` for `kind = "openai_compat"`: one server's transcription wire.

    Never branches on its own name; every difference in behaviour comes from its capabilities.
    """

    def __init__(
        self,
        name: str,
        config: OpenAICompatTranscriptionConfig,
        client: httpx.AsyncClient,
        clock: Clock,
    ) -> None:
        """Wrap an already-built httpx client; prefer `create()` outside tests.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            client: An httpx client already carrying the base URL, auth header and timeouts;
                accepted directly so a test can pass one over `httpx.MockTransport`.
            clock: Source of `health()`'s `checked_at`.
        """
        self._name = name
        self._config = config
        self._http = client
        self._clock = clock
        # A transcriber has no context window, so no 400 is ever read as an overflow.
        self._client = OpenAICompatClient(client, name, context_window=None)

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
            A provider ready to transcribe; nothing here does I/O (codingrules section 5.5).
        """
        headers: dict[str, str] = {}
        if config.api_key is not None:
            # .get_secret_value() is the only place the key ever leaves its SecretStr.
            headers["Authorization"] = f"Bearer {config.api_key.get_secret_value()}"
        timeout = httpx.Timeout(config.timeout_s, connect=CONNECT_TIMEOUT_S)
        http = httpx.AsyncClient(base_url=config.base_url, timeout=timeout, headers=headers)
        return cls(name, config, http, clock)

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key."""
        return self._name

    @property
    def capabilities(self) -> TranscriptionCapabilities:
        """Return this server's declared capabilities."""
        return self._config.capabilities

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        """Upload `clip` and read back its transcript; see `TranscriptionProvider.transcribe`."""
        hint = normalise_language(language)
        if clip.media_type not in self._config.capabilities.media_types:
            # Refused before any upload, the same way the server itself would refuse it.
            raise ProviderRequestError(
                self._name,
                UNSUPPORTED_MEDIA_TYPE_STATUS,
                error_type="unsupported_media_type",
                detail=f"{clip.media_type.value} is not a format this provider decodes",
            )
        upload = (f"{_UPLOAD_STEM}.{clip.media_type.extension}", clip.data, clip.media_type.value)
        try:
            # External await, seconds to minutes (the model runs on the whole clip); on timeout
            # the call is abandoned and reads as the provider being unavailable.
            async with asyncio.timeout(self._config.timeout_s):
                payload = await self._client.post_form(
                    TRANSCRIPTIONS_PATH, _form_fields(self._config.model, hint), {"file": upload}
                )
        except TimeoutError as exc:
            raise ProviderUnavailableError(
                self._name, f"no transcript within {self._config.timeout_s:.0f}s"
            ) from exc
        return self._read_transcript(payload, clip, hint)

    async def stream(
        self, chunks: AsyncIterable[AudioChunk], language: str | None = None
    ) -> Transcript:
        """Gather the frames into one clip and upload that; see `TranscriptionProvider.stream`."""
        clip = await clip_from_chunks(chunks)
        return await self.transcribe(clip, language)

    async def health(self) -> ProviderHealth:
        """Probe `GET /models`; see `TranscriptionProvider.health`. Never raises."""
        try:
            # External await, bounded by HEALTH_TIMEOUT_S; a timeout reads as DOWN below.
            response = await self._http.get(MODELS_PATH, timeout=HEALTH_TIMEOUT_S)
        except httpx.TransportError as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return ProviderHealth(
                state=HealthState.DOWN, detail=detail, checked_at=self._clock.now()
            )
        state, detail = _health_from_status(response.status_code)
        return ProviderHealth(state=state, detail=detail, checked_at=self._clock.now())

    async def aclose(self) -> None:
        """Close the httpx client and its pooled connections; idempotent."""
        # Local work in milliseconds; the registry bounds it with PROVIDER_CLOSE_TIMEOUT_S.
        await self._http.aclose()

    def _read_transcript(
        self, payload: JsonObject, clip: AudioClip, hint: str | None
    ) -> Transcript:
        """Turn a verbose_json or plain json answer into a Transcript.

        Raises:
            ProviderRequestError: The answer has no text, or does not fit a Transcript.
        """
        text = payload.get("text")
        if not isinstance(text, str):
            raise ProviderRequestError(
                self._name, _OK_STATUS, detail="the transcription answer carried no text"
            )
        try:
            return Transcript(
                text=text.strip(),
                language=hint if hint is not None else self._detected_language(payload),
                duration_s=_duration(payload, clip),
                segments=_segments(payload) if self._config.capabilities.segments else (),
            )
        except ValidationError as exc:
            # A segment that is not an object of numbers and text, or an answer past a bound:
            # the server spoke, but not in a shape the Hive can carry.
            raise ProviderRequestError(
                self._name, _OK_STATUS, detail="the transcription answer did not fit a Transcript"
            ) from exc

    def _detected_language(self, payload: JsonObject) -> str | None:
        """Return the language the server reports, when this provider declares detection."""
        language = payload.get("language")
        if not self._config.capabilities.language_detection or not isinstance(language, str):
            return None
        # "en" from most servers, "english" from some; anything longer is not a language name.
        return language if 0 < len(language) <= MAX_LANGUAGE_CHARS else None


def _form_fields(model: str, hint: str | None) -> dict[str, str]:
    """Build the upload's text fields: the model, the format, and the language when hinted."""
    fields = {"model": model, "response_format": _VERBOSE_FORMAT}
    if hint is not None:
        fields["language"] = hint
    return fields


def _duration(payload: JsonObject, clip: AudioClip) -> float:
    """Return the server's measured duration when it reports one, else the clip's own."""
    reported = payload.get("duration")
    # bool is an int subclass in Python; a JSON true is not a duration.
    if isinstance(reported, int | float) and not isinstance(reported, bool) and reported >= 0:
        return float(reported)
    return clip.duration_s


def _segments(payload: JsonObject) -> tuple[TranscriptSegment, ...]:
    """Read verbose_json's timed segments; () when the answer is plain json with none.

    Raises:
        ValidationError: A segment is not an object whose start, end and text fit the model.
    """
    raw = payload.get("segments")
    if not isinstance(raw, list):
        return ()
    return tuple(_segment(entry) for entry in raw)


def _segment(entry: JsonValue) -> TranscriptSegment:
    """Read one segment object; pydantic validates each value's type and range."""
    fields = entry if isinstance(entry, dict) else {}
    text = fields.get("text")
    return TranscriptSegment.model_validate(
        {
            "start_s": fields.get("start"),
            "end_s": fields.get("end"),
            "text": text.strip() if isinstance(text, str) else text,
        }
    )


def _health_from_status(status_code: int) -> tuple[HealthState, str]:
    """Map `GET /models`'s status to a reading.

    Returns:
        HEALTHY for 200; DOWN for refused credentials (401, 403), which are as unusable as no
        answer; DEGRADED for anything else (a 429 or 5xx: the server answered, unhappily).
    """
    if status_code == _OK_STATUS:
        return HealthState.HEALTHY, "ok"
    if status_code in _UNAUTHORIZED_STATUSES:
        return HealthState.DOWN, f"unauthorized ({status_code})"
    return HealthState.DEGRADED, f"status {status_code}"
