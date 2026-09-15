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
from datetime import timedelta

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


def test_find_returns_the_first_source_matching_provider_and_model() -> None:
    source = make_source(source_id="src_1", provider="acme", model="acme-model")
    forage_map = ForageMap([source], clock=FakeClock())

    assert forage_map.find("acme", "acme-model") == source


def test_find_returns_none_when_no_source_matches() -> None:
    forage_map = ForageMap([make_source(source_id="src_1")], clock=FakeClock())

    assert forage_map.find("no-such-provider", "no-such-model") is None


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


async def test_set_abundance_writes_the_reported_rate_figures() -> None:
    # Roadmap step 4.7a: set_abundance now writes both halves from what was actually measured,
    # rather than carrying the map's previous rate figures forward unchanged.
    source = make_source(source_id="src_1", abundance={"seats_free": 2})
    forage_map = ForageMap([source], clock=FakeClock())

    await forage_map.set_abundance(
        "src_1", seats_free=0, requests_per_minute_left=30, tokens_per_minute_left=1_000
    )

    abundance = forage_map.get("src_1").abundance
    assert abundance.seats_free == 0
    assert abundance.requests_per_minute_left == 30
    assert abundance.tokens_per_minute_left == 1_000


async def test_set_abundance_defaults_rate_figures_to_none_for_an_unmetered_source() -> None:
    # A provider that publishes no limits keeps None on both rate fields throughout: every call
    # passes None explicitly, so the source never carries an earlier call's stale figure forward.
    source = make_source(
        source_id="src_1",
        abundance={
            "seats_free": 2,
            "requests_per_minute_left": 30,
            "tokens_per_minute_left": 1_000,
        },
    )
    forage_map = ForageMap([source], clock=FakeClock())

    await forage_map.set_abundance("src_1", seats_free=1)

    abundance = forage_map.get("src_1").abundance
    assert abundance.seats_free == 1
    assert abundance.requests_per_minute_left is None
    assert abundance.tokens_per_minute_left is None


async def test_set_abundance_clears_an_existing_throttle() -> None:
    clock = FakeClock()
    forage_map = ForageMap([make_source(source_id="src_1")], clock=clock)
    await forage_map.throttle("src_1", clock.now() + timedelta(seconds=60))

    await forage_map.set_abundance("src_1", seats_free=1)

    assert forage_map.get("src_1").abundance.throttled_until is None


async def test_set_abundance_raises_on_an_unknown_id() -> None:
    forage_map = ForageMap([], clock=FakeClock())

    with pytest.raises(UnknownSourceError):
        await forage_map.set_abundance("src_missing", seats_free=1)


# ──────────────────────────────────────────────────────────────────────────────
# throttle: masks headroom to zero until the window passes (roadmap step 4.7a)
# ──────────────────────────────────────────────────────────────────────────────


async def test_throttle_masks_seats_free_and_metered_rate_fields_to_zero() -> None:
    clock = FakeClock()
    source = make_source(
        source_id="src_1",
        abundance={
            "seats_free": 4,
            "requests_per_minute_left": 30,
            "tokens_per_minute_left": 1_000,
        },
    )
    forage_map = ForageMap([source], clock=clock)

    await forage_map.throttle("src_1", clock.now() + timedelta(seconds=60))

    abundance = forage_map.get("src_1").abundance
    assert abundance.seats_free == 0
    assert abundance.requests_per_minute_left == 0
    assert abundance.tokens_per_minute_left == 0
    assert abundance.throttled_until is not None


async def test_throttle_leaves_an_unmetered_rate_field_as_none() -> None:
    # A provider that publishes no limits keeps None on both rate fields throughout, even while
    # throttled on the other dimension (seats): never invent a number it never reported.
    clock = FakeClock()
    forage_map = ForageMap([make_source(source_id="src_1")], clock=clock)

    await forage_map.throttle("src_1", clock.now() + timedelta(seconds=60))

    abundance = forage_map.get("src_1").abundance
    assert abundance.seats_free == 0
    assert abundance.requests_per_minute_left is None
    assert abundance.tokens_per_minute_left is None


async def test_throttle_raises_on_an_unknown_id() -> None:
    clock = FakeClock()
    forage_map = ForageMap([], clock=clock)

    with pytest.raises(UnknownSourceError):
        await forage_map.throttle("src_missing", clock.now() + timedelta(seconds=60))


async def test_get_still_masks_a_source_whose_window_has_not_passed() -> None:
    clock = FakeClock()
    forage_map = ForageMap([make_source(source_id="src_1")], clock=clock)
    await forage_map.throttle("src_1", clock.now() + timedelta(seconds=60))

    clock.advance(59.0)

    assert forage_map.get("src_1").abundance.seats_free == 0


async def test_get_clears_the_throttle_once_the_window_passes_with_no_timer() -> None:
    clock = FakeClock()
    source = make_source(source_id="src_1", seats=4)
    forage_map = ForageMap([source], clock=clock)
    await forage_map.throttle("src_1", clock.now() + timedelta(seconds=60))

    clock.advance(60.0)  # No timer anywhere: the very next read is what clears the mask.

    abundance = forage_map.get("src_1").abundance
    assert abundance.seats_free == 4  # Back to the spec's full seat count, not the old figure.
    assert abundance.requests_per_minute_left is None
    assert abundance.throttled_until is None


async def test_find_and_sources_and_for_slot_all_clear_an_expired_throttle_too() -> None:
    clock = FakeClock()
    source = make_source(source_id="src_1", provider="acme", model="acme-model", seats=2)
    forage_map = ForageMap([source], clock=clock)
    await forage_map.throttle("src_1", clock.now() + timedelta(seconds=60))
    clock.advance(60.0)

    assert forage_map.find("acme", "acme-model").abundance.seats_free == 2  # type: ignore[union-attr]
    assert forage_map.sources()[0].abundance.seats_free == 2
    binding = _binding(key="worker", provider="acme", model="acme-model")
    resolved = forage_map.for_slot(ModelSlot.WORKER, [binding])
    assert resolved is not None
    assert resolved.abundance.seats_free == 2


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
