"""Contract suite for LLMProvider: one clause per line, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    `hivemind.llm.provider.LLMProvider` contract (codingrules 8.6: "same contract for every
    provider") and runs against every implementation registered in `_HARNESSES`:
    `hivemind.llm.fake.FakeLLMProvider`, `hivemind.llm.providers.openai_compat.
    OpenAICompatProvider` and `hivemind.llm.providers.anthropic.AnthropicProvider`, each built by
    `contracts.llm_provider_harness`'s harness for it. A new provider passes this suite before it
    is registered (codingrules 14.3). The `live_llm` tests at the bottom hit a real endpoint only
    when explicitly opted into; they never run in the default gate.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/codingrules.md section 8.6 for "same contract for every provider" and the
      zero-capability floor every ladder (and this suite) must survive.
    - .claude/codingrules.md section 14.3 for the contract-suite rule this module follows.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md and
      docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md for the decisions this
      suite proves every provider honours.
    - contracts.llm_provider_harness for ProviderHarness and every concrete harness.
    - packages/hivemind/tests/contracts/test_cell_session_contract.py for the parametrised-harness
      pattern this suite follows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest
from builders.llm import make_bound, make_request, make_tool
from contracts.llm_provider_harness import (
    DEFAULT_MODEL,
    AnthropicHarness,
    ErrorKind,
    FakeHarness,
    OpenAICompatHarness,
    ProviderHarness,
)
from pydantic import BaseModel, SecretStr

from hivemind.llm import (
    ContextTooLongError,
    FallbackNote,
    HealthState,
    LLMError,
    LLMRequest,
    Message,
    ProviderCapabilities,
    ProviderUnavailableError,
    RateLimitedError,
    Role,
    Rung,
    StopReason,
    ToolCall,
    ToolResultPart,
    complete_structured,
    run_tool_loop,
)
from hivemind.llm.providers.anthropic import AnthropicConfig, AnthropicProvider
from hivemind.llm.providers.openai_compat import OpenAICompatConfig, OpenAICompatProvider
from waggle.clock import SystemClock

_SECRET = "supersecret-value"  # noqa: S105 -- a probe value, never a real credential (item 11).
# A capability combo no real adapter declares on purpose, but the ladder must still honour it: json
# mode without schema enforcement, the middle rung between NATIVE and PROMPTED.
_JSON_MODE_ONLY = ProviderCapabilities.full().model_copy(update={"schema_output": False})

_HARNESSES: dict[str, ProviderHarness] = {
    "fake": FakeHarness(),
    "openai_compat": OpenAICompatHarness(),
    "anthropic": AnthropicHarness(),
}


@pytest.fixture(params=sorted(_HARNESSES))
def harness(request: pytest.FixtureRequest) -> ProviderHarness:
    """One ProviderHarness per registered LLMProvider implementation."""
    return _HARNESSES[request.param]


def _request(**overrides: object) -> LLMRequest:
    """Build an LLMRequest with an explicit model: AnthropicProvider raises on `model=None`."""
    fields: dict[str, object] = {"model": DEFAULT_MODEL}
    fields.update(overrides)
    return make_request(**fields)


class _Answer(BaseModel):
    """A minimal structured-output schema for the `complete_structured` contract line."""

    answer: str


@dataclass
class _RecordingObserver:
    """A LadderObserver that keeps every FallbackNote it sees, to assert none fired."""

    notes: list[FallbackNote] = field(default_factory=list)

    async def on_fallback(self, note: FallbackNote) -> None:
        """Record `note`; see `LadderObserver.on_fallback`."""
        self.notes.append(note)


@dataclass
class _RecordingExecutor:
    """A ToolExecutor that always succeeds and counts how many calls it ran."""

    executed: int = 0

    async def execute(self, call: ToolCall) -> ToolResultPart:
        """Record the call and return a fixed success result."""
        self.executed += 1
        return ToolResultPart(call_id=call.id, content="ok", is_error=False)


def _expected_rung(capabilities: ProviderCapabilities) -> Rung:
    """Mirror `hivemind.llm.ladders.structured._initial_rung`'s own capability -> rung mapping."""
    if capabilities.schema_output:
        return Rung.NATIVE
    if capabilities.json_mode:
        return Rung.JSON_MODE
    return Rung.PROMPTED


# ──────────────────────────────────────────────────────────────────────────────
# 1. complete() returns normalised usage and a StopReason
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_returns_normalised_usage_and_stop_reason(harness: ProviderHarness) -> None:
    provider = harness.make_provider()
    harness.arrange_text("Hello there.")

    response = await provider.complete(_request())

    assert response.text == "Hello there."
    assert response.usage.input_tokens >= 0
    assert response.usage.output_tokens >= 0
    assert response.usage.cost_usd is None or isinstance(response.usage.cost_usd, float)
    assert isinstance(response.stop_reason, StopReason)


# ──────────────────────────────────────────────────────────────────────────────
# 2. complete() stamps request.model onto the wire request
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_stamps_request_model_onto_the_wire(harness: ProviderHarness) -> None:
    provider = harness.make_provider()
    harness.arrange_text("hi")

    await provider.complete(_request(model="probe-model"))

    assert harness.last_request_model() == "probe-model"


# ──────────────────────────────────────────────────────────────────────────────
# 3. A tool-call response round-trips
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_round_trips_a_tool_call(harness: ProviderHarness) -> None:
    provider = harness.make_provider()  # Default capabilities: native tool calls.
    harness.arrange_tool_call("test_tool", {"city": "Paris"})

    response = await provider.complete(_request(tools=(make_tool(name="test_tool"),)))

    assert response.stop_reason == StopReason.TOOL_USE
    assert response.tool_calls[0].name == "test_tool"
    assert response.tool_calls[0].arguments == {"city": "Paris"}


# ──────────────────────────────────────────────────────────────────────────────
# 4. Structured output at each rung the provider's capabilities allow
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "capabilities", [ProviderCapabilities.full(), _JSON_MODE_ONLY, ProviderCapabilities.none()]
)
async def test_structured_output_succeeds_at_every_capability_rung(
    harness: ProviderHarness, capabilities: ProviderCapabilities
) -> None:
    provider = harness.make_provider(capabilities)
    harness.arrange_structured({"answer": "ok"})
    observer = _RecordingObserver()
    bound = make_bound(provider=provider)

    result = await complete_structured(bound, make_request(), _Answer, observer=observer)

    assert result.value == _Answer(answer="ok")
    assert result.rung == _expected_rung(capabilities)
    assert observer.notes == []  # The first attempt succeeded: no step-down was ever reported.


# ──────────────────────────────────────────────────────────────────────────────
# 5. The tool loop at each rung, both ending in one final answer
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "capabilities",
    [ProviderCapabilities.full(), ProviderCapabilities.none()],
    ids=["native", "prompted"],
)
async def test_tool_loop_completes_at_every_rung(
    harness: ProviderHarness, capabilities: ProviderCapabilities
) -> None:
    provider = harness.make_provider(capabilities)
    harness.arrange_tool_call("test_tool", {"x": 1})
    harness.arrange_text("Final answer.")
    bound = make_bound(provider=provider)
    tool = make_tool(
        name="test_tool",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
            "additionalProperties": False,
        },
    )
    executor = _RecordingExecutor()

    result = await run_tool_loop(bound, make_request(), (tool,), executor)

    assert result.calls[0].name == "test_tool"
    assert result.calls[0].arguments == {"x": 1}
    assert result.final_text == "Final answer."
    assert result.is_exhausted is False
    assert executor.executed == 1


# ──────────────────────────────────────────────────────────────────────────────
# 6. stream() concatenates to the completion, one final chunk carries usage+stop
# ──────────────────────────────────────────────────────────────────────────────


async def test_stream_concatenates_with_one_final_usage_and_stop_chunk(
    harness: ProviderHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_stream(("Hel", "lo"))

    chunks = [chunk async for chunk in provider.stream(_request())]

    text = "".join(chunk.text for chunk in chunks if chunk.text is not None)
    assert text == "Hello"
    finals = [chunk for chunk in chunks if chunk.stop_reason is not None]
    assert len(finals) == 1
    assert finals[0].usage is not None


# ──────────────────────────────────────────────────────────────────────────────
# 7. Error mapping: typed errors raised, a refusal returned rather than raised
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("rate_limited", RateLimitedError),
        ("unavailable", ProviderUnavailableError),
        ("context_too_long", ContextTooLongError),
    ],
)
async def test_errors_map_to_typed_exceptions(
    harness: ProviderHarness, kind: ErrorKind, expected: type[LLMError]
) -> None:
    provider = harness.make_provider()
    harness.arrange_error(kind)

    with pytest.raises(expected):
        await provider.complete(_request())


async def test_rate_limited_carries_retry_after_when_the_fixture_sets_it(
    harness: ProviderHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_error("rate_limited")

    with pytest.raises(RateLimitedError) as excinfo:
        await provider.complete(_request())

    assert excinfo.value.retry_after_s == pytest.approx(2.0)


async def test_a_refusal_is_returned_with_stop_reason_never_raised(
    harness: ProviderHarness,
) -> None:
    provider = harness.make_provider()
    harness.arrange_error("refused")

    response = await provider.complete(_request())  # Must not raise.

    assert response.stop_reason == StopReason.REFUSAL


# ──────────────────────────────────────────────────────────────────────────────
# 8. health() reports HEALTHY/DEGRADED/DOWN
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("state", [HealthState.HEALTHY, HealthState.DEGRADED, HealthState.DOWN])
async def test_health_reports_the_arranged_state(
    harness: ProviderHarness, state: HealthState
) -> None:
    if state not in harness.supported_health_states():
        # The fake has no HTTP layer to answer slow or partially: honest, not a gap to fix.
        pytest.skip(f"{type(harness).__name__} cannot honestly simulate {state.value}.")
    provider = harness.make_provider()
    harness.arrange_health(state)

    health = await provider.health()

    assert health.state == state


# ──────────────────────────────────────────────────────────────────────────────
# 9. count_tokens() returns a positive int for a non-empty request
# ──────────────────────────────────────────────────────────────────────────────


async def test_count_tokens_returns_a_positive_int(harness: ProviderHarness) -> None:
    provider = harness.make_provider()

    count = await provider.count_tokens(
        _request(messages=(Message.text(Role.USER, "hello world"),))
    )

    assert count is not None
    assert count > 0


# ──────────────────────────────────────────────────────────────────────────────
# 10. Capability honesty: none() still completes text (codingrules 8.6)
# ──────────────────────────────────────────────────────────────────────────────


async def test_zero_capabilities_still_completes_text(harness: ProviderHarness) -> None:
    """The Drone must survive a ProviderCapabilities.none() binding (codingrules 8.6)."""
    provider = harness.make_provider(ProviderCapabilities.none())
    assert isinstance(provider.capabilities, ProviderCapabilities)
    harness.arrange_text("Still works.")

    response = await provider.complete(_request())

    assert response.text == "Still works."


# ──────────────────────────────────────────────────────────────────────────────
# 11. No secret leaks in a raised error's message
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["rate_limited", "unavailable", "context_too_long"])
async def test_no_secret_leak_in_error_messages(harness: ProviderHarness, kind: ErrorKind) -> None:
    harness.configure_api_key(_SECRET)
    provider = harness.make_provider()
    harness.arrange_error(kind)

    with pytest.raises(LLMError) as excinfo:
        await provider.complete(_request())

    assert _SECRET not in str(excinfo.value)


# ──────────────────────────────────────────────────────────────────────────────
# live_llm variant: real endpoints, opt-in only
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.live_llm
async def test_live_anthropic_completes_and_reports_health() -> None:
    """Hit the real Anthropic API; skips cleanly unless HIVEMIND_LIVE_LLM=1 and a key are set."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    api_key = os.environ.get("HIVEMIND_ANTHROPIC_API_KEY")
    model = os.environ.get("HIVEMIND_LIVE_LLM_MODEL")
    if not api_key or not model:
        pytest.skip("HIVEMIND_ANTHROPIC_API_KEY and HIVEMIND_LIVE_LLM_MODEL must both be set.")
    config = AnthropicConfig(api_key=SecretStr(api_key))
    provider = AnthropicProvider.from_config("anthropic", config, SystemClock())

    response = await provider.complete(
        make_request(model=model, messages=(Message.text(Role.USER, "Say hi in one word."),))
    )
    health = await provider.health()

    assert response.text
    assert health.state in (HealthState.HEALTHY, HealthState.DEGRADED)


@pytest.mark.live_llm
async def test_live_openai_compat_completes_and_reports_health() -> None:
    """Hit a real local server; skips cleanly unless HIVEMIND_LIVE_LLM=1 and a base URL are set."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    base_url = os.environ.get("HIVEMIND_LOCAL_LLM_BASE_URL")
    model = os.environ.get("HIVEMIND_LIVE_LLM_MODEL")
    if not base_url or not model:
        pytest.skip("HIVEMIND_LOCAL_LLM_BASE_URL and HIVEMIND_LIVE_LLM_MODEL must both be set.")
    config = OpenAICompatConfig(
        base_url=base_url, model=model, timeout_s=30.0, capabilities=ProviderCapabilities.full()
    )
    provider = OpenAICompatProvider.create("openai_compat", config, SystemClock())

    response = await provider.complete(
        make_request(model=model, messages=(Message.text(Role.USER, "Say hi in one word."),))
    )
    health = await provider.health()

    assert response.text
    assert health.state in (HealthState.HEALTHY, HealthState.DEGRADED)
