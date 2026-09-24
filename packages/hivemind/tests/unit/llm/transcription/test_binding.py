"""Tests for hivemind.llm.transcription.binding: BoundTranscriber and resolve_transcriber.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/binding.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.binding for the module under test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from builders.llm import make_binding

from hivemind.forage.slots import ModelSlot
from hivemind.llm.slots import UnresolvableSlotError
from hivemind.llm.transcription.binding import resolve_transcriber
from hivemind.llm.transcription.fake import FakeTranscription
from hivemind.llm.transcription.provider import TranscriptionProvider


@dataclass
class _Lookup:
    """A TranscriberLookup that builds one FakeTranscription per (name, model) and counts asks."""

    asked: list[tuple[str, str]] = field(default_factory=list)
    built: dict[tuple[str, str], FakeTranscription] = field(default_factory=dict)

    def __call__(self, name: str, model: str) -> TranscriptionProvider:
        """Return (building on first ask) the fake for this pair."""
        self.asked.append((name, model))
        return self.built.setdefault((name, model), FakeTranscription(name=name))


def test_resolve_transcriber_starts_at_the_transcriber_slots_own_row() -> None:
    lookup = _Lookup()
    rows = [make_binding(key="transcriber", provider="local", model="speech-small")]

    bound = resolve_transcriber(rows, lookup)

    assert bound.slot is ModelSlot.TRANSCRIBER
    assert bound.binding == "transcriber"
    assert bound.model == "speech-small"
    assert bound.provider is lookup.built[("local", "speech-small")]
    assert bound.fallback is None


def test_resolve_transcriber_follows_the_fallback_chain_asking_for_each_rows_model() -> None:
    lookup = _Lookup()
    rows = [
        make_binding(key="transcriber", provider="local", model="speech-large", fallback="ears"),
        make_binding(key="ears", provider="hosted", model="speech-hosted"),
    ]

    bound = resolve_transcriber(rows, lookup)

    assert bound.fallback is not None
    assert (bound.fallback.binding, bound.fallback.model) == ("ears", "speech-hosted")
    assert bound.fallback.slot is ModelSlot.TRANSCRIBER
    assert set(lookup.asked) == {("local", "speech-large"), ("hosted", "speech-hosted")}


def test_resolve_transcriber_can_start_from_a_named_binding() -> None:
    rows = [
        make_binding(key="transcriber", provider="local", model="speech-large"),
        make_binding(key="ears", provider="hosted", model="speech-hosted"),
    ]

    bound = resolve_transcriber(rows, _Lookup(), key="ears")

    assert bound.binding == "ears"
    assert bound.slot is ModelSlot.TRANSCRIBER


def test_resolve_transcriber_ends_the_chain_at_a_missing_fallback_target() -> None:
    rows = [make_binding(key="transcriber", provider="local", model="m", fallback="gone")]

    assert resolve_transcriber(rows, _Lookup()).fallback is None


def test_resolve_transcriber_raises_for_a_missing_starting_row() -> None:
    with pytest.raises(UnresolvableSlotError):
        resolve_transcriber([make_binding(key="worker")], _Lookup())


def test_resolve_transcriber_raises_for_a_cyclic_chain() -> None:
    rows = [
        make_binding(key="transcriber", provider="a", model="m", fallback="ears"),
        make_binding(key="ears", provider="b", model="m", fallback="transcriber"),
    ]

    with pytest.raises(UnresolvableSlotError, match="cycles"):
        resolve_transcriber(rows, _Lookup())
