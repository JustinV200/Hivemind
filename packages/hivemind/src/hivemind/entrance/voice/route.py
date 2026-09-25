"""Serve ``POST /v1/chat/audio``: a clip, as a raw body with its media type, heard at the door.

An enrolled device speaks to the Hive with one request (ADR-0040: audio arrives as a raw body with
its media type, so no multipart parser is added): the recording as the body, labelled by its
``Content-Type`` (WAV, Ogg or WebM Opus, MP3, M4A), and a query naming the intent (``goal``,
``chat`` or ``answer:<question id>``), the clip's length when it is compressed (a WAV's header is
read instead) and an optional language hint. The body is read whole, within the row's own
allowance, before the request's signature is checked, since the signature covers its SHA-256.
The route is mounted only while ``[entrance.voice]`` is on (``Switch.VOICE``); off, it is a 404 on
both listeners because it was never mounted, exactly as a loopback-only route is on the remote
listener (ADR-0042: the contract still describes it, with ``x-hive-switch``). It needs
``entrance:submit`` and, since it answers with the transcript, ``honey:clearance:c2``; an answer
also needs ``entrance:answer``, checked by the door. Every refusal is the ``ErrorBody`` of its
status: 413 (too long or too large), 415 (a format the Hive does not take), 422, 429 (the device's
audio budget), 503 (the transcriber).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Registered in
    the route table by ``hivemind.entrance.routes.registry``. Calls into the voice door.

Key invariants:
    - The clip is built, and its length read or taken, before anything is heard.
    - The transcript goes back to this request's device alone.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md, the audio intents.
    - hivemind.entrance.voice.door for what hearing does.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query, Request

from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import (
    BOTH_LISTENERS,
    RawBody,
    RouteEffect,
    RouteSpec,
    Switch,
    session_with,
)
from hivemind.entrance.voice.door import Utterance, hear
from hivemind.entrance.voice.errors import (
    PAYLOAD_TOO_LARGE,
    UNAVAILABLE,
    UNSUPPORTED_MEDIA_TYPE,
    ClipRefusedError,
)
from hivemind.entrance.voice.intent import SUBMIT, VoiceIntent
from hivemind.entrance.voice.models import SpeakParams, VoiceAccepted
from hivemind.llm.transcription import (
    MAX_CLIP_BYTES,
    AudioClip,
    AudioMediaType,
    ClipProblem,
    InvalidAudioClipError,
)

AUDIO_PATH = "/v1/chat/audio"  # Beside the chat it speaks into.
AUDIO_READ_TIMEOUT_S = 120.0  # A 25 MB clip over a slow phone uplink; a stalled one lets go.
CONTENT_TYPE_HEADER = "content-type"  # The body's label; what the clip's format is read from.
# The raw bodies the route takes: every accepted format, up to the transcription ceiling.
AUDIO_BODY = RawBody(
    media_types=tuple(format_.value for format_ in AudioMediaType),
    max_bytes=MAX_CLIP_BYTES,
    read_timeout_s=AUDIO_READ_TIMEOUT_S,
)
# Beyond the refusals every row declares: what only a clip can be answered with.
VOICE_REFUSALS: tuple[tuple[int, str], ...] = (
    (PAYLOAD_TOO_LARGE, "The clip is longer than [entrance.voice] max_clip_seconds, or too big."),
    (UNSUPPORTED_MEDIA_TYPE, "The clip's Content-Type is missing or not an accepted format."),
    (UNAVAILABLE, "The transcriber could not hear the clip; try again shortly."),
)

__all__ = ["AUDIO_BODY", "AUDIO_PATH", "VOICE_ROUTES", "clip_from_body"]


async def speak(
    request: Request,
    caller: CallerParam,
    services: Services,
    params: Annotated[SpeakParams, Query()],
) -> VoiceAccepted:
    """Hear the request's body as a clip and deliver its words as the intent says.

    Args:
        request: The request; its body is the clip and its Content-Type the clip's format.
        caller: The admitted caller.
        services: The Entrance's services.
        params: The intent, the clip's stated length and a language hint.

    Returns:
        The transcript and what the words became, for this device alone.
    """
    intent = VoiceIntent.parse(params.intent)
    # Latency: none; the body limit already read and buffered it, and the gate hashed it.
    clip = clip_from_body(
        await request.body(), request.headers.get(CONTENT_TYPE_HEADER), params.duration_s
    )
    return await hear(services, caller, Utterance(clip, intent, params.language))


def clip_from_body(body: bytes, media_type: str | None, duration_s: float | None) -> AudioClip:
    """Build the clip a request carries, refusing what the Hive does not accept.

    Args:
        body: The raw body.
        media_type: Its ``Content-Type``, or None when it named none.
        duration_s: The stated length; required for a compressed format, ignored for WAV.

    Returns:
        The validated clip, its length read from a WAV header or taken as stated.

    Raises:
        ClipRefusedError: No media type, or the clip breaks a transcription-boundary rule.
    """
    if media_type is None:
        raise ClipRefusedError(ClipProblem.UNSUPPORTED_FORMAT, "the request names no Content-Type")
    try:
        return AudioClip.from_upload(body, media_type, duration_s)
    except InvalidAudioClipError as exc:
        raise ClipRefusedError.from_invalid(exc) from exc


VOICE_ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path=AUDIO_PATH,
        listeners=BOTH_LISTENERS,
        access=session_with(SUBMIT, c2=True),
        effect=RouteEffect.INBOX,
        endpoint=speak,
        summary="Speak to the Queen: a clip heard on the transcriber, as a goal, answer or chat.",
        status_code=202,
        response_model=VoiceAccepted,
        mounted_when=Switch.VOICE,
        raw_body=AUDIO_BODY,
        refusals=VOICE_REFUSALS,
    ),
)
