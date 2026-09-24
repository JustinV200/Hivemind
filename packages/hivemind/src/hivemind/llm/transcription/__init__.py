"""Hear speech on ModelSlot.TRANSCRIBER: the transcription boundary (roadmap step 6.5a).

Transcription is audio in, text out, on `ModelSlot.TRANSCRIBER` (the one model slot that hears),
with its own protocol rather than a chat call (ADR-0033). This package is that boundary, built the
way `hivemind.llm` builds the chat one: HiveMind's own values (`AudioClip`, `AudioChunk`,
`Transcript`, `TranscriptSegment`), a declared capability shape and the guard every adapter runs,
the `TranscriptionProvider` protocol, a buffered `stream` for adapters without native streaming,
an honest fake, the slot resolver (`BoundTranscriber`, `resolve_transcriber`) and the call seam
(`TranscriptionGate`) the Fanner (the seat meter every model call passes through) implements.
No library or vendor type crosses it: faster-whisper stays inside `hivemind.llm.providers.whisper`
and the OpenAI-compatible wire inside `hivemind.llm.providers.openai_compat.transcription`.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm`. Called by Buzz's
    `listen` tool (the Exoskeleton's ears, roadmap step 6.5) and the Hive Entrance's voice route
    (10.5f), always through a gate; implemented by the adapters under `hivemind.llm.providers`.
    Calls into `hivemind.forage`, `hivemind.llm.capabilities`, `hivemind.llm.errors` and
    `hivemind.llm.slots` only, never into a provider package (the registry does that).

Key invariants:
    - Audio is transient: no model here puts its bytes in a `repr`, and no value here is ever
      written to the Pheromone Trail (the Hive's append-only audit log); the Fanner records only
      a call's slot, provider, model, audio seconds and latency.
    - Whatever is not re-exported here is private to this package (codingrules 5.4).

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the decision this implements.
    - .claude/codingrules.md section 8.6 for the provider independence rules it follows.
    - hivemind.llm.registry for ProviderRegistry.transcriber and bound_transcriber.
    - hivemind.llm.fanner.transcription for FannerTranscriptionGate.

Public API:
    - Values (`models`): AudioClip, AudioChunk, AudioMediaType, Transcript, TranscriptSegment,
      and the bounds MAX_CLIP_BYTES, MAX_PCM_BYTES, LANGUAGE_PATTERN.
    - Capabilities (`capabilities`): TranscriptionCapabilities, check_request,
      DEFAULT_MAX_CLIP_S.
    - The door (`provider`): TranscriptionProvider.
    - Buffered streaming (`buffered`): stream_by_buffering, collect_clip.
    - The fake (`fake`): FakeTranscription, FakeTranscriptionCall.
    - The slot (`binding`): BoundTranscriber, TranscriberLookup, resolve_transcriber.
    - The seam (`gate`): TranscriptionGate, DirectTranscriptionGate, and Ears (a gate plus the
      resolved chain, what a Worker's `listen` tool holds).
    - WAV (`wav`): encode_wav, read_wav_header, WavHeader, PCM_SAMPLE_WIDTH_BYTES.
"""

from hivemind.llm.transcription.binding import (
    BoundTranscriber,
    TranscriberLookup,
    resolve_transcriber,
)
from hivemind.llm.transcription.buffered import collect_clip, stream_by_buffering
from hivemind.llm.transcription.capabilities import (
    DEFAULT_MAX_CLIP_S,
    TranscriptionCapabilities,
    check_request,
)
from hivemind.llm.transcription.fake import FakeTranscription, FakeTranscriptionCall
from hivemind.llm.transcription.gate import DirectTranscriptionGate, Ears, TranscriptionGate
from hivemind.llm.transcription.models import (
    LANGUAGE_PATTERN,
    MAX_CLIP_BYTES,
    MAX_PCM_BYTES,
    AudioChunk,
    AudioClip,
    AudioMediaType,
    Transcript,
    TranscriptSegment,
)
from hivemind.llm.transcription.provider import TranscriptionProvider
from hivemind.llm.transcription.wav import (
    PCM_SAMPLE_WIDTH_BYTES,
    WavHeader,
    encode_wav,
    read_wav_header,
)

__all__ = [
    "DEFAULT_MAX_CLIP_S",
    "LANGUAGE_PATTERN",
    "MAX_CLIP_BYTES",
    "MAX_PCM_BYTES",
    "PCM_SAMPLE_WIDTH_BYTES",
    "AudioChunk",
    "AudioClip",
    "AudioMediaType",
    "BoundTranscriber",
    "DirectTranscriptionGate",
    "Ears",
    "FakeTranscription",
    "FakeTranscriptionCall",
    "TranscriberLookup",
    "Transcript",
    "TranscriptSegment",
    "TranscriptionCapabilities",
    "TranscriptionGate",
    "TranscriptionProvider",
    "WavHeader",
    "check_request",
    "collect_clip",
    "encode_wav",
    "read_wav_header",
    "resolve_transcriber",
    "stream_by_buffering",
]
