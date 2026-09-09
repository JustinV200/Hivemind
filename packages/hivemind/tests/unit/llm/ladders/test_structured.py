"""Tests for hivemind.llm.ladders.structured: complete_structured and its rung ladder.

Fits into the Hive:
    Mirrors src/hivemind/llm/ladders/structured.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.ladders.structured for the module under test.
"""

from __future__ import annotations

from builders.llm import make_bound, make_request, text_response
from pydantic import BaseModel, ConfigDict

from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import (
    ContextTooLongError,
    MalformedOutputError,
    ProviderUnavailableError,
    RateLimitedError,
    RefusedError,
)
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.ladders.observer import FallbackReason, TrailLadderObserver
from hivemind.llm.ladders.structured import (
    JSON_MODE_RETRIES,
    NATIVE_SCHEMA_RETRIES,
    PROMPTED_JSON_RETRIES,
    Rung,
    complete_structured,
)
from hivemind.llm.models import StopReason, Usage
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


class _Decision(BaseModel):
    """A tiny structured schema for complete_structured's tests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    choice: str


class _RecordingObserver:
    """A LadderObserver that records every note it sees, for assertions."""

    def __init__(self) -> None:
        self.notes: list[object] = []

    async def on_fallback(self, note: object) -> None:
        self.notes.append(note)


def _native() -> FakeLLMProvider:
    return FakeLLMProvider(capabilities=ProviderCapabilities.full())


def _json_mode_only() -> FakeLLMProvider:
    return FakeLLMProvider(
        capabilities=ProviderCapabilities.full().model_copy(update={"schema_output": False})
    )


def _none() -> FakeLLMProvider:
    return FakeLLMProvider(capabilities=ProviderCapabilities.none())


# ──────────────────────────────────────────────────────────────────────────────
# Rung selection, one per capability level
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_structured_uses_native_rung_when_schema_output_is_supported() -> None:
    provider = _native()
    provider.script(text_response('{"choice": "a"}'))
    bound = make_bound(provider=provider)

    result = await complete_structured(bound, make_request(), _Decision)

    assert result.rung is Rung.NATIVE
    assert result.value == _Decision(choice="a")
    assert result.attempts == 1


async def test_complete_structured_uses_json_mode_rung_when_schema_output_is_unsupported() -> None:
    provider = _json_mode_only()
    provider.script(text_response('{"choice": "a"}'))
    bound = make_bound(provider=provider)

    result = await complete_structured(bound, make_request(), _Decision)

    assert result.rung is Rung.JSON_MODE


async def test_complete_structured_uses_prompted_rung_with_none_capabilities() -> None:
    provider = _none()
    provider.script(text_response('```json\n{"choice": "a"}\n```'))
    bound = make_bound(provider=provider)

    result = await complete_structured(bound, make_request(), _Decision)

    assert result.rung is Rung.PROMPTED
    assert result.value.choice == "a"


# ──────────────────────────────────────────────────────────────────────────────
# Retry, then success
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_structured_retries_after_a_validation_failure_then_succeeds() -> None:
    provider = _native()
    provider.script(text_response("not json at all"), text_response('{"choice": "a"}'))
    bound = make_bound(provider=provider)

    result = await complete_structured(bound, make_request(), _Decision)

    assert result.attempts == 2
    assert result.rung is Rung.NATIVE
    # The retry request carries the validation error as a new user message.
    retry_request = provider.calls[1]
    assert any(
        "did not match" in part.text or "not valid JSON" in part.text
        for message in retry_request.messages
        for part in message.parts
        if hasattr(part, "text")
    )


async def test_complete_structured_retries_after_valid_json_that_fails_schema_validation() -> None:
    provider = _native()
    # {"choice": 123} is valid JSON but choice must be a str: this is a pydantic ValidationError,
    # not a json.JSONDecodeError, so it exercises the schema-mismatch retry path specifically.
    provider.script(text_response('{"choice": 123}'), text_response('{"choice": "a"}'))
    bound = make_bound(provider=provider)

    result = await complete_structured(bound, make_request(), _Decision)

    assert result.attempts == 2
    assert result.value.choice == "a"


async def test_complete_structured_sums_usage_across_retries() -> None:
    provider = _native()
    provider.script(
        text_response("bad", usage=Usage(input_tokens=10, output_tokens=1)),
        text_response('{"choice": "a"}', usage=Usage(input_tokens=20, output_tokens=2)),
    )
    bound = make_bound(provider=provider)

    result = await complete_structured(bound, make_request(), _Decision)

    assert result.usage == Usage(input_tokens=30, output_tokens=3)


# ──────────────────────────────────────────────────────────────────────────────
# Step-down
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_structured_steps_down_a_rung_after_exhausting_native_retries() -> None:
    provider = _native()
    # NATIVE gets NATIVE_SCHEMA_RETRIES + 1 attempts, all bad, before stepping down to JSON_MODE.
    for _ in range(NATIVE_SCHEMA_RETRIES + 1):
        provider.script(text_response("not json"))
    provider.script(text_response('{"choice": "a"}'))
    bound = make_bound(provider=provider)
    observer = _RecordingObserver()

    result = await complete_structured(bound, make_request(), _Decision, observer=observer)

    assert result.rung is Rung.JSON_MODE
    assert result.attempts == NATIVE_SCHEMA_RETRIES + 2
    assert len(observer.notes) == 1
    note = observer.notes[0]
    assert note.from_rung is Rung.NATIVE  # type: ignore[attr-defined]
    assert note.to_rung is Rung.JSON_MODE  # type: ignore[attr-defined]
    assert note.to_binding is None  # type: ignore[attr-defined]
    assert note.reason is FallbackReason.RUNG_EXHAUSTED  # type: ignore[attr-defined]


async def test_complete_structured_raises_malformed_output_after_prompted_is_exhausted() -> None:
    provider = _none()  # Starts on PROMPTED; no lower rung to step down to.
    total_attempts = PROMPTED_JSON_RETRIES + 1
    for _ in range(total_attempts):
        provider.script(text_response("never a fenced json block"))
    bound = make_bound(provider=provider)

    try:
        await complete_structured(bound, make_request(), _Decision)
        raised = False
    except MalformedOutputError as exc:
        raised = True
        assert exc.attempts == total_attempts
        assert exc.provider == provider.name
    assert raised


async def test_complete_structured_walks_every_rung_down_to_malformed_output() -> None:
    provider = _native()
    for _ in range(NATIVE_SCHEMA_RETRIES + 1):
        provider.script(text_response("bad"))
    for _ in range(JSON_MODE_RETRIES + 1):
        provider.script(text_response("still bad"))
    for _ in range(PROMPTED_JSON_RETRIES + 1):
        provider.script(text_response("still no fenced block"))
    bound = make_bound(provider=provider)

    try:
        await complete_structured(bound, make_request(), _Decision)
        raised = False
    except MalformedOutputError:
        raised = True
    assert raised
    expected_attempts = (
        (NATIVE_SCHEMA_RETRIES + 1) + (JSON_MODE_RETRIES + 1) + (PROMPTED_JSON_RETRIES + 1)
    )
    assert len(provider.calls) == expected_attempts


# ──────────────────────────────────────────────────────────────────────────────
# Fallback chain
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_structured_falls_back_to_the_next_binding_on_provider_unavailable() -> None:
    primary = _native()
    primary.script(ProviderUnavailableError("primary", "down for maintenance"))
    fallback_provider = _native()
    fallback_provider.script(text_response('{"choice": "a"}'))
    fallback_bound = make_bound(provider=fallback_provider, binding="local_worker")
    bound = make_bound(provider=primary, binding="worker", fallback=fallback_bound)
    observer = _RecordingObserver()

    result = await complete_structured(bound, make_request(), _Decision, observer=observer)

    assert result.value.choice == "a"
    assert len(observer.notes) == 1
    note = observer.notes[0]
    assert note.reason is FallbackReason.PROVIDER_UNAVAILABLE  # type: ignore[attr-defined]
    assert note.to_binding == "local_worker"  # type: ignore[attr-defined]
    assert note.from_rung is None  # type: ignore[attr-defined]


async def test_complete_structured_falls_back_to_the_next_binding_on_rate_limited() -> None:
    primary = _native()
    primary.script(RateLimitedError("primary", retry_after_s=1.0))
    fallback_provider = _native()
    fallback_provider.script(text_response('{"choice": "a"}'))
    fallback_bound = make_bound(provider=fallback_provider, binding="local_worker")
    bound = make_bound(provider=primary, binding="worker", fallback=fallback_bound)
    observer = _RecordingObserver()

    result = await complete_structured(bound, make_request(), _Decision, observer=observer)

    assert result.value.choice == "a"
    assert observer.notes[0].reason is FallbackReason.RATE_LIMITED  # type: ignore[attr-defined]


async def test_complete_structured_reraises_when_no_fallback_binding_exists() -> None:
    provider = _native()
    provider.script(ProviderUnavailableError("primary", "down"))
    bound = make_bound(provider=provider)

    try:
        await complete_structured(bound, make_request(), _Decision)
        raised = False
    except ProviderUnavailableError:
        raised = True
    assert raised


async def test_complete_structured_records_fallback_notes_via_the_trail_observer() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    observer = TrailLadderObserver(trail, new_hive_id(clock), new_node_id(clock), "human", clock)
    primary = _native()
    primary.script(ProviderUnavailableError("primary", "down"))
    fallback_provider = _native()
    fallback_provider.script(text_response('{"choice": "a"}'))
    fallback_bound = make_bound(provider=fallback_provider, binding="local_worker")
    bound = make_bound(provider=primary, binding="worker", fallback=fallback_bound)

    await complete_structured(bound, make_request(), _Decision, observer=observer)

    events = await trail.query(TrailQuery())
    assert len(events) == 1
    assert events[0].kind == "llm.fallback"


# ──────────────────────────────────────────────────────────────────────────────
# ContextTooLongError and REFUSAL
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_structured_propagates_context_too_long_unchanged() -> None:
    primary = _native()
    primary.script(ContextTooLongError("primary", window=8_192, requested=20_000))
    fallback_provider = _native()
    fallback_bound = make_bound(provider=fallback_provider, binding="local_worker")
    bound = make_bound(provider=primary, fallback=fallback_bound)

    try:
        await complete_structured(bound, make_request(), _Decision)
        raised = False
    except ContextTooLongError:
        raised = True
    assert raised
    assert fallback_provider.calls == []  # No fallback attempted: this error is the caller's job.


async def test_complete_structured_raises_refused_error_on_a_refusal_stop_reason() -> None:
    provider = _native()
    provider.script(text_response("I can't help with that.", stop=StopReason.REFUSAL))
    bound = make_bound(provider=provider)

    try:
        await complete_structured(bound, make_request(), _Decision)
        raised = False
    except RefusedError:
        raised = True
    assert raised


# ──────────────────────────────────────────────────────────────────────────────
# Default gate/observer
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_structured_works_with_no_gate_or_observer_injected() -> None:
    provider = _native()
    provider.script(text_response('{"choice": "a"}'))
    bound = make_bound(provider=provider)

    result = await complete_structured(bound, make_request(), _Decision)

    assert result.value.choice == "a"
