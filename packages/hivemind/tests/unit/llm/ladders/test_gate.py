"""Tests for hivemind.llm.ladders.gate: CallGate and DirectCallGate.

Fits into the Hive:
    Mirrors src/hivemind/llm/ladders/gate.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.ladders.gate for the module under test.
"""

from __future__ import annotations

from builders.llm import make_bound, make_request, text_response

from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.ladders.gate import DirectCallGate


async def test_direct_call_gate_calls_the_bound_providers_complete() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("hi"))
    bound = make_bound(provider=provider)
    gate = DirectCallGate()

    response = await gate.complete(bound, make_request())

    assert response.text == "hi"


async def test_direct_call_gate_records_the_request_on_the_provider() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("hi"))
    bound = make_bound(provider=provider)
    gate = DirectCallGate()
    request = make_request()

    await gate.complete(bound, request)

    # The gate forwards the caller's request unchanged except for the model it stamps from the
    # binding, so the provider sees exactly the same call with the binding's model id on it.
    assert provider.calls == [request.model_copy(update={"model": bound.model})]


async def test_direct_call_gate_stamps_the_bindings_model_on_the_request() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("ok"))
    bound = make_bound(provider=provider, model="local-small")

    await DirectCallGate().complete(bound, make_request())

    # The caller's request named no model; the gate filled it in from the binding, on a copy.
    assert provider.calls[0].model == "local-small"
