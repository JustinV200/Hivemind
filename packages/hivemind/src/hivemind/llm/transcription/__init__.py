"""Define the transcription boundary: audio in, text out, on the TRANSCRIBER slot.

Roadmap step 6.5a ("Ears") in the subset step 10.5f (voice in at the Landing Board, the
Entrance's public API) needs: HiveMind's own audio and transcript models, bounded once for every
provider; the `TranscriptionProvider` protocol and its declared capabilities; an honest,
scriptable fake; and `BoundTranscriber`, the TRANSCRIBER slot bound to live providers through the
same `[llm.slots]` chain rules chat slots follow. The OpenAI-compatible adapter lives with its
siblings under `hivemind.llm.providers.openai_compat`; metering lives with the Fanner (the seat
meter every model call passes through) as `hivemind.llm.fanner.MeteredTranscriber`; binding is
`hivemind.llm.registry.ProviderRegistry.transcriber`.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm`. Called by the Entrance's voice route
    (roadmap step 10.5f) and later by Buzz, the Exoskeleton's audio (roadmap step 6.5). Imports
    `hivemind.forage`, `hivemind.common` and `hivemind.llm`'s own boundary modules only; no
    vendor client, which stays under `hivemind.llm.providers` (codingrules section 8.6).

Key invariants:
    - No library type crosses this boundary: clips and transcripts are this package's models.
    - Audio and transcript text are C2 and never appear in a repr, a log line or the trail.
    - A provider kind that cannot transcribe is refused when the slot is bound, never on the
      first call.

See Also:
    - .claude/codingrules.md section 8.6 for the LLM provider independence rules.
    - .claude/codingrules.md section 8.15, "Voice is transcribed at the door".
    - .claude/roadmap.md steps 6.5a and 10.5f.
    - hivemind.llm.README for what of 6.5a this package does not yet cover.

Public API:
    - Models (`models`): AudioClip (with `from_upload`), AudioChunk, Transcript,
      TranscriptSegment, clip_from_chunks, normalise_language, MAX_CLIP_BYTES, MAX_CLIP_SECONDS,
      MAX_TRANSCRIPT_CHARS, MAX_TRANSCRIPT_SEGMENTS, MAX_LANGUAGE_CHARS.
    - Formats (`media`): AudioMediaType.
    - WAV headers (`wav`): wav_duration_s.
    - The door (`provider`): TranscriptionProvider, TranscriptionCapabilities.
    - The fake (`fake`): FakeTranscription, TranscriptionCall, FAKE_DETECTED_LANGUAGE,
      UNSUPPORTED_MEDIA_TYPE_STATUS.
    - Binding (`binding`): BoundTranscriber, TranscriberLookup, resolve_transcriber,
      SECONDS_PER_MINUTE.
    - Errors (`errors`): InvalidAudioClipError, ClipProblem, TranscriptionUnsupportedError.
"""

from hivemind.llm.transcription.binding import (
    SECONDS_PER_MINUTE,
    BoundTranscriber,
    TranscriberLookup,
    resolve_transcriber,
)
from hivemind.llm.transcription.errors import (
    ClipProblem,
    InvalidAudioClipError,
    TranscriptionUnsupportedError,
)
from hivemind.llm.transcription.fake import (
    FAKE_DETECTED_LANGUAGE,
    UNSUPPORTED_MEDIA_TYPE_STATUS,
    FakeTranscription,
    TranscriptionCall,
)
from hivemind.llm.transcription.media import AudioMediaType
from hivemind.llm.transcription.models import (
    MAX_CLIP_BYTES,
    MAX_CLIP_SECONDS,
    MAX_LANGUAGE_CHARS,
    MAX_TRANSCRIPT_CHARS,
    MAX_TRANSCRIPT_SEGMENTS,
    AudioChunk,
    AudioClip,
    Transcript,
    TranscriptSegment,
    clip_from_chunks,
    normalise_language,
)
from hivemind.llm.transcription.provider import TranscriptionCapabilities, TranscriptionProvider
from hivemind.llm.transcription.wav import wav_duration_s

__all__ = [
    "FAKE_DETECTED_LANGUAGE",
    "MAX_CLIP_BYTES",
    "MAX_CLIP_SECONDS",
    "MAX_LANGUAGE_CHARS",
    "MAX_TRANSCRIPT_CHARS",
    "MAX_TRANSCRIPT_SEGMENTS",
    "SECONDS_PER_MINUTE",
    "UNSUPPORTED_MEDIA_TYPE_STATUS",
    "AudioChunk",
    "AudioClip",
    "AudioMediaType",
    "BoundTranscriber",
    "ClipProblem",
    "FakeTranscription",
    "InvalidAudioClipError",
    "TranscriberLookup",
    "Transcript",
    "TranscriptSegment",
    "TranscriptionCall",
    "TranscriptionCapabilities",
    "TranscriptionProvider",
    "TranscriptionUnsupportedError",
    "clip_from_chunks",
    "normalise_language",
    "resolve_transcriber",
    "wav_duration_s",
]
