"""Define every way the voice door refuses a clip, each with the status a device is answered with.

Voice at the Landing Board (roadmap step 10.5f) refuses a clip for reasons no other route has: its
intent is not one the door knows, it is not a clip the Hive accepts (a format no transcriber takes,
more bytes or seconds than allowed, a compressed clip with no stated length), its device is over
its budget of audio seconds, the transcriber failed, or it held no words (or more than its intent
takes). Each is an ``EntranceError`` with its own stable code; the ones no
``hivemind.common.errors`` category covers declare the HTTP status they are answered with
(``http_status``, read by ``hivemind.entrance.gate.handlers.status_for``), so the route answers
them with an ``ErrorBody`` and the chat socket's push-to-talk sends the same body as a refusal
frame. Every refusal before transcription happens before any model runs.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Raised by the
    intent parser, the voice route, the push-to-talk reader and the voice door. Calls into
    ``hivemind.entrance.errors``, the gate's ``RateLimitedError`` and the transcription boundary's
    ``ClipProblem`` only.

Key invariants:
    - Every class sets its own code; no message carries audio, a transcript or a device's words.

See Also:
    - hivemind.entrance.gate.handlers for how a refusal becomes a status and a body.
    - hivemind.llm.transcription.errors for the clip problems the door maps.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import ClassVar

from hivemind.common.errors import NotFoundError
from hivemind.entrance.errors import EntranceError
from hivemind.entrance.gate.errors import RateLimitedError
from hivemind.llm.transcription import ClipProblem, InvalidAudioClipError

PAYLOAD_TOO_LARGE = 413  # A clip longer (or larger) than the Hive accepts.
UNSUPPORTED_MEDIA_TYPE = 415  # A format no transcriber here decodes, or none named at all.
UNPROCESSABLE = 422  # Well-formed HTTP carrying a clip or an intent the door cannot use.
UNAVAILABLE = 503  # The transcriber could not be reached, refused, or ran out of time.
# The status each of the transcription boundary's clip problems is answered with.
_CLIP_STATUSES: Mapping[ClipProblem, int] = MappingProxyType(
    {
        ClipProblem.TOO_LARGE: PAYLOAD_TOO_LARGE,
        ClipProblem.TOO_LONG: PAYLOAD_TOO_LARGE,
        ClipProblem.UNSUPPORTED_FORMAT: UNSUPPORTED_MEDIA_TYPE,
        ClipProblem.MISSING_DURATION: UNPROCESSABLE,
        ClipProblem.MALFORMED: UNPROCESSABLE,
    }
)

__all__ = [
    "AudioOverBudgetError",
    "ClipRefusedError",
    "InvalidIntentError",
    "NothingHeardError",
    "PushToTalkError",
    "TranscriptTooLongError",
    "TranscriptionFailedError",
    "VoiceNotServedError",
]


class VoiceNotServedError(EntranceError, NotFoundError):
    """Raise when a clip reaches a Hive whose voice is off (``[entrance.voice] enabled``).

    The HTTP route is not even mounted then (a 404 from the router); this is the same answer for
    a push-to-talk hold on the chat socket, which stays open for the chat itself.
    """

    code: ClassVar[str] = "hivemind.entrance.voice_not_served"

    def __init__(self) -> None:
        """Build the error; one fixed message."""
        super().__init__("This Hive does not take voice: [entrance.voice] is off.")


class InvalidIntentError(EntranceError):
    """Raise when a clip's intent is none of goal, chat or answer:<question id>."""

    code: ClassVar[str] = "hivemind.entrance.invalid_intent"
    http_status: ClassVar[int] = UNPROCESSABLE

    def __init__(self, detail: str) -> None:
        """Build the error.

        Args:
            detail: What is wrong with the intent, never the intent's own text.
        """
        super().__init__(f"This clip's intent cannot be used: {detail}.")


class ClipRefusedError(EntranceError):
    """Raise when a clip is not one the Hive accepts: its format, its size or its length."""

    code: ClassVar[str] = "hivemind.entrance.clip_refused"

    def __init__(self, problem: ClipProblem, detail: str) -> None:
        """Build the error for one refused clip.

        Args:
            problem: Which rule the clip broke; decides the status.
            detail: The sizes, lengths or format involved, never any of the audio.
        """
        super().__init__(f"Audio clip refused ({problem.value}): {detail}.")
        self.problem = problem
        self.http_status = _CLIP_STATUSES[problem]

    @classmethod
    def from_invalid(cls, error: InvalidAudioClipError) -> ClipRefusedError:
        """Answer the transcription boundary's own refusal of a clip.

        Args:
            error: What ``AudioClip.from_upload`` or ``clip_from_chunks`` raised.

        Returns:
            The same refusal, as the door answers it.
        """
        detail = str(error).split(": ", 1)[-1].rstrip(".")
        return cls(error.problem, detail)


class AudioOverBudgetError(RateLimitedError):
    """Raise when a device has sent more audio this minute than ``audio_seconds_per_minute``."""

    code: ClassVar[str] = "hivemind.entrance.audio_over_budget"

    def __init__(self) -> None:
        """Build the error; one fixed message, since the budget is the manifest's."""
        # Past RateLimitedError's own fixed sentence, which speaks of requests, not audio.
        super(RateLimitedError, self).__init__(
            "This device has sent its audio budget for now; speak again shortly."
        )


class TranscriptionFailedError(EntranceError):
    """Raise when the transcriber failed on a clip: down, rate limited, refused or too slow."""

    code: ClassVar[str] = "hivemind.entrance.transcription_failed"
    http_status: ClassVar[int] = UNAVAILABLE

    def __init__(self) -> None:
        """Build the error; one fixed message, since the cause is on the trail and in the log."""
        super().__init__("The transcriber could not hear this clip; try again shortly.")


class NothingHeardError(EntranceError):
    """Raise when a clip's transcript holds no words at all."""

    code: ClassVar[str] = "hivemind.entrance.nothing_heard"
    http_status: ClassVar[int] = UNPROCESSABLE

    def __init__(self) -> None:
        """Build the error; one fixed message."""
        super().__init__("Nothing was heard in this clip.")


class TranscriptTooLongError(EntranceError):
    """Raise when a transcript is longer than what its intent becomes may hold."""

    code: ClassVar[str] = "hivemind.entrance.transcript_too_long"
    http_status: ClassVar[int] = UNPROCESSABLE

    def __init__(self, chars: int, limit: int) -> None:
        """Build the error.

        Args:
            chars: The transcript's length in characters.
            limit: The most its intent takes.
        """
        super().__init__(f"The transcript has {chars} characters; this intent takes {limit}.")


class PushToTalkError(EntranceError):
    """Raise when a push-to-talk hold on the chat socket breaks the frame protocol."""

    code: ClassVar[str] = "hivemind.entrance.push_to_talk_refused"
    http_status: ClassVar[int] = UNPROCESSABLE

    def __init__(self, detail: str) -> None:
        """Build the error.

        Args:
            detail: What went wrong with the hold (its frames, its bounds, its timing).
        """
        super().__init__(f"This push-to-talk hold was refused: {detail}.")
