"""Build audio fixtures for transcription tests: silent WAV clips, generated, never checked in.

Codingrules 14.3 asks for every `TranscriptionProvider` to pass its contract suite against
fixture clips with no network; this module makes those clips on the fly with the standard
library's `wave` module (a real RIFF/WAVE container, silence inside), so no binary file is
committed and a test states the exact length and rate it needs. `make_clip` wraps one as a
validated `AudioClip` through the same `AudioClip.from_upload` a device's upload goes through;
`make_transcript` builds a scripted `Transcript` with neutral defaults.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the transcription unit
    tests, the Fanner's metering tests and the transcription provider contract suite.

Key invariants:
    - Every clip is 16-bit mono PCM silence: the smallest honest WAV a Whisper-style server
      accepts, and one whose header duration is exactly `seconds` for any whole number of frames.
    - Builders return validated models; nothing bypasses validation (codingrules 14.5).

See Also:
    - hivemind.llm.transcription.models for AudioClip and Transcript.
    - hivemind.llm.transcription.wav for the header reader these clips exercise.
"""

from __future__ import annotations

import io
import wave

from hivemind.llm.transcription import (
    AudioClip,
    AudioMediaType,
    Transcript,
    TranscriptSegment,
)

DEFAULT_SAMPLE_RATE = 16_000  # Hz: the rate speech models are trained on.
DEFAULT_SECONDS = 1.0  # Long enough to be a clip, short enough to keep fixtures tiny (32 KB).
_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM.
_MONO = 1

__all__ = ["DEFAULT_SAMPLE_RATE", "DEFAULT_SECONDS", "make_clip", "make_transcript", "silent_wav"]


def silent_wav(seconds: float = DEFAULT_SECONDS, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """Return a 16-bit mono PCM WAV file of `seconds` of silence.

    Args:
        seconds: The clip's length; rounded to a whole number of frames.
        sample_rate: Frames per second.

    Returns:
        The complete file, RIFF header included.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(_MONO)
        writer.setsampwidth(_SAMPLE_WIDTH_BYTES)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00" * (round(seconds * sample_rate) * _SAMPLE_WIDTH_BYTES))
    return buffer.getvalue()


def make_clip(seconds: float = DEFAULT_SECONDS) -> AudioClip:
    """Return a validated WAV AudioClip of `seconds` of silence, built as an upload would be.

    Args:
        seconds: The clip's length.
    """
    return AudioClip.from_upload(silent_wav(seconds), AudioMediaType.WAV)


def make_transcript(
    text: str = "turn on the lights", seconds: float = DEFAULT_SECONDS
) -> Transcript:
    """Return a validated one-segment Transcript of `text` spanning `seconds`.

    Args:
        text: What was "heard".
        seconds: The transcript's duration and its one segment's end.
    """
    segment = TranscriptSegment(start_s=0.0, end_s=seconds, text=text)
    return Transcript(text=text, language="en", duration_s=seconds, segments=(segment,))
