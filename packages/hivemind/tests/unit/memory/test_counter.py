"""Tests for hivemind.memory.counter: EstimateCounter's arithmetic and ProviderCounter's fallback.

Fits into the Hive:
    Mirrors src/hivemind/memory/counter.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.counter for the module under test.
"""

from __future__ import annotations

from hivemind.forage.slots import ModelSlot
from hivemind.llm import FakeLLMProvider, ProviderCapabilities
from hivemind.memory.counter import EstimateCounter, ProviderCounter


async def test_estimate_counter_scales_char_count_by_the_margin() -> None:
    counter = EstimateCounter(chars_per_token=4, margin=1.15)

    # 40 chars / 4 = 10 tokens; * 1.15 = 11.5, rounded up to 12.
    result = await counter.count("x" * 40)

    assert result == 12


async def test_estimate_counter_handles_empty_text() -> None:
    counter = EstimateCounter()

    assert await counter.count("") == 0


async def test_provider_counter_uses_the_providers_own_count_when_available() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    counter = ProviderCounter(provider, ModelSlot.WORKER, EstimateCounter())

    # FakeLLMProvider.count_tokens: len(text) // 4 when token_counting is declared.
    result = await counter.count("x" * 40)

    assert result == 10


async def test_provider_counter_falls_back_when_the_provider_cannot_count() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    fallback = EstimateCounter(chars_per_token=4, margin=1.0)
    counter = ProviderCounter(provider, ModelSlot.WORKER, fallback)

    result = await counter.count("x" * 40)

    # ProviderCapabilities.none() declares no token counting: count_tokens returns None, so the
    # fallback (a plain 4-chars-per-token estimate with no margin) answers instead.
    assert result == 10
