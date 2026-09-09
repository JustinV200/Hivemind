"""Tests for hivemind.llm.fanner.recorder: LlmEventRecorder and its implementations.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/recorder.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.recorder for the module under test.
"""

from __future__ import annotations

from hivemind.llm.fanner.recorder import NullLlmEventRecorder, TrailLlmEventRecorder
from hivemind.pheromone import LlmEvent, MemoryPheromoneTrail, TrailQuery
from waggle.clock import Clock, FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id

# LlmEvent's own two kinds a Fanner occurrence ever records (hivemind.llm.fanner.lane.
# LLM_CALL_KIND/LLM_SPILL_KIND); spelled out here rather than imported so this module -- which
# tests recorder.py alone -- does not need to import its sibling lane.py just for two literals.
LLM_CALL_KIND = "llm.call"
LLM_SPILL_KIND = "llm.spill"


async def test_null_llm_event_recorder_discards_without_raising() -> None:
    recorder = NullLlmEventRecorder()
    clock = FakeClock()

    # Does not raise; there is nothing else to assert about a recorder that discards everything.
    await recorder.record(LLM_CALL_KIND, new_event_id(clock), {"slot": "WORKER"})


def _build_trail_recorder() -> tuple[TrailLlmEventRecorder, MemoryPheromoneTrail, Clock]:
    """Wire a TrailLlmEventRecorder over a fresh MemoryPheromoneTrail, sharing one clock."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailLlmEventRecorder(trail, new_hive_id(clock), new_node_id(clock), "human", clock)
    return recorder, trail, clock


async def test_trail_llm_event_recorder_lifts_slot_provider_and_usage_into_typed_fields() -> None:
    recorder, trail, clock = _build_trail_recorder()

    await recorder.record(
        LLM_CALL_KIND,
        new_event_id(clock),
        {
            "slot": "WORKER",
            "provider": "acme",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_tokens": 0,
                "cost_usd": 0.02,
            },
            "latency_s": 1.5,
        },
    )

    (event,) = await trail.query(TrailQuery())
    assert isinstance(event, LlmEvent)
    assert event.kind == LLM_CALL_KIND
    assert event.slot == "WORKER"
    assert event.provider == "acme"
    assert event.usage is not None
    assert event.usage.input_tokens == 10
    assert event.usage.output_tokens == 5
    assert event.usage.cost_usd == 0.02
    # latency_s is not one of LlmEvent's typed fields, so it stays on the event's own payload.
    assert event.payload == {"latency_s": 1.5}


async def test_trail_llm_event_recorder_records_a_spill_with_no_usage() -> None:
    recorder, trail, clock = _build_trail_recorder()

    await recorder.record(
        LLM_SPILL_KIND,
        new_event_id(clock),
        {
            "slot": "WORKER",
            "provider": "acme",
            "from_binding": "worker",
            "to_binding": "local_worker",
            "reason": "GRADE_BELOW_FLOOR",
        },
    )

    (event,) = await trail.query(TrailQuery())
    assert isinstance(event, LlmEvent)
    assert event.kind == LLM_SPILL_KIND
    assert event.slot == "WORKER"
    assert event.provider == "acme"
    assert event.usage is None
    assert event.payload == {
        "from_binding": "worker",
        "to_binding": "local_worker",
        "reason": "GRADE_BELOW_FLOOR",
    }


async def test_trail_llm_event_recorder_uses_the_given_subject_id() -> None:
    recorder, trail, clock = _build_trail_recorder()
    given_subject_id = new_event_id(clock)

    # llm.spill (not llm.call) so an empty payload stays valid: llm.call alone requires
    # slot/provider/usage together (hivemind.pheromone.events.families.LlmEvent).
    await recorder.record(LLM_SPILL_KIND, given_subject_id, {})

    (event,) = await trail.query(TrailQuery())
    assert event.subject_id == given_subject_id
    assert event.subject_id != event.id


async def test_trail_llm_event_recorder_records_one_event_per_call() -> None:
    recorder, trail, clock = _build_trail_recorder()

    await recorder.record(LLM_SPILL_KIND, new_event_id(clock), {})
    await recorder.record(LLM_SPILL_KIND, new_event_id(clock), {})

    events = await trail.query(TrailQuery())
    assert len(events) == 2
    assert events[0].id != events[1].id
