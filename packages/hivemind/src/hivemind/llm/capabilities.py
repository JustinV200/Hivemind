"""Define ProviderCapabilities and ProviderHealth: what a provider can do, and whether it can act.

Codingrules section 8.6 fixes the rule this module exists to make mechanical: "capabilities are
declared, not assumed... core code branches on capabilities, never on provider name." Every
``LLMProvider`` (an adapter that lets the Hive talk to a model, hosted or local) exposes a frozen
``ProviderCapabilities``, and every subsystem above ``hivemind.llm`` reads it instead of asking
which vendor is behind the call. ``full()`` and ``none()`` are the two ends of the range the
degradation ladders (a later roadmap step) are proven against: the strongest provider the Hive
ships with today, and a plain-text model with no native affordances at all, so the weakest local
model still gets a working interface through prompted fallbacks.

``ProviderHealth`` is Appendix C's "Provider health" machine (``HEALTHY`` <-> ``DEGRADED`` <->
``DOWN``): kept in memory only, re-probed on start, with no forbidden edge to enforce (any state
may follow any other, since a probe can observe recovery or failure in either direction without
warning), so it is a plain value plus an enum rather than a table of edges like the Hive's other
state machines.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Read by every subsystem that chooses how to
    call a model: the ladders (``hivemind.llm.ladders``), routing, and Clustering (which pauses
    affected bees when a provider's health goes DOWN with no fallback, codingrules section 8.13).
    Calls into nothing beyond the standard library and pydantic.

Key invariants:
    - Every ProviderCapabilities is frozen and forbids unknown fields (codingrules section 8.5).
    - ``full().context_window`` defaults to 200,000 and ``none().context_window`` to 8,192; both
      accept an override, since a real provider's exact window still varies by model.
    - ``none()`` sets every boolean False: no native tool calls, no schema-enforced output, no
      JSON mode, no vision, no streaming (the provider's ``stream()`` still works, by yielding
      one chunk; see hivemind.llm.fake), no reasoning control, no system role, no parallel tool
      calls, no token counting.

See Also:
    - .claude/codingrules.md section 8.6 for "capabilities are declared, not assumed".
    - .claude/codingrules.md Appendix C, "Provider health", for the state machine ProviderHealth
      carries.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - hivemind.llm.provider for LLMProvider.capabilities and LLMProvider.health, the two members
      that return these types.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from waggle.messages.base import UtcDatetime

FULL_CONTEXT_WINDOW_DEFAULT = 200_000  # A generous hosted-model window; full()'s default.
NONE_CONTEXT_WINDOW_DEFAULT = 8_192  # A small plain-text local model's typical window; none()'s.

__all__ = [
    "FULL_CONTEXT_WINDOW_DEFAULT",
    "NONE_CONTEXT_WINDOW_DEFAULT",
    "HealthState",
    "ProviderCapabilities",
    "ProviderHealth",
]


class ProviderCapabilities(BaseModel):
    """What one provider can do, declared up front so core code never branches on its name.

    Read by the degradation ladders (hivemind.llm.ladders) to choose native, JSON-mode, or
    prompted behaviour, and by routing to reject a binding that cannot meet a request's needs
    (e.g. vision content on a text-only model).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    native_tool_calls: bool = Field(
        description="Whether the provider has its own tool-call protocol, not a prompted one."
    )
    schema_output: bool = Field(
        description="Whether the provider can enforce a JSON schema on its own output."
    )
    json_mode: bool = Field(
        description="Whether the provider can be told to emit valid JSON, without enforcing a "
        "specific schema."
    )
    vision: bool = Field(description="Whether the provider accepts ImagePart content.")
    streaming: bool = Field(
        description="Whether the provider streams real deltas; False means stream() still works "
        "but yields the whole response as a single chunk."
    )
    reasoning_control: bool = Field(
        description="Whether the provider exposes a knob over how much hidden reasoning it does."
    )
    context_window: int = Field(gt=0, description="The model's context window, in tokens.")
    system_role: bool = Field(
        description="Whether the provider has a dedicated system-prompt channel, rather than "
        "needing the system prompt folded into the first user turn."
    )
    parallel_tool_calls: bool = Field(
        description="Whether the provider can return more than one tool call in one turn."
    )
    token_counting: bool = Field(
        description="Whether the provider can estimate a request's token count before sending it."
    )

    @classmethod
    def full(cls, *, context_window: int = FULL_CONTEXT_WINDOW_DEFAULT) -> ProviderCapabilities:
        """Return every capability enabled: the strongest provider shape the Hive expects.

        Args:
            context_window: The model's context window, in tokens.

        Returns:
            A ProviderCapabilities with every boolean True.
        """
        return cls(
            native_tool_calls=True,
            schema_output=True,
            json_mode=True,
            vision=True,
            streaming=True,
            reasoning_control=True,
            context_window=context_window,
            system_role=True,
            parallel_tool_calls=True,
            token_counting=True,
        )

    @classmethod
    def none(cls, *, context_window: int = NONE_CONTEXT_WINDOW_DEFAULT) -> ProviderCapabilities:
        """Return every capability disabled: a plain-text model with no native affordances.

        Every ladder and the Drone must still work against this shape, through the prompted
        rungs (phase 3 brief section 7).

        Args:
            context_window: The model's context window, in tokens.

        Returns:
            A ProviderCapabilities with every boolean False.
        """
        return cls(
            native_tool_calls=False,
            schema_output=False,
            json_mode=False,
            vision=False,
            streaming=False,
            reasoning_control=False,
            context_window=context_window,
            system_role=False,
            parallel_tool_calls=False,
            token_counting=False,
        )


class HealthState(Enum):
    """A provider's own health, as Appendix C's "Provider health" machine: HEALTHY/DEGRADED/DOWN.

    In memory only, re-probed on start; every state may follow any other, since a probe can
    observe recovery or failure in either direction with no invalid edge to forbid.
    """

    HEALTHY = "HEALTHY"  # Answering normally.
    DEGRADED = "DEGRADED"  # Answering, but slow or partially failing; a fallback is worth trying.
    DOWN = "DOWN"  # Not answering at all; with no fallback, this triggers Clustering (8.13).


class ProviderHealth(BaseModel):
    """One point-in-time health reading for a provider, from ``LLMProvider.health``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: HealthState = Field(description="The provider's health at the time of this reading.")
    detail: str = Field(description="A short, human-readable reason for this state.")
    checked_at: UtcDatetime = Field(
        description="When this reading was taken, from an injected Clock."
    )
