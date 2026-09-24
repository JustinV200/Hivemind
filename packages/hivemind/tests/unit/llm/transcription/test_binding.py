"""Tests for hivemind.llm.transcription.binding: resolving TRANSCRIBER to a BoundTranscriber.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/binding.py (codingrules section 3). The registry's
    production lookup (kind refusal, caching, offline) is covered by
    unit/llm/test_registry_transcriber.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.binding for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_source
from builders.llm import make_binding

from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.models import ModelCost
from hivemind.forage.slots import ModelSlot
from hivemind.llm.slots import UnresolvableSlotError
from hivemind.llm.transcription import (
    BoundTranscriber,
    FakeTranscription,
    TranscriptionProvider,
    TranscriptionUnsupportedError,
    resolve_transcriber,
)
from waggle.clock import FakeClock

_TRANSCRIBER = make_binding(key="transcriber", provider="local", model="test-model")


class _RecordingLookup:
    """A TranscriberLookup that builds a FakeTranscription per provider and records the order."""

    def __init__(self, refuse: frozenset[str] = frozenset()) -> None:
        self.looked_up: list[str] = []  # Each row's key, in lookup order.
        self._refuse = refuse

    def __call__(self, binding: SlotBinding) -> TranscriptionProvider:
        self.looked_up.append(binding.key)
        if binding.provider in self._refuse:
            raise TranscriptionUnsupportedError(
                binding.provider, "anthropic", binding.key, ["fake"]
            )
        return FakeTranscription(name=binding.provider)


def test_resolve_binds_the_slots_own_row_to_its_provider_and_model() -> None:
    bound = resolve_transcriber(ModelSlot.TRANSCRIBER, [_TRANSCRIBER], _RecordingLookup())

    assert bound.slot is ModelSlot.TRANSCRIBER
    assert bound.binding == "transcriber"
    assert bound.provider.name == "local"
    assert bound.model == "test-model"
    assert bound.fallback is None


def test_resolve_follows_the_fallback_chain_looking_up_the_head_first() -> None:
    head = make_binding(key="transcriber", provider="local", fallback="hosted_ears")
    tail = make_binding(key="hosted_ears", provider="hosted")
    lookup = _RecordingLookup()

    bound = resolve_transcriber(ModelSlot.TRANSCRIBER, [head, tail], lookup)

    assert lookup.looked_up == ["transcriber", "hosted_ears"]
    assert bound.fallback is not None
    assert (bound.fallback.binding, bound.fallback.provider.name) == ("hosted_ears", "hosted")
    assert bound.fallback.slot is ModelSlot.TRANSCRIBER


def test_one_unusable_fallback_refuses_the_whole_binding() -> None:
    head = make_binding(key="transcriber", provider="local", fallback="chat_only")
    tail = make_binding(key="chat_only", provider="anthropic")

    with pytest.raises(TranscriptionUnsupportedError) as excinfo:
        resolve_transcriber(
            ModelSlot.TRANSCRIBER, [head, tail], _RecordingLookup(frozenset({"anthropic"}))
        )

    assert excinfo.value.binding == "chat_only"


def test_resolve_raises_when_the_slot_has_no_row() -> None:
    with pytest.raises(UnresolvableSlotError):
        resolve_transcriber(ModelSlot.TRANSCRIBER, [], _RecordingLookup())


def test_a_map_source_prices_the_binding_per_audio_minute() -> None:
    source = make_source(
        provider="local", model="test-model", cost=ModelCost(cost_per_audio_minute_usd=0.006)
    )
    forage_map = ForageMap([source], clock=FakeClock())

    bound = resolve_transcriber(
        ModelSlot.TRANSCRIBER, [_TRANSCRIBER], _RecordingLookup(), forage_map
    )

    assert bound.cost_per_audio_minute_usd == 0.006
    assert bound.cost_usd(90.0) == pytest.approx(0.009)


@pytest.mark.parametrize("has_map", [False, True])
def test_a_binding_with_no_map_or_no_matching_source_is_unpriced(has_map: bool) -> None:
    forage_map = ForageMap([], clock=FakeClock()) if has_map else None

    bound = resolve_transcriber(
        ModelSlot.TRANSCRIBER, [_TRANSCRIBER], _RecordingLookup(), forage_map
    )

    assert bound.cost_per_audio_minute_usd is None
    assert bound.cost_usd(60.0) is None


def test_a_bound_transcriber_is_frozen() -> None:
    bound = BoundTranscriber(
        slot=ModelSlot.TRANSCRIBER,
        binding="transcriber",
        provider=FakeTranscription(),
        model="test-model",
        cost_per_audio_minute_usd=None,
    )

    with pytest.raises(AttributeError):
        bound.model = "other"  # type: ignore[misc]  # The assignment is the test.
