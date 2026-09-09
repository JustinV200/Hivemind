"""Define the LLM boundary: HiveMind's own request, message and response shapes.

Every model call in the Hive crosses this boundary in both directions: an ``LLMRequest`` goes out
to a provider (an LLM the way a Hive bee reaches a model, whether hosted or run locally) and an
``LLMResponse`` (or a stream of ``LLMChunk``) comes back. None of these types are a vendor SDK
type; codingrules section 8.6 ("our types at the boundary") requires that a provider adapter
translate to and from these shapes in its own ``mapping.py``, so nothing above ``hivemind.llm``
ever imports a vendor SDK. ``Message`` carries a tuple of ``ContentPart`` (text, an image, a tool
call, or a tool result), tagged by a pydantic discriminated union on ``kind`` so a JSON payload
round-trips through the exact subtype it was built from. ``JsonObject`` is the one alias for "a
field that holds arbitrary JSON" (codingrules phase-3 brief section 0): a tool's JSON-schema
parameters, a tool call's arguments, and a requested response schema are all ``JsonObject``, never
``dict[str, Any]`` (codingrules section 9).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Read and built by every layer above that is
    allowed to think with a model: workers, wardens, queen, and the ladders in
    ``hivemind.llm.ladders`` (a later roadmap step). Calls into ``hivemind.forage`` for
    ``ModelSlot`` and ``Effort`` only (codingrules section 4: llm imports forage, never the
    reverse).

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5): a state
      change produces a new value, never a mutation.
    - ``ContentPart`` is a discriminated union on ``kind``; a JSON payload with an unrecognised
      ``kind`` is rejected rather than silently coerced into the wrong part type.
    - ``Usage.__add__`` is cost-aware: the sum's ``cost_usd`` is ``None`` whenever either addend's
      is ``None``, since an unpriced call can never be folded into a priced total.
    - ``LLMRequest.messages`` is one outgoing request's turns, never a growing transcript
      (codingrules section 8.8); ``scripts/check_no_transcripts.py`` allowlists this file by name
      for exactly that reason.

See Also:
    - .claude/codingrules.md section 8.6 for "our types at the boundary" and the ladder rules
      built on these shapes.
    - .claude/codingrules.md section 8.8 for why a request's messages are not a transcript.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this module is
      part of.
    - hivemind.llm.capabilities for ProviderCapabilities, the companion boundary to this module.
    - hivemind.llm.provider for LLMProvider, the protocol these models cross.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hivemind.forage.slots import Effort, ModelSlot

# A JSON schema, a tool's arguments, or a requested response schema: arbitrary JSON, never
# `dict[str, Any]` (codingrules section 9). Defined once here and re-exported from
# `hivemind.llm` so every other package that needs the same shape imports it from one place.
JsonObject = dict[str, JsonValue]

TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"  # A tool name a prompted protocol can parse back out.

__all__ = [
    "TOOL_NAME_PATTERN",
    "ContentPart",
    "ImagePart",
    "JsonObject",
    "LLMChunk",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "Role",
    "StopReason",
    "TextPart",
    "ToolCall",
    "ToolCallPart",
    "ToolDefinition",
    "ToolResultPart",
    "Usage",
]


class Role(Enum):
    """Who spoke a Message: the human/tool-loop side (USER) or the model (ASSISTANT)."""

    USER = "user"  # A prompt turn, or a tool result fed back to the model.
    ASSISTANT = "assistant"  # A prior model turn, replayed for multi-turn tool loops.


class StopReason(Enum):
    """Why a model call stopped generating; drives ladder and tool-loop control flow."""

    END_TURN = "end_turn"  # The model finished its turn normally.
    MAX_TOKENS = "max_tokens"  # Hit LLMRequest.max_output_tokens before finishing.
    TOOL_USE = "tool_use"  # The model wants a tool call executed before continuing.
    STOP_SEQUENCE = "stop_sequence"  # One of LLMRequest.stop_sequences matched.
    REFUSAL = "refusal"  # The model declined to answer; see RefusedError for the raised form.


class ToolCall(BaseModel):
    """One tool invocation a model asked for: an id, the tool's name, and its arguments.

    Carried on a ``ToolCallPart`` inside an ``LLMResponse``, and echoed back inside a
    ``ToolResultPart`` so a multi-turn tool loop can match a result to the call that produced it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        description="The call's id: provider-issued for a native call, or a synthesized "
        "'call_<n>' for a prompted tool protocol (hivemind.llm.ladders.extraction)."
    )
    name: str = Field(description="The name of the tool this call invokes.")
    arguments: JsonObject = Field(description="The tool's arguments, already parsed from JSON.")


class ToolDefinition(BaseModel):
    """One tool a model may call: its name, a description, and a JSON-schema parameter shape.

    Passed on ``LLMRequest.tools``; a provider adapter maps this to its own native tool-call
    format, or a prompted ladder renders it into the tool-call preamble (hivemind.llm.ladders).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(
        pattern=TOOL_NAME_PATTERN,
        description="The tool's name; lowercase, starts with a letter, ASCII word characters "
        "only, so a prompted protocol can parse it back out of a fenced block unambiguously.",
    )
    description: str = Field(description="What the tool does and when to call it.")
    parameters: JsonObject = Field(description="A JSON schema for the tool's arguments.")


class TextPart(BaseModel):
    """A plain-text content part of a Message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["text"] = Field(default="text", description="Discriminator for ContentPart.")
    text: str = Field(description="The text itself.")


class ImagePart(BaseModel):
    """An inline image content part of a Message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["image"] = Field(default="image", description="Discriminator for ContentPart.")
    media_type: str = Field(description="The image's MIME type, e.g. 'image/png'.")
    data_base64: str = Field(description="The image's bytes, base64-encoded.")


class ToolCallPart(BaseModel):
    """A model-issued tool call, as a content part of an assistant Message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["tool_call"] = Field(
        default="tool_call", description="Discriminator for ContentPart."
    )
    call: ToolCall = Field(description="The call the model asked for.")


class ToolResultPart(BaseModel):
    """A tool's result, fed back to the model as a content part of a user Message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["tool_result"] = Field(
        default="tool_result", description="Discriminator for ContentPart."
    )
    call_id: str = Field(description="The ToolCall.id this result answers.")
    content: str = Field(description="The tool's output, already rendered to text.")
    is_error: bool = Field(
        default=False, description="Whether the tool call failed; content then holds the error."
    )


# A discriminated union: pydantic dispatches on `kind` alone, so a stored or replayed Message
# always rebuilds the exact part subtype it was built from, never a generic fallback.
ContentPart = Annotated[
    TextPart | ImagePart | ToolCallPart | ToolResultPart, Field(discriminator="kind")
]


class Message(BaseModel):
    """One turn of a conversation: who spoke, and the content parts they sent.

    A single ``LLMRequest.messages`` entry, not a growing transcript (codingrules section 8.8):
    the caller assembles the full list fresh for each request from durable state
    (``hivemind.memory.assemble``), and nothing here accumulates across calls.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role = Field(description="Who spoke this turn.")
    parts: tuple[ContentPart, ...] = Field(description="This turn's content, in order.")

    @classmethod
    def text(cls, role: Role, text: str) -> Message:
        """Build a single-part text Message; the common case for a plain prompt turn.

        Args:
            role: Who spoke this turn.
            text: The turn's text.

        Returns:
            A Message with one TextPart.
        """
        return cls(role=role, parts=(TextPart(text=text),))


class Usage(BaseModel):
    """Normalised token and cost accounting for one model call (codingrules section 8.6).

    Every ``LLMResponse`` carries one; the Pheromone Trail and the cost view read only this
    shape, so a provider change never breaks accounting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(ge=0, description="Tokens the request consumed, as billed.")
    output_tokens: int = Field(ge=0, description="Tokens the response generated, as billed.")
    cached_tokens: int = Field(
        default=0, ge=0, description="Of input_tokens, how many were served from a prompt cache."
    )
    cost_usd: Annotated[float, Field(ge=0)] | None = Field(
        default=None,
        description="What this call cost in US dollars, or None when the manifest lists no "
        "price for the model that served it.",
    )

    def __add__(self, other: Usage) -> Usage:
        """Sum two Usages: token counts add; cost is None if either addend's cost is None.

        Args:
            other: The Usage to add to this one, typically the next call in the same tool loop
                or ladder attempt.

        Returns:
            A new Usage with summed token counts. ``cost_usd`` is the sum of both costs when
            both are known, otherwise None: an unpriced call can never be folded into a priced
            running total without silently understating it.
        """
        if not isinstance(other, Usage):
            return NotImplemented
        cost = (
            None
            if self.cost_usd is None or other.cost_usd is None
            else self.cost_usd + other.cost_usd
        )
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            cost_usd=cost,
        )


class LLMRequest(BaseModel):
    """A single, self-contained request to a model behind a ModelSlot.

    Assembled fresh for each call (codingrules section 8.8: an awake episode is stateless); no
    field here accumulates across calls.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: ModelSlot = Field(description="The named place this call resolves to (hivemind.forage).")
    model: str | None = Field(
        default=None,
        description="The provider's own model id for this call, stamped by the call gate from the "
        "BoundModel; None lets the provider use its configured default.",
    )
    system: str | None = Field(
        default=None, description="The system prompt, or None when the call needs none."
    )
    messages: tuple[Message, ...] = Field(description="This call's turns, oldest first.")
    tools: tuple[ToolDefinition, ...] = Field(
        default=(), description="Tools the model may call; empty means no tools are offered."
    )
    response_schema: JsonObject | None = Field(
        default=None,
        description="A JSON schema the response must satisfy, or None for free-form text.",
    )
    max_output_tokens: int = Field(gt=0, description="The output token budget for this call.")
    effort: Effort = Field(
        default=Effort.MEDIUM, description="How hard the model should think on this call."
    )
    stop_sequences: tuple[str, ...] = Field(
        default=(), description="Strings that end generation early when produced."
    )
    temperature: float | None = Field(
        default=None, description="Sampling temperature, or None for the provider's default."
    )


class LLMResponse(BaseModel):
    """A completed model call: its content parts, why it stopped, and its normalised usage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parts: tuple[ContentPart, ...] = Field(description="The response's content, in order.")
    stop_reason: StopReason = Field(description="Why generation stopped.")
    usage: Usage = Field(description="Normalised token and cost accounting for this call.")
    model: str = Field(description="The provider's own model id that served this call.")
    reasoning_summary: str | None = Field(
        default=None, description="A provider-supplied summary of hidden reasoning, if any."
    )

    @property
    def text(self) -> str:
        """Return every TextPart's text, concatenated in order.

        Returns:
            The response's plain-text content; empty when it holds no TextPart.
        """
        return "".join(part.text for part in self.parts if isinstance(part, TextPart))

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        """Return every tool call the response asked for, in order.

        Returns:
            One ToolCall per ToolCallPart in ``parts``; empty when the model called no tool.
        """
        return tuple(part.call for part in self.parts if isinstance(part, ToolCallPart))


class LLMChunk(BaseModel):
    """One streaming delta from ``LLMProvider.stream``.

    Every field is optional because one chunk carries at most one kind of news: a slice of text,
    one tool call, or the final usage and stop reason. ``stop_reason`` is set only on the last
    chunk of a stream.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str | None = Field(default=None, description="A slice of response text, if any.")
    tool_call: ToolCall | None = Field(
        default=None, description="One complete tool call, if this chunk carries one."
    )
    usage: Usage | None = Field(
        default=None, description="Usage so far, or the final usage on the last chunk."
    )
    stop_reason: StopReason | None = Field(
        default=None, description="Why the stream ended; set on the final chunk only."
    )
