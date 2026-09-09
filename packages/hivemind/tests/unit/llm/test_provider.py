"""Tests for hivemind.llm.provider: the LLMProvider protocol.

LLMProvider has no logic of its own (codingrules section 8.1: a Protocol is structural), so this
module covers the two things that belong to `provider.py` itself: that it really is a
non-instantiable Protocol, and that `FakeLLMProvider` (hivemind.llm.fake) satisfies its full call
surface -- including the `provider: LLMProvider = FakeLLMProvider()` assignment below, which is a
static check `mypy --strict` enforces on every test run, not only a runtime one. The exhaustive
behavioural contract for every implementation lives in `tests/contracts/test_llm_provider_
contract.py` (roadmap step 3.8, a later dispatch), not here.

Fits into the Hive:
    Mirrors src/hivemind/llm/provider.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.provider for the module under test.
    - hivemind.llm.fake for FakeLLMProvider, the implementation exercised here.
"""

from __future__ import annotations

import pytest
from builders.llm import make_request, text_response

from hivemind.llm.capabilities import ProviderCapabilities, ProviderHealth
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.models import LLMChunk, LLMResponse
from hivemind.llm.provider import LLMProvider


def test_llm_provider_cannot_be_instantiated_directly() -> None:
    # Protocols are structural (codingrules section 8.1): only a real implementation is
    # constructible, never the Protocol class itself.
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[misc]  # The instantiation itself is the test.


async def test_fake_llm_provider_satisfies_the_llm_provider_protocol() -> None:
    fake = FakeLLMProvider()
    fake.script(text_response("hi there"))
    # This assignment is itself part of the test: mypy --strict would fail it if FakeLLMProvider
    # ever drifted from LLMProvider's shape. `script` is a FakeLLMProvider-only extra, so it is
    # called on `fake` above, before narrowing to the protocol's own call surface here.
    provider: LLMProvider = fake
    request = make_request()

    assert isinstance(provider.name, str)
    assert isinstance(provider.capabilities, ProviderCapabilities)
    response = await provider.complete(request)
    assert isinstance(response, LLMResponse)
    health = await provider.health()
    assert isinstance(health, ProviderHealth)
    token_count = await provider.count_tokens(request)
    assert token_count is None or isinstance(token_count, int)


async def test_fake_llm_provider_stream_returns_an_async_iterator_of_llm_chunk() -> None:
    fake = FakeLLMProvider()
    fake.script(text_response("abc"))
    provider: LLMProvider = fake

    chunks = [chunk async for chunk in provider.stream(make_request())]

    assert chunks
    assert all(isinstance(chunk, LLMChunk) for chunk in chunks)
