"""Tests for hivemind.forage.map: SlotBinding and ForageMap.

Fits into the Hive:
    Mirrors src/hivemind/forage/map.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.map for the module under test.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.forage import make_source

from hivemind.forage.errors import UnknownSourceError
from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import Effort, ModelSlot
from waggle.clock import FakeClock


def _binding(**overrides: object) -> SlotBinding:
    fields: dict[str, object] = {
        "key": "worker",
        "provider": "test-provider",
        "model": "test-model",
        "fallback": None,
        "effort": Effort.MEDIUM,
    }
    fields.update(overrides)
    return SlotBinding(**fields)


def test_forage_map_get_returns_the_matching_source() -> None:
    source = make_source(source_id="src_1")
    forage_map = ForageMap([source], clock=FakeClock())

    assert forage_map.get("src_1") == source


def test_forage_map_get_raises_on_an_unknown_id() -> None:
    forage_map = ForageMap([], clock=FakeClock())

    with pytest.raises(UnknownSourceError):
        forage_map.get("src_missing")


def test_forage_map_sources_returns_every_entry() -> None:
    sources = [make_source(source_id="src_1"), make_source(source_id="src_2")]
    forage_map = ForageMap(sources, clock=FakeClock())

    assert set(forage_map.sources()) == set(sources)


def test_forage_map_construction_keeps_the_last_of_a_repeated_source_id() -> None:
    first = make_source(source_id="src_1", grade=1)
    second = make_source(source_id="src_1", grade=5)
    forage_map = ForageMap([first, second], clock=FakeClock())

    assert forage_map.get("src_1").spec.grade == 5


def test_for_slot_resolves_the_primary_binding() -> None:
    source = make_source(source_id="src_1", provider="acme", model="acme-model")
    forage_map = ForageMap([source], clock=FakeClock())
    binding = _binding(key="worker", provider="acme", model="acme-model")

    resolved = forage_map.for_slot(ModelSlot.WORKER, [binding])

    assert resolved == source


def test_for_slot_follows_the_fallback_chain_when_the_primary_source_is_missing() -> None:
    fallback_source = make_source(source_id="src_2", provider="fallback-provider", model="fb-model")
    forage_map = ForageMap([fallback_source], clock=FakeClock())
    primary = _binding(key="worker", provider="acme", model="acme-model", fallback="local_worker")
    fallback = _binding(key="local_worker", provider="fallback-provider", model="fb-model")

    resolved = forage_map.for_slot(ModelSlot.WORKER, [primary, fallback])

    assert resolved == fallback_source


def test_for_slot_returns_none_when_no_binding_names_the_slot() -> None:
    forage_map = ForageMap([], clock=FakeClock())

    assert forage_map.for_slot(ModelSlot.WORKER, []) is None


def test_for_slot_returns_none_when_the_fallback_chain_is_exhausted() -> None:
    forage_map = ForageMap([], clock=FakeClock())
    binding = _binding(key="worker", provider="acme", model="acme-model", fallback="ghost")

    assert forage_map.for_slot(ModelSlot.WORKER, [binding]) is None


async def test_observe_updates_distance_and_stamps_measured_at() -> None:
    clock = FakeClock()
    source = make_source(source_id="src_1")
    forage_map = ForageMap([source], clock=clock)

    await forage_map.observe("src_1", latency_s=0.25, tokens_per_s=12.5)

    distance = forage_map.get("src_1").distance
    assert distance is not None
    assert distance.latency_s == 0.25
    assert distance.tokens_per_s == 12.5
    assert distance.measured_at == clock.now()


async def test_observe_raises_on_an_unknown_id() -> None:
    forage_map = ForageMap([], clock=FakeClock())

    with pytest.raises(UnknownSourceError):
        await forage_map.observe("src_missing", latency_s=1.0, tokens_per_s=1.0)


async def test_set_abundance_replaces_seats_free_and_keeps_rate_limits() -> None:
    source = make_source(
        source_id="src_1",
        abundance={
            "seats_free": 2,
            "requests_per_minute_left": 30,
            "tokens_per_minute_left": 1_000,
        },
    )
    forage_map = ForageMap([source], clock=FakeClock())

    await forage_map.set_abundance("src_1", seats_free=0)

    abundance = forage_map.get("src_1").abundance
    assert abundance.seats_free == 0
    assert abundance.requests_per_minute_left == 30
    assert abundance.tokens_per_minute_left == 1_000


async def test_set_abundance_raises_on_an_unknown_id() -> None:
    forage_map = ForageMap([], clock=FakeClock())

    with pytest.raises(UnknownSourceError):
        await forage_map.set_abundance("src_missing", seats_free=1)


async def test_observe_and_set_abundance_on_different_sources_do_not_interfere() -> None:
    clock = FakeClock()
    forage_map = ForageMap(
        [make_source(source_id="src_1"), make_source(source_id="src_2")], clock=clock
    )

    await asyncio.gather(
        forage_map.observe("src_1", latency_s=0.1, tokens_per_s=5.0),
        forage_map.set_abundance("src_2", seats_free=7),
    )

    assert forage_map.get("src_1").distance is not None
    assert forage_map.get("src_2").abundance.seats_free == 7
