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
    - Message shapes (`hivemind.llm.models`): Role, TextPart, ImagePart, ToolCallPart,
      ToolResultPart, ContentPart, Message, JsonObject, ToolDefinition, ToolCall, StopReason,
      Usage, LLMRequest, LLMResponse, LLMChunk, RateLimitSnapshot, TOOL_NAME_PATTERN.
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

Public API (roadmap step 6.5a, the subset step 10.5f needs):
    - Transcription (`hivemind.llm.transcription`): AudioClip, AudioChunk, AudioMediaType,
      Transcript, TranscriptSegment, TranscriptionProvider, TranscriptionCapabilities,
      FakeTranscription, TranscriptionCall, BoundTranscriber, TranscriberLookup,
      resolve_transcriber, clip_from_chunks, normalise_language, wav_duration_s,
      InvalidAudioClipError, ClipProblem, TranscriptionUnsupportedError, MAX_CLIP_BYTES,
      MAX_CLIP_SECONDS, MAX_TRANSCRIPT_CHARS, MAX_TRANSCRIPT_SEGMENTS, MAX_LANGUAGE_CHARS,
      FAKE_DETECTED_LANGUAGE, UNSUPPORTED_MEDIA_TYPE_STATUS, SECONDS_PER_MINUTE.
    - Metering it (`hivemind.llm.fanner`): MeteredTranscriber, bind_transcriber,
      AUDIO_SECONDS_KEY.
    - Binding it (`hivemind.llm.registry`): ProviderRegistry.transcriber, TranscriberFactory,
      default_transcriber_factories.
    - Closing every provider at shutdown (`hivemind.llm.registry`): ProviderRegistry.aclose,
      PROVIDER_CLOSE_TIMEOUT_S; every LLMProvider and TranscriptionProvider has `aclose()`.
"""

from hivemind.llm.capabilities import (
    FULL_CONTEXT_WINDOW_DEFAULT,
    NONE_CONTEXT_WINDOW_DEFAULT,
    HealthState,
    ProviderCapabilities,
    ProviderHealth,
)
from hivemind.llm.errors import (
    MAX_RAW_PREVIEW_CHARS,
    ContextTooLongError,
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
    LlmEventRecorder,
    MeteredTranscriber,
    NullLlmEventRecorder,
    RateLimit,
    SpillReason,
    TrailLlmEventRecorder,
    bind_transcriber,
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
    ContentPart,
    ImagePart,
    JsonObject,
    LLMChunk,
    LLMRequest,
    LLMResponse,
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
    PENDING_KINDS,
    PROVIDER_CLOSE_TIMEOUT_S,
    MissingDefaultModelError,
    ProviderConfig,
    ProviderFactory,
    ProviderKind,
    ProviderRegistry,
    RegistryDeps,
    TranscriberFactory,
    apply_overrides,
    default_factories,
    default_transcriber_factories,
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
    FAKE_DETECTED_LANGUAGE,
    MAX_CLIP_BYTES,
    MAX_CLIP_SECONDS,
    MAX_LANGUAGE_CHARS,
    MAX_TRANSCRIPT_CHARS,
    MAX_TRANSCRIPT_SEGMENTS,
    SECONDS_PER_MINUTE,
    UNSUPPORTED_MEDIA_TYPE_STATUS,
    AudioChunk,
    AudioClip,
    AudioMediaType,
    BoundTranscriber,
    ClipProblem,
    FakeTranscription,
    InvalidAudioClipError,
    TranscriberLookup,
    Transcript,
    TranscriptionCall,
    TranscriptionCapabilities,
    TranscriptionProvider,
    TranscriptionUnsupportedError,
    TranscriptSegment,
    clip_from_chunks,
    normalise_language,
    resolve_transcriber,
    wav_duration_s,
)

__all__ = [
    "AUDIO_SECONDS_KEY",
    "CHARS_PER_TOKEN_ESTIMATE",
    "DEFAULT_SEATS",
    "DEFAULT_THROTTLE_S",
    "FAKE_DETECTED_LANGUAGE",
    "FAKE_MODEL_ID",
    "FULL_CONTEXT_WINDOW_DEFAULT",
    "JSON_MODE_RETRIES",
    "LLM_CALL_KIND",
    "LLM_SPILL_KIND",
    "LLM_THROTTLED_KIND",
    "MAX_CLIP_BYTES",
    "MAX_CLIP_SECONDS",
    "MAX_LANGUAGE_CHARS",
    "MAX_RAW_PREVIEW_CHARS",
    "MAX_TOOL_ROUNDS_DEFAULT",
    "MAX_TRANSCRIPT_CHARS",
    "MAX_TRANSCRIPT_SEGMENTS",
    "NATIVE_SCHEMA_RETRIES",
    "NONE_CONTEXT_WINDOW_DEFAULT",
    "PENDING_KINDS",
    "PROMPTED_JSON_RETRIES",
    "PROVIDER_CLOSE_TIMEOUT_S",
    "SECONDS_PER_MINUTE",
    "SPILL_WAIT_FRACTION",
    "TEXT_STREAM_CHUNK_COUNT",
    "TOOL_NAME_PATTERN",
    "UNSUPPORTED_MEDIA_TYPE_STATUS",
    "AudioChunk",
    "AudioClip",
    "AudioMediaType",
    "BoundModel",
    "BoundTranscriber",
    "CallGate",
    "ClipProblem",
    "CompositeLlmEventRecorder",
    "ContentPart",
    "ContextTooLongError",
    "DirectCallGate",
    "FakeLLMProvider",
    "FakeTranscription",
    "FallbackNote",
    "FallbackReason",
    "Fanner",
    "FannerDeps",
    "FannerLane",
    "HealthState",
    "ImagePart",
    "InvalidAudioClipError",
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
    "Message",
    "MeteredTranscriber",
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
    "TranscriberFactory",
    "TranscriberLookup",
    "Transcript",
    "TranscriptSegment",
    "TranscriptionCall",
    "TranscriptionCapabilities",
    "TranscriptionProvider",
    "TranscriptionUnsupportedError",
    "UnknownProviderError",
    "UnresolvableSlotError",
    "Usage",
    "apply_overrides",
    "bind_transcriber",
    "clip_from_chunks",
    "complete_structured",
    "default_factories",
    "default_transcriber_factories",
    "load_prompt",
    "normalise_language",
    "render",
    "resolve",
    "resolve_key",
    "resolve_transcriber",
    "run_tool_loop",
    "text_response",
    "tool_call_response",
    "validate_arguments",
    "walk_chain",
    "wav_duration_s",
]
