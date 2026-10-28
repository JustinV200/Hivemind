"""Tests for hivemind.pheromone.events.families.resources: forage, memory, tool, basket, swarm, llm.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/events/families/resources.py (codingrules section 3:
    tests/unit mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.events.families.resources for the module under test.
    - test_codec.py beside this module for the tests every family shares.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.pheromone.events.base import LlmUsage
from hivemind.pheromone.events.families import (
    BasketEvent,
    ForageEvent,
    GuardEvent,
    LlmEvent,
    MemoryEvent,
    SwarmEvent,
    ToolEvent,
)
from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id


def _base_kwargs(clock: FakeClock) -> dict[str, object]:
    """Build a valid PheromoneEvent kwargs dict (minus `kind`), minting fresh ids from `clock`."""
    return {
        "id": new_id(IdKind.EVENT, clock),
        "hive_id": new_id(IdKind.HIVE, clock),
        "node_id": new_id(IdKind.NODE, clock),
        "at": clock.now(),
        "actor": "system",
        "subject_id": new_id(IdKind.TASK, clock),
        "payload": {},
    }


def test_llm_event_call_requires_slot_provider_and_usage() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        LlmEvent(kind="llm.call", **_base_kwargs(clock))


def test_llm_event_call_accepts_slot_provider_and_usage() -> None:
    clock = FakeClock()
    usage = LlmUsage(input_tokens=10, output_tokens=5, cached_tokens=0, cost_usd=0.01)

    event = LlmEvent(
        kind="llm.call", slot="WORKER", provider="anthropic", usage=usage, **_base_kwargs(clock)
    )

    assert event.slot == "WORKER"
    assert event.provider == "anthropic"
    assert event.usage == usage


@pytest.mark.parametrize("kind", ["llm.rebound", "llm.fallback", "llm.spill", "llm.throttled"])
def test_llm_event_non_call_kinds_do_not_require_slot_provider_or_usage(kind: str) -> None:
    clock = FakeClock()

    event = LlmEvent(kind=kind, **_base_kwargs(clock))

    assert event.slot is None
    assert event.provider is None
    assert event.usage is None


def test_llm_event_kinds_include_throttled() -> None:
    # Roadmap step 4.7a: the Fanner records llm.throttled after a RateLimitedError masks a
    # source's headroom to zero on the Forage map (hivemind.forage.map.ForageMap.throttle).
    assert "llm.throttled" in LlmEvent.KINDS


def test_forage_event_kinds_include_ceilings_set() -> None:
    # Roadmap step 4.8: hivemind.queen.forage.ceilings.set_ceilings/change_ceilings record this
    # kind whenever the Queen sets or changes a Warden's Ceilings.
    assert "forage.ceilings_set" in ForageEvent.KINDS


def test_forage_event_kinds_include_the_model_source_kinds() -> None:
    # Roadmap step 8.3 (ADR-0044): a Warden's server loaded a source, lost one, or the Queen
    # evicted one to reclaim the seats she exported.
    assert {"forage.source_added", "forage.source_removed", "forage.source_evicted"} <= (
        ForageEvent.KINDS
    )


def test_a_source_added_event_is_a_valid_forage_event_about_a_cell() -> None:
    clock = FakeClock()
    kwargs = _base_kwargs(clock)
    kwargs["subject_id"] = new_id(IdKind.CELL, clock)
    kwargs["payload"] = {"server_id": "box", "source_id": "small", "seats_total": 2}

    event = ForageEvent(kind="forage.source_added", **kwargs)

    assert event.payload["seats_total"] == 2


def test_memory_event_kinds_include_the_phase_3_14_additions() -> None:
    # roadmap step 3.14 (memory v0): added alongside the store that first needs them.
    assert {"memory.episode", "memory.note", "memory.pinned"} <= MemoryEvent.KINDS


def test_memory_event_kinds_include_the_phase_4_2_addition() -> None:
    # roadmap step 4.2 (Bee Bread, the warm tier): added alongside the table that first needs it.
    assert {"memory.bee_bread_deposited"} <= MemoryEvent.KINDS


def test_memory_event_kinds_include_the_phase_4_4_addition() -> None:
    # roadmap step 4.4 (overflow recovery): memory.overflow records one ContextTooLong shrink.
    assert {"memory.overflow"} <= MemoryEvent.KINDS


def test_memory_event_kinds_include_the_taint_label() -> None:
    # Roadmap step 10.6d (ADR-0043): one label, set by three paths and cleared only by a judge.
    assert {"memory.tainted", "memory.taint_cleared"} <= MemoryEvent.KINDS


_PHASE_9_TOOL_KINDS = {
    "tool.quarantine_requested",
    "tool.sandbox_provisioned",
    "tool.sandbox_released",
    "tool.announced",
    "tool.loaded",
    "tool.load_refused",
    "tool.rolled_back",
}
_BASKET_KINDS = {
    "basket.deposited",
    "basket.deposit_deduplicated",
    "basket.deposit_refused",
    "basket.offered",
    "basket.received",
    "basket.refused",
    "basket.evicted",
    "basket.retired",
    "basket.shipped",
}


def test_tool_event_kinds_keep_the_six_original_kinds_and_gain_the_phase_9_stages() -> None:
    # Phase 9 (ADR-0046, ADR-0047): the original lifecycle is unchanged; the new stages are
    # additions, so a stored event of an old kind still decodes.
    original = {
        "tool.requested",
        "tool.scaffolded",
        "tool.quarantined",
        "tool.promoted",
        "tool.rejected",
        "tool.retired",
    }

    assert original | _PHASE_9_TOOL_KINDS == ToolEvent.KINDS


@pytest.mark.parametrize("kind", sorted(_PHASE_9_TOOL_KINDS))
def test_tool_event_accepts_each_phase_9_kind(kind: str) -> None:
    event = ToolEvent(kind=kind, **_base_kwargs(FakeClock()))

    assert event.kind == kind
    assert event.family == "tool"


def test_basket_event_declares_exactly_the_nine_basket_kinds() -> None:
    assert BasketEvent.FAMILY == "basket"
    assert BasketEvent.KINDS == _BASKET_KINDS


@pytest.mark.parametrize("kind", sorted(_BASKET_KINDS))
def test_basket_event_accepts_each_kind_and_round_trips(kind: str) -> None:
    event = BasketEvent(kind=kind, **_base_kwargs(FakeClock()))

    assert event.family == "basket"
    assert BasketEvent.model_validate(event.model_dump(mode="json")) == event


@pytest.mark.parametrize("kind", ["basket.nonexistent", "tool.deposited", "blob.deposited"])
def test_basket_event_rejects_a_kind_outside_its_family(kind: str) -> None:
    with pytest.raises(ValidationError):
        BasketEvent(kind=kind, **_base_kwargs(FakeClock()))


def test_guard_event_kinds_include_the_basket_integrity_failure() -> None:
    # Phase 9 (ADR-0048): a transfer whose bytes or manifest failed verification is a guard event,
    # so the Guard Bee's rules can follow it beside the other node-integrity refusals.
    assert "guard.basket_integrity_failed" in GuardEvent.KINDS


_ORIGINAL_SWARM_KINDS = {
    "swarm.invited",
    "swarm.enrolled",
    "swarm.promoted",
    "swarm.demoted",
    "swarm.revoked",
    "swarm.command_sent",
}
# Phase 11 (ADR-0051 to ADR-0056): what the gateway and the Queen record around a device, and the
# operator's naming of the directories a device's Warden watches (ADR-0054's amendment).
_PHASE_11_SWARM_KINDS = {
    "swarm.enrol_refused",
    "swarm.online",
    "swarm.unreachable",
    "swarm.access_changed",
    "swarm.shield_changed",
    "swarm.shield_verified",
    "swarm.shield_refused",
    "swarm.node_held",
    "swarm.watch_changed",
    "swarm.packet_built",
}


def test_swarm_event_kinds_keep_the_six_original_kinds_and_gain_the_phase_11_ten() -> None:
    # The original six are unchanged, so a stored event of an old kind still decodes.
    assert SwarmEvent.FAMILY == "swarm"
    assert _ORIGINAL_SWARM_KINDS | _PHASE_11_SWARM_KINDS == SwarmEvent.KINDS
    assert len(SwarmEvent.KINDS) == 16


@pytest.mark.parametrize("kind", sorted(_ORIGINAL_SWARM_KINDS | _PHASE_11_SWARM_KINDS))
def test_swarm_event_accepts_each_kind_and_round_trips(kind: str) -> None:
    event = SwarmEvent(kind=kind, **_base_kwargs(FakeClock()))

    assert event.family == "swarm"
    assert SwarmEvent.model_validate(event.model_dump(mode="json")) == event


@pytest.mark.parametrize("kind", ["swarm.nonexistent", "device.online", "guard.online"])
def test_swarm_event_rejects_a_kind_outside_its_family(kind: str) -> None:
    with pytest.raises(ValidationError):
        SwarmEvent(kind=kind, **_base_kwargs(FakeClock()))
