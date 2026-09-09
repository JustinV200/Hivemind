"""Implement complete_structured: the structured-output degradation ladder (roadmap step 3.5).

Codingrules section 8.6 ("degrade by ladder, in one place"): a caller that needs a model's reply
shaped as a pydantic model never asks whether the bound provider can enforce a schema. It calls
`complete_structured`, and this module tries the strongest thing the binding declares, falls back
to weaker rungs on repeated failure, and falls back to the next binding in the chain on an outage --
so the same call site works unchanged whether it is bound to the strongest hosted model or the
weakest local one. A **rung** is one strategy for getting structured output out of a model: NATIVE
(the provider enforces a JSON schema itself), JSON_MODE (the provider guarantees valid JSON, but not
a specific shape, so this module validates with pydantic), or PROMPTED (a plain-text model, told in
the prompt to reply with one fenced ```json block). Which rung a binding starts on is read from its
declared `ProviderCapabilities` (codingrules section 8.6: "core code branches on capabilities,
never on provider name"), never from `provider.name`.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.ladders`. Called by
    every awake episode (`queen/awake`, `wardens/awake`, roadmap steps 3.19-3.20, later dispatches)
    that needs a structured decision back from a model. Calls into `hivemind.llm.ladders`'
    siblings (`extraction`, `gate`, `observer`), `hivemind.llm.errors`, `hivemind.llm.models`,
    `hivemind.llm.slots` and `hivemind.llm.capabilities` only.

Key invariants:
    - Rung order is fixed: NATIVE -> JSON_MODE -> PROMPTED. A binding starts on the strongest rung
      its `ProviderCapabilities` supports and only ever steps down, never back up, within one
      binding attempt.
    - `ContextTooLongError` is never caught here: it propagates unchanged to `complete_structured`'s
      caller, which owns the prompt budget and must shrink it (codingrules section 8.6's ladder
      rule stops at retries and fallback; a context overflow is not either).
    - `ProviderUnavailableError`/`RateLimitedError` move the whole ladder to `bound.fallback` (a
      fresh restart at the fallback's own top rung); every other rung-exhaustion path raises
      `MalformedOutputError` instead, never falls back to another binding.
    - This module keeps its cross-call turn-building in a local variable named `turns`, never
      `messages`/`history`: `scripts/check_no_transcripts.py` does not allowlist this file (unlike
      `hivemind.llm.models` and `hivemind.llm.ladders.tools`), and each retry request is built
      fresh from the caller's own `request.messages`, never accumulated across attempts.

See Also:
    - .claude/codingrules.md section 8.6 for "degrade by ladder, in one place".
    - docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md for the decision this
      module implements.
    - hivemind.llm.ladders.extraction for the fenced-block extraction the PROMPTED rung uses.
    - hivemind.llm.ladders.gate for CallGate, the seam every attempt calls through.
    - hivemind.llm.ladders.observer for LadderObserver and FallbackNote, how a step-down is
      reported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import cast

from pydantic import BaseModel, ValidationError

from hivemind.forage.slots import ModelSlot
from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import (
    MalformedOutputError,
    ProviderUnavailableError,
    RateLimitedError,
    RefusedError,
)
from hivemind.llm.ladders.extraction import extract_json_block, render_json_preamble
from hivemind.llm.ladders.gate import CallGate, DirectCallGate
from hivemind.llm.ladders.observer import (
    FallbackNote,
    FallbackReason,
    LadderObserver,
    NullLadderObserver,
)
from hivemind.llm.models import (
    JsonObject,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    StopReason,
    Usage,
)
from hivemind.llm.slots import BoundModel

NATIVE_SCHEMA_RETRIES = 1  # Native enforcement rarely fails; one retry covers a transient.
JSON_MODE_RETRIES = 2  # JSON mode drifts on long outputs; two validation retries.
PROMPTED_JSON_RETRIES = 3  # A plain-text model needs the most coaxing.

__all__ = [
    "JSON_MODE_RETRIES",
    "NATIVE_SCHEMA_RETRIES",
    "PROMPTED_JSON_RETRIES",
    "Rung",
    "StructuredResult",
    "complete_structured",
]


class Rung(Enum):
    """One step of the structured-output degradation ladder, strongest first."""

    NATIVE = "NATIVE"  # The provider enforces response_schema itself.
    JSON_MODE = "JSON_MODE"  # The provider guarantees valid JSON, not a specific schema.
    PROMPTED = "PROMPTED"  # Plain text; the schema and reply format are requested in the prompt.


@dataclass(frozen=True, slots=True)
class StructuredResult[ModelT: BaseModel]:
    """One `complete_structured` call's outcome: the parsed value and how it was obtained."""

    value: ModelT  # The schema-validated value.
    rung: Rung  # Which rung actually produced it.
    attempts: int  # How many model calls the winning binding made before succeeding.
    usage: Usage  # Summed token/cost accounting across every attempt on the winning binding.


@dataclass(frozen=True, slots=True)
class _LadderTools:
    """Group a ladder run's fixed collaborators, so `_run_ladder` stays within 5 parameters."""

    gate: CallGate
    observer: LadderObserver
    slot: ModelSlot


async def complete_structured[ModelT: BaseModel](
    bound: BoundModel,
    request: LLMRequest,
    schema: type[ModelT],
    *,
    gate: CallGate | None = None,
    observer: LadderObserver | None = None,
) -> StructuredResult[ModelT]:
    """Get a `schema`-shaped reply out of `bound`, degrading rungs and bindings as needed.

    Args:
        bound: The binding to call, and its fallback chain.
        request: The call to make; its `response_schema` is overwritten per rung, never read.
        schema: The pydantic model the reply must validate against.
        gate: How to make each call; `DirectCallGate()` (no metering) when omitted.
        observer: Who to tell about a step-down; `NullLadderObserver()` (discards) when omitted.

    Returns:
        A StructuredResult holding the validated value, the rung that produced it, the attempt
        count and the summed usage, all scoped to the binding that succeeded.

    Raises:
        MalformedOutputError: Every rung down to PROMPTED exhausted its retries on `bound` (and
            every fallback binding after it, if any).
        RefusedError: A response's `stop_reason` was REFUSAL.
        ContextTooLongError: Propagated unchanged; the caller must shrink `request` and retry.
    """
    active_gate = gate if gate is not None else DirectCallGate()
    active_observer = observer if observer is not None else NullLadderObserver()
    tools = _LadderTools(gate=active_gate, observer=active_observer, slot=bound.slot)
    current = bound
    while True:
        try:
            return await _run_ladder(current, request, schema, tools)
        except (ProviderUnavailableError, RateLimitedError) as exc:
            # The gate could not reach `current` at all; a rung step-down cannot fix that, so the
            # whole ladder moves to the next binding and restarts fresh (module docstring).
            if current.fallback is None:
                raise
            await active_observer.on_fallback(
                FallbackNote(
                    slot=bound.slot,
                    from_binding=current.binding,
                    to_binding=current.fallback.binding,
                    from_rung=None,
                    to_rung=None,
                    reason=_outage_reason(exc),
                )
            )
            current = current.fallback


async def _run_ladder[ModelT: BaseModel](
    current: BoundModel, request: LLMRequest, schema: type[ModelT], tools: _LadderTools
) -> StructuredResult[ModelT]:
    """Run every rung `current` supports, in order, until one produces a valid value.

    Raises:
        MalformedOutputError: PROMPTED's own retries were exhausted too.
        RefusedError: A response's stop_reason was REFUSAL.
        ProviderUnavailableError | RateLimitedError: Left to propagate; `complete_structured`
            decides whether to fall back to `current.fallback`.
    """
    rung = _initial_rung(current.provider.capabilities)
    attempts = 0
    correction: str | None = None
    last_raw = ""
    total_usage = Usage(input_tokens=0, output_tokens=0)
    while True:
        for _ in range(_retry_budget(rung) + 1):
            attempt_request = _build_request_for_rung(request, rung, schema, correction)
            response = await tools.gate.complete(current, attempt_request)
            attempts += 1
            total_usage = total_usage + response.usage
            if response.stop_reason is StopReason.REFUSAL:
                reason = response.reasoning_summary or "no reason given"
                raise RefusedError(current.provider.name, reason)
            try:
                value = _parse_structured(response, rung, schema)
            except _StructuredParseError as exc:
                last_raw = response.text
                correction = str(exc)
                continue
            return StructuredResult(value=value, rung=rung, attempts=attempts, usage=total_usage)
        next_rung = _step_down(rung)
        if next_rung is None:
            raise MalformedOutputError(current.provider.name, last_raw, attempts)
        await tools.observer.on_fallback(
            FallbackNote(
                slot=tools.slot,
                from_binding=current.binding,
                to_binding=None,
                from_rung=rung,
                to_rung=next_rung,
                reason=FallbackReason.RUNG_EXHAUSTED,
            )
        )
        rung = next_rung
        correction = None


class _StructuredParseError(Exception):
    """Raise when a rung's response fails to parse or validate; never escapes this module."""


def _parse_structured[ModelT: BaseModel](
    response: LLMResponse, rung: Rung, schema: type[ModelT]
) -> ModelT:
    """Parse and validate one response against `schema`, or raise `_StructuredParseError`."""
    raw_text = response.text
    if rung is Rung.PROMPTED:
        block = extract_json_block(raw_text)
        if block is None:
            raise _StructuredParseError("no fenced ```json block was found in the reply.")
        raw_text = block
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise _StructuredParseError(f"reply was not valid JSON: {exc}.") from exc
    try:
        return schema.model_validate(parsed)
    except ValidationError as exc:
        raise _StructuredParseError(f"reply did not match the schema: {exc}.") from exc


def _initial_rung(capabilities: ProviderCapabilities) -> Rung:
    """Return the strongest rung `capabilities` supports."""
    if capabilities.schema_output:
        return Rung.NATIVE
    if capabilities.json_mode:
        return Rung.JSON_MODE
    return Rung.PROMPTED


_RUNG_ORDER: tuple[Rung, ...] = (Rung.NATIVE, Rung.JSON_MODE, Rung.PROMPTED)
_RETRY_BUDGETS: dict[Rung, int] = {
    Rung.NATIVE: NATIVE_SCHEMA_RETRIES,
    Rung.JSON_MODE: JSON_MODE_RETRIES,
    Rung.PROMPTED: PROMPTED_JSON_RETRIES,
}


def _retry_budget(rung: Rung) -> int:
    """Return how many retries (beyond the first attempt) `rung` gets."""
    return _RETRY_BUDGETS[rung]


def _step_down(rung: Rung) -> Rung | None:
    """Return the next weaker rung after `rung`, or None when `rung` is already PROMPTED."""
    index = _RUNG_ORDER.index(rung) + 1
    return _RUNG_ORDER[index] if index < len(_RUNG_ORDER) else None


def _build_request_for_rung(
    base: LLMRequest, rung: Rung, schema: type[BaseModel], correction: str | None
) -> LLMRequest:
    """Build one attempt's request for `rung`, folding in a validation `correction` if retrying."""
    if rung is Rung.PROMPTED:
        preamble = render_json_preamble(cast(JsonObject, schema.model_json_schema()))
        turns = (*base.messages, Message.text(Role.USER, preamble))
        if correction is not None:
            turns = (*turns, Message.text(Role.USER, correction))
        return base.model_copy(update={"messages": turns, "response_schema": None})
    # NATIVE and JSON_MODE both send the schema; the adapter is what turns JSON mode on for a
    # provider that lacks schema_output (ADR-0009: the rung picks the hint, the adapter the wire).
    turns = (
        base.messages
        if correction is None
        else (*base.messages, Message.text(Role.USER, correction))
    )
    schema_json = cast(JsonObject, schema.model_json_schema())
    return base.model_copy(update={"messages": turns, "response_schema": schema_json})


def _outage_reason(exc: ProviderUnavailableError | RateLimitedError) -> FallbackReason:
    """Map a gate outage exception to the FallbackReason it represents."""
    return (
        FallbackReason.RATE_LIMITED
        if isinstance(exc, RateLimitedError)
        else FallbackReason.PROVIDER_UNAVAILABLE
    )
