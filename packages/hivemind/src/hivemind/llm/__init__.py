"""Define the provider-agnostic LLM boundary every model call in the Hive crosses: llm.

Codingrules section 8.6 ("LLM provider independence") is the rule this package makes concrete:
the Hive starts on a hosted model and must be able to move, slot by slot, to a locally hosted one
without touching any code above `hivemind.llm`. Roadmap steps 3.2 and 3.3 build the boundary
itself: HiveMind's own request/response/message shapes (`models`), the declared capability and
health shapes (`capabilities`), the one `LLMProvider` protocol every adapter implements
(`provider`), that protocol's typed error tree (`errors`), a scriptable, honestly-limited fake
implementation for tests and demos (`fake`), and `BoundModel`, the fully resolved
slot-plus-provider-plus-fallback value later roadmap steps build and consume (`slots`). No vendor
SDK is imported here or anywhere else outside `llm/providers/<name>/`; code above this package
sees only the types re-exported below.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by workers, wardens and queen
    whenever a bee is allowed to think with a model, and by the degradation ladders (`hivemind.
    llm.ladders`) that sit on top of `LLMProvider`. Calls into `hivemind.forage` (for `ModelSlot`
    and `Effort`) and never the reverse (codingrules section 4).

Key invariants:
    - `hivemind.llm` never imports a vendor LLM SDK; that is confined to `llm/providers/<name>/`
      and enforced by `lint-imports` (codingrules section 8.6).
    - Every boundary model here is frozen and forbids unknown fields (codingrules section 8.5).
    - `BoundModel.cost_per_million_input_usd`/`cost_per_million_output_usd` are plain floats, not
      a `hivemind.forage` cost type: `forage` may not be imported by anything that would create a
      cycle, and its own cost shape is still under construction elsewhere this phase (see
      `hivemind.llm.slots`'s module docstring for the full reasoning).
    - `hivemind.llm` never imports `hivemind.manifest` (codingrules section 4's Layer 1
      "llm | manifest" independent siblings): `resolve`/`resolve_key` (`hivemind.llm.slots`) and
      `ProviderRegistry` (`hivemind.llm.registry`) take Forage-side and this package's own
      decoupled shapes instead of the manifest's `LlmSection`/`ProviderSpec` directly; the CLI
      composition root (roadmap step 3.21) builds those from a loaded `HiveManifest`.

See Also:
    - .claude/codingrules.md section 8.6 for the LLM provider independence rules this package
      implements.
    - .claude/codingrules.md section 4 for the layer 1 row this package occupies.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision behind this
      package's shape.
    - hivemind.forage for ModelSlot, Effort and Tempo, the types this package reads but never
      re-exports.

Public API:
    - Message shapes (`hivemind.llm.models`): Role, TextPart, ImagePart, AudioPart, MediaPart,
      ToolCallPart, ToolResultPart, ContentPart, Message, JsonObject, ToolDefinition, ToolCall,
      StopReason, Usage, LLMRequest, LLMResponse, LLMChunk, RateLimitSnapshot, TOOL_NAME_PATTERN.
      `AudioPart` and `ToolResultPart.media` are roadmap step 6.5's: a tool result may carry a
      screenshot or a recording, sent only to a model that declares `vision` or `audio`.
    - Capabilities and health (`hivemind.llm.capabilities`): ProviderCapabilities, HealthState,
      ProviderHealth, FULL_CONTEXT_WINDOW_DEFAULT, NONE_CONTEXT_WINDOW_DEFAULT.
    - The one door (`hivemind.llm.provider`): LLMProvider.
    - Errors (`hivemind.llm.errors`): LLMError, RateLimitedError, ProviderUnavailableError,
      ContextTooLongError, RefusedError, MalformedOutputError, UnknownProviderError,
      OfflineViolationError, ProviderRequestError, MAX_RAW_PREVIEW_CHARS.
    - The fake (`hivemind.llm.fake`): FakeLLMProvider, Responder, text_response,
      tool_call_response, FAKE_MODEL_ID, TEXT_STREAM_CHUNK_COUNT, CHARS_PER_TOKEN_ESTIMATE.
    - Slots (`hivemind.llm.slots`): BoundModel, ProviderLookup, UnresolvableSlotError, resolve,
      resolve_key.
    - The provider registry (`hivemind.llm.registry`): ProviderRegistry, RegistryDeps,
      ProviderFactory, ProviderConfig, ProviderKind, PENDING_KINDS, apply_overrides,
      default_factories.
    - Prompts (`hivemind.llm.prompts`): PromptName, SectionLabel, load_prompt, render.

Public API (roadmap step 3.5):
    - Structured output (`hivemind.llm.ladders.structured`): complete_structured,
      StructuredResult, LadderOptions, Rung, NATIVE_SCHEMA_RETRIES, JSON_MODE_RETRIES,
      PROMPTED_JSON_RETRIES.
    - Tool calls (`hivemind.llm.ladders.tools`): run_tool_loop, ToolExecutor, ToolLoopOptions,
      ToolLoopResult, MAX_TOOL_ROUNDS_DEFAULT.
    - Argument validation (`hivemind.llm.ladders.extraction`): validate_arguments.
    - The call seam (`hivemind.llm.ladders.gate`): CallGate, DirectCallGate.
    - Reporting a step-down (`hivemind.llm.ladders.observer`): LadderObserver, FallbackNote,
      FallbackReason, NullLadderObserver, TrailLadderObserver.

Public API (roadmap step 3.12a, extended by step 4.7a):
    - The Fanner and its lanes (`hivemind.llm.fanner`): Fanner, FannerDeps, FannerLane,
      DEFAULT_SEATS, DEFAULT_THROTTLE_S, LLM_CALL_KIND, LLM_SPILL_KIND, LLM_THROTTLED_KIND.
    - Spill-over: SpillReason (now including THROTTLED), SPILL_WAIT_FRACTION.
    - Rate limiting: RateLimit, the manifest-configured ceiling `ProviderRateLimiter` prefers a
      provider's own reported `RateLimitSnapshot` figures over, once it has them.
    - Trail recording: LlmEventRecorder, NullLlmEventRecorder, TrailLlmEventRecorder.

Public API (roadmap step 6.5a, ADR-0033: the transcription boundary, `hivemind.llm.transcription`):
    - Values: AudioClip, AudioChunk, AudioMediaType, Transcript, TranscriptSegment, WavHeader,
      encode_wav, read_wav_header, MAX_CLIP_BYTES, MAX_PCM_BYTES, PCM_SAMPLE_WIDTH_BYTES,
      LANGUAGE_PATTERN.
    - Capabilities and the door: TranscriptionCapabilities, check_request, DEFAULT_MAX_CLIP_S,
      TranscriptionProvider; buffered streaming: stream_by_buffering, collect_clip.
    - The fake: FakeTranscription, FakeTranscriptionCall.
    - The slot and the seam: BoundTranscriber, TranscriberLookup, resolve_transcriber,
      TranscriptionGate, DirectTranscriptionGate, Ears, and the Fanner's FannerTranscriptionGate
      with AUDIO_SECONDS_KEY, the `llm.call` payload key carrying a transcription's seconds.
    - The registry's transcription half: TranscriptionUnsupportedError, IN_PROCESS_KINDS and
      `ProviderRegistry.transcriber`/`bound_transcriber`; its factory table (TranscriptionBuild,
      TranscriptionFactory, default_transcription_factories) from `hivemind.llm.registry` itself.
    - `ProviderCapabilities.audio`: whether a chat provider takes audio directly; when False,
      audio goes through `ModelSlot.TRANSCRIBER` instead.
Public API (roadmap step 7.1, ADR-0036):
    - The embedding boundary (`hivemind.llm.embedding`): EmbeddingRequest, EmbeddingResponse,
      MAX_EMBED_TEXTS, EmbeddingCapabilities, EmbeddingProvider, FakeEmbedding,
      FAKE_EMBED_MODEL_ID, BoundEmbedder, EmbedGate, DirectEmbedGate.
    - The new error (`hivemind.llm.errors`): EmbeddingUnsupportedError.
    - Registry additions: EMBEDDING_ONLY_KINDS, IN_PROCESS_KINDS and `ProviderRegistry.embedder()`;
      the factory table (EmbeddingFactory, default_embedding_factories) from
      `hivemind.llm.registry` itself; `ProviderKind` now includes `"sentence_transformers"`.
"""

from hivemind.llm.capabilities import (
    FULL_CONTEXT_WINDOW_DEFAULT,
    NONE_CONTEXT_WINDOW_DEFAULT,
    HealthState,
    ProviderCapabilities,
    ProviderHealth,
)
from hivemind.llm.embedding import (
    FAKE_EMBED_MODEL_ID,
    MAX_EMBED_TEXTS,
    BoundEmbedder,
    DirectEmbedGate,
    EmbeddingCapabilities,
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbedGate,
    FakeEmbedding,
)
from hivemind.llm.errors import (
    MAX_RAW_PREVIEW_CHARS,
    ContextTooLongError,
    EmbeddingUnsupportedError,
    LLMError,
    MalformedOutputError,
    OfflineViolationError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
    RefusedError,
    UnknownProviderError,
)
from hivemind.llm.fake import (
    CHARS_PER_TOKEN_ESTIMATE,
    FAKE_MODEL_ID,
    TEXT_STREAM_CHUNK_COUNT,
    FakeLLMProvider,
    Responder,
    text_response,
    tool_call_response,
)
from hivemind.llm.fanner import (
    AUDIO_SECONDS_KEY,
    DEFAULT_SEATS,
    DEFAULT_THROTTLE_S,
    LLM_CALL_KIND,
    LLM_SPILL_KIND,
    LLM_THROTTLED_KIND,
    SPILL_WAIT_FRACTION,
    CompositeLlmEventRecorder,
    Fanner,
    FannerDeps,
    FannerLane,
    FannerTranscriptionGate,
    LlmEventRecorder,
    NullLlmEventRecorder,
    RateLimit,
    SpillReason,
    TrailLlmEventRecorder,
)
from hivemind.llm.ladders import (
    JSON_MODE_RETRIES,
    MAX_TOOL_ROUNDS_DEFAULT,
    NATIVE_SCHEMA_RETRIES,
    PROMPTED_JSON_RETRIES,
    CallGate,
    DirectCallGate,
    FallbackNote,
    FallbackReason,
    LadderObserver,
    LadderOptions,
    NullLadderObserver,
    Rung,
    StructuredResult,
    ToolExecutor,
    ToolLoopOptions,
    ToolLoopResult,
    TrailLadderObserver,
    complete_structured,
    run_tool_loop,
    validate_arguments,
)
from hivemind.llm.models import (
    TOOL_NAME_PATTERN,
    AudioPart,
    ContentPart,
    ImagePart,
    JsonObject,
    LLMChunk,
    LLMRequest,
    LLMResponse,
    MediaPart,
    Message,
    RateLimitSnapshot,
    Role,
    StopReason,
    TextPart,
    ToolCall,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
    Usage,
)
from hivemind.llm.prompts import PromptName, SectionLabel, load_prompt, render
from hivemind.llm.provider import LLMProvider
from hivemind.llm.registry import (
    EMBEDDING_ONLY_KINDS,
    IN_PROCESS_KINDS,
    PENDING_KINDS,
    MissingDefaultModelError,
    ProviderConfig,
    ProviderFactory,
    ProviderKind,
    ProviderRegistry,
    RegistryDeps,
    TranscriptionUnsupportedError,
    apply_overrides,
    default_factories,
)
from hivemind.llm.slots import (
    BoundModel,
    ProviderLookup,
    UnresolvableSlotError,
    resolve,
    resolve_key,
    walk_chain,
)
from hivemind.llm.transcription import (
    DEFAULT_MAX_CLIP_S,
    LANGUAGE_PATTERN,
    MAX_CLIP_BYTES,
    MAX_PCM_BYTES,
    PCM_SAMPLE_WIDTH_BYTES,
    AudioChunk,
    AudioClip,
    AudioMediaType,
    BoundTranscriber,
    DirectTranscriptionGate,
    Ears,
    FakeTranscription,
    FakeTranscriptionCall,
    TranscriberLookup,
    Transcript,
    TranscriptionCapabilities,
    TranscriptionGate,
    TranscriptionProvider,
    TranscriptSegment,
    WavHeader,
    check_request,
    collect_clip,
    encode_wav,
    read_wav_header,
    resolve_transcriber,
    stream_by_buffering,
)

__all__ = [
    "AUDIO_SECONDS_KEY",
    "CHARS_PER_TOKEN_ESTIMATE",
    "DEFAULT_MAX_CLIP_S",
    "DEFAULT_SEATS",
    "DEFAULT_THROTTLE_S",
    "EMBEDDING_ONLY_KINDS",
    "FAKE_EMBED_MODEL_ID",
    "FAKE_MODEL_ID",
    "FULL_CONTEXT_WINDOW_DEFAULT",
    "IN_PROCESS_KINDS",
    "JSON_MODE_RETRIES",
    "LANGUAGE_PATTERN",
    "LLM_CALL_KIND",
    "LLM_SPILL_KIND",
    "LLM_THROTTLED_KIND",
    "MAX_CLIP_BYTES",
    "MAX_EMBED_TEXTS",
    "MAX_PCM_BYTES",
    "MAX_RAW_PREVIEW_CHARS",
    "MAX_TOOL_ROUNDS_DEFAULT",
    "MAX_TRANSCRIPT_CHARS",
    "NATIVE_SCHEMA_RETRIES",
    "NONE_CONTEXT_WINDOW_DEFAULT",
    "PCM_SAMPLE_WIDTH_BYTES",
    "PENDING_KINDS",
    "PROMPTED_JSON_RETRIES",
    "SPILL_WAIT_FRACTION",
    "TEXT_STREAM_CHUNK_COUNT",
    "TOOL_NAME_PATTERN",
    "AudioChunk",
    "AudioClip",
    "AudioMediaType",
    "AudioPart",
    "BoundEmbedder",
    "BoundModel",
    "BoundTranscriber",
    "CallGate",
    "CompositeLlmEventRecorder",
    "ContentPart",
    "ContextTooLongError",
    "DirectCallGate",
    "DirectEmbedGate",
    "DirectTranscriptionGate",
    "Ears",
    "EmbedGate",
    "EmbeddingCapabilities",
    "EmbeddingProvider",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "EmbeddingUnsupportedError",
    "FakeEmbedding",
    "FakeLLMProvider",
    "FakeTranscription",
    "FakeTranscriptionCall",
    "FallbackNote",
    "FallbackReason",
    "Fanner",
    "FannerDeps",
    "FannerLane",
    "FannerTranscriptionGate",
    "HealthState",
    "ImagePart",
    "JsonObject",
    "LLMChunk",
    "LLMError",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LadderObserver",
    "LadderOptions",
    "LlmEventRecorder",
    "MalformedOutputError",
    "MediaPart",
    "Message",
    "MissingDefaultModelError",
    "NullLadderObserver",
    "NullLlmEventRecorder",
    "OfflineViolationError",
    "PromptName",
    "ProviderCapabilities",
    "ProviderConfig",
    "ProviderFactory",
    "ProviderHealth",
    "ProviderKind",
    "ProviderLookup",
    "ProviderRegistry",
    "ProviderRequestError",
    "ProviderUnavailableError",
    "RateLimit",
    "RateLimitSnapshot",
    "RateLimitedError",
    "RefusedError",
    "RegistryDeps",
    "Responder",
    "Role",
    "Rung",
    "SectionLabel",
    "SpillReason",
    "StopReason",
    "StructuredResult",
    "TextPart",
    "ToolCall",
    "ToolCallPart",
    "ToolDefinition",
    "ToolExecutor",
    "ToolLoopOptions",
    "ToolLoopResult",
    "ToolResultPart",
    "TrailLadderObserver",
    "TrailLlmEventRecorder",
    "TranscriberLookup",
    "Transcript",
    "TranscriptSegment",
    "TranscriptionCapabilities",
    "TranscriptionGate",
    "TranscriptionProvider",
    "TranscriptionUnsupportedError",
    "UnknownProviderError",
    "UnresolvableSlotError",
    "Usage",
    "WavHeader",
    "apply_overrides",
    "check_request",
    "collect_clip",
    "complete_structured",
    "default_factories",
    "encode_wav",
    "load_prompt",
    "read_wav_header",
    "render",
    "resolve",
    "resolve_key",
    "resolve_transcriber",
    "run_tool_loop",
    "stream_by_buffering",
    "text_response",
    "tool_call_response",
    "validate_arguments",
    "walk_chain",
]
