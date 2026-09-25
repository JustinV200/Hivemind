"""Define voice's Landing Board shapes: what a clip became, and the push-to-talk frames.

A clip spoken at the Landing Board (the Hive Entrance's versioned API, roadmap step 10.5f) is
answered with ``VoiceAccepted``: the intent, the transcript and what the words became, a goal
request (echoed back and held for the human's yes, or submitted), an answered question, or a chat
line, each in the view the typed route answers with. The transcript is ``C2`` (the human's own
words) and goes back only to the device that spoke it: in the HTTP answer, or in a ``VoiceFrame``
on that device's own chat socket. Push-to-talk on ``/v1/chat/stream`` is ``AudioChunkFrame``s (the
recording's bytes, base64, in order) then one ``AudioEndFrame`` naming the intent; the view answers
the end frame with a ``VoiceFrame``, or a ``VoiceRefusedFrame`` carrying the same ``ErrorBody`` the
HTTP route would have answered with. The client frames are published closed (the Entrance refuses a
member it does not know); what the view sends is published open, like every frame a client reads.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Used by the
    voice route, the push-to-talk reader and the door; published in the OpenAPI document by
    ``hivemind.entrance.landing_board``. Calls into the Entrance's goal, inbox and chat views, the
    gate's ``ErrorBody``, the transcription boundary's bounds and pydantic.

Key invariants:
    - Exactly one of ``goal``, ``answered`` and ``chat`` is set, the one the intent names.
    - A chunk's bytes are bounded so its frame fits the socket's one-mebibyte frame limit.

See Also:
    - hivemind.entrance.voice.door for what builds a VoiceAccepted.
    - hivemind.entrance.voice.talk for the push-to-talk reader.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from hivemind.entrance.gate.handlers import ErrorBody
from hivemind.entrance.models import AnsweredView, ChatAccepted, GoalView
from hivemind.entrance.voice.intent import INTENT_PATTERN, MAX_INTENT_CHARS, VoiceIntentKind
from hivemind.llm.transcription import MAX_CLIP_SECONDS, MAX_LANGUAGE_CHARS, MAX_TRANSCRIPT_CHARS

# Raw audio in one push-to-talk frame: its base64 (four thirds as long) and the frame's JSON stay
# under the socket's 1 MiB frame limit; a recorder's quarter-second slices are far smaller.
MAX_CHUNK_BYTES = 524_288
# The longest base64 text MAX_CHUNK_BYTES encodes to: what the published schema bounds, since a
# client checks the string it sends, while the Entrance bounds the bytes that string decodes to.
_MAX_CHUNK_CHARS = 4 * math.ceil(MAX_CHUNK_BYTES / 3)
MAX_MEDIA_TYPE_CHARS = 128  # "audio/webm;codecs=opus" and every other label, with room.
_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every model here: immutable, no strays.
# A chunk's bytes travel as base64 in JSON, both ways (the default would read audio as UTF-8).
_BYTES_CONFIG = ConfigDict(
    frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
)

__all__ = [
    "MAX_CHUNK_BYTES",
    "MAX_MEDIA_TYPE_CHARS",
    "AudioChunkFrame",
    "AudioEndFrame",
    "ClientFrame",
    "SpeakParams",
    "VoiceAccepted",
    "VoiceFrame",
    "VoiceRefusedFrame",
    "read_client_frame",
]


class SpeakParams(BaseModel):
    """``POST /v1/chat/audio``'s query: the intent, and what the device knows about the clip."""

    model_config = _CONFIG

    intent: str = Field(
        pattern=INTENT_PATTERN,
        max_length=MAX_INTENT_CHARS,
        description="goal (echoed back for confirmation), chat, or answer:<question id>.",
    )
    duration_s: float | None = Field(
        default=None,
        gt=0,
        le=MAX_CLIP_SECONDS,
        allow_inf_nan=False,
        description="The clip's length in seconds; required for a compressed format, ignored "
        "for WAV (its header is read instead).",
    )
    language: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_LANGUAGE_CHARS,
        description="A language hint (en, en-US); the transcriber detects it when absent.",
    )


class VoiceAccepted(BaseModel):
    """What a clip became (C2: the transcript is the human's own words)."""

    model_config = _CONFIG

    intent: VoiceIntentKind = Field(description="goal, answer or chat.")
    transcript: str = Field(
        max_length=MAX_TRANSCRIPT_CHARS, description="What was heard (C2), trimmed."
    )
    language: str | None = Field(description="The language heard, when the transcriber named it.")
    duration_s: float = Field(ge=0, description="The clip's length in seconds, as transcribed.")
    goal: GoalView | None = Field(
        default=None,
        description="For a goal: its request, AWAITING_CONFIRMATION when echoed back (confirm "
        "or decline it at /v1/goals/{request_id}), RECEIVED when submitted at once.",
    )
    answered: AnsweredView | None = Field(
        default=None, description="For an answer: the question answered and the task it resumed."
    )
    chat: ChatAccepted | None = Field(default=None, description="For chat: the chat line.")

    @model_validator(mode="after")
    def _one_outcome(self) -> VoiceAccepted:
        """Require exactly the outcome the intent names, and no other."""
        outcomes = {
            VoiceIntentKind.GOAL: self.goal,
            VoiceIntentKind.ANSWER: self.answered,
            VoiceIntentKind.CHAT: self.chat,
        }
        present = {kind for kind, outcome in outcomes.items() if outcome is not None}
        if present != {self.intent}:
            raise ValueError(f"A {self.intent.value} clip carries exactly its own outcome.")
        return self


class AudioChunkFrame(BaseModel):
    """One slice of a push-to-talk recording, sent on the chat socket in order."""

    model_config = _BYTES_CONFIG

    type: Literal["audio_chunk"] = Field(description="Always audio_chunk.")
    media_type: str = Field(
        min_length=1,
        max_length=MAX_MEDIA_TYPE_CHARS,
        description="The recording's format, the same on every chunk, e.g. audio/webm;codecs=opus.",
    )
    data: bytes = Field(
        min_length=1,
        max_length=MAX_CHUNK_BYTES,
        json_schema_extra={"maxLength": _MAX_CHUNK_CHARS},
        description=f"This slice's bytes (at most {MAX_CHUNK_BYTES}), base64: the standard or "
        "the URL-safe alphabet.",
    )
    duration_s: float | None = Field(
        default=None,
        ge=0,
        le=MAX_CLIP_SECONDS,
        allow_inf_nan=False,
        description="This slice's length in seconds; every compressed slice needs one, a WAV "
        "recording's header decides instead.",
    )


class AudioEndFrame(BaseModel):
    """The end of a push-to-talk hold: hear everything sent since the hold began, as ``intent``."""

    model_config = _CONFIG

    type: Literal["audio_end"] = Field(description="Always audio_end.")
    intent: str = Field(
        pattern=INTENT_PATTERN,
        max_length=MAX_INTENT_CHARS,
        description="goal (echoed back for confirmation), chat, or answer:<question id>.",
    )
    language: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_LANGUAGE_CHARS,
        description="A language hint; the transcriber detects it when absent.",
    )


# A frame a client sends on the chat socket after its first: a slice, or the end of a hold.
ClientFrame = Annotated[AudioChunkFrame | AudioEndFrame, Field(discriminator="type")]
_CLIENT_FRAME: TypeAdapter[AudioChunkFrame | AudioEndFrame] = TypeAdapter(ClientFrame)


class VoiceFrame(BaseModel):
    """A push-to-talk hold heard, sent to the socket that spoke it and no other (C2)."""

    model_config = _CONFIG

    type: Literal["voice"] = Field(default="voice", description="Always voice.")
    result: VoiceAccepted = Field(description="What the hold became, as the HTTP route answers.")


class VoiceRefusedFrame(BaseModel):
    """A push-to-talk hold refused, with the body the HTTP route would have answered."""

    model_config = _CONFIG

    type: Literal["voice_refused"] = Field(default="voice_refused", description="Always so.")
    status: int = Field(description="The HTTP status the same refusal is answered with.")
    refusal: ErrorBody = Field(description="Why: a stable code and one sentence.")


def read_client_frame(text: str) -> AudioChunkFrame | AudioEndFrame:
    """Parse one text frame a client sent on the chat socket.

    Args:
        text: The frame's JSON text.

    Returns:
        The chunk or end frame it is.

    Raises:
        pydantic.ValidationError: It is neither, or breaks a bound.
    """
    return _CLIENT_FRAME.validate_json(text)
