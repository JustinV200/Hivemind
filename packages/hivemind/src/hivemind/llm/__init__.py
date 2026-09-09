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
    - `resolve(slot, manifest) -> BoundModel` does not exist yet; it is roadmap step 3.4
      (`hivemind.llm.slots` gains it in a later dispatch). This package only defines the shape it
      will return.

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
      Usage, LLMRequest, LLMResponse, LLMChunk, TOOL_NAME_PATTERN.
    - Capabilities and health (`hivemind.llm.capabilities`): ProviderCapabilities, HealthState,
      ProviderHealth, FULL_CONTEXT_WINDOW_DEFAULT, NONE_CONTEXT_WINDOW_DEFAULT.
    - The one door (`hivemind.llm.provider`): LLMProvider.
    - Errors (`hivemind.llm.errors`): LLMError, RateLimitedError, ProviderUnavailableError,
      ContextTooLongError, RefusedError, MalformedOutputError, UnknownProviderError,
      OfflineViolationError, ProviderRequestError, MAX_RAW_PREVIEW_CHARS.
    - The fake (`hivemind.llm.fake`): FakeLLMProvider, Responder, text_response,
      tool_call_response, FAKE_MODEL_ID, TEXT_STREAM_CHUNK_COUNT, CHARS_PER_TOKEN_ESTIMATE.
    - Slots (`hivemind.llm.slots`): BoundModel.
    - Prompts (`hivemind.llm.prompts`): PromptName, SectionLabel, load_prompt, render.

Public API (roadmap step 3.5):
    - Structured output (`hivemind.llm.ladders.structured`): complete_structured,
      StructuredResult, Rung, NATIVE_SCHEMA_RETRIES, JSON_MODE_RETRIES, PROMPTED_JSON_RETRIES.
    - Tool calls (`hivemind.llm.ladders.tools`): run_tool_loop, ToolExecutor, ToolLoopOptions,
      ToolLoopResult, MAX_TOOL_ROUNDS_DEFAULT.
    - Argument validation (`hivemind.llm.ladders.extraction`): validate_arguments.
    - The call seam (`hivemind.llm.ladders.gate`): CallGate, DirectCallGate.
    - Reporting a step-down (`hivemind.llm.ladders.observer`): LadderObserver, FallbackNote,
      FallbackReason, NullLadderObserver, TrailLadderObserver.
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
from hivemind.llm.slots import BoundModel

__all__ = [
    "CHARS_PER_TOKEN_ESTIMATE",
    "FAKE_MODEL_ID",
    "FULL_CONTEXT_WINDOW_DEFAULT",
    "JSON_MODE_RETRIES",
    "MAX_RAW_PREVIEW_CHARS",
    "MAX_TOOL_ROUNDS_DEFAULT",
    "NATIVE_SCHEMA_RETRIES",
    "NONE_CONTEXT_WINDOW_DEFAULT",
    "PROMPTED_JSON_RETRIES",
    "TEXT_STREAM_CHUNK_COUNT",
    "TOOL_NAME_PATTERN",
    "BoundModel",
    "CallGate",
    "ContentPart",
    "ContextTooLongError",
    "DirectCallGate",
    "FakeLLMProvider",
    "FallbackNote",
    "FallbackReason",
    "HealthState",
    "ImagePart",
    "JsonObject",
    "LLMChunk",
    "LLMError",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LadderObserver",
    "MalformedOutputError",
    "Message",
    "NullLadderObserver",
    "OfflineViolationError",
    "PromptName",
    "ProviderCapabilities",
    "ProviderHealth",
    "ProviderRequestError",
    "ProviderUnavailableError",
    "RateLimitedError",
    "RefusedError",
    "Responder",
    "Role",
    "Rung",
    "SectionLabel",
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
    "UnknownProviderError",
    "Usage",
    "complete_structured",
    "load_prompt",
    "render",
    "run_tool_loop",
    "text_response",
    "tool_call_response",
    "validate_arguments",
]
