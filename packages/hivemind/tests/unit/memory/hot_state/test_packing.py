"""Tests for hivemind.memory.hot_state.packing: AssembleRequest, Prompt and assemble.

Fits into the Hive:
    Mirrors src/hivemind/memory/hot_state/packing.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.hot_state.packing for the module under test.
    - .claude/roadmap.md step 4.1 for the relevance-ordered packing this suite exercises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from builders.memory import (
    make_alarm_summary,
    make_note,
    make_pin,
    make_principal,
    make_task_summary,
    make_token_budget,
    make_trigger_event,
)

from hivemind.cell import HoneyClearance
from hivemind.llm import SectionLabel
from hivemind.memory.counter import EstimateCounter
from hivemind.memory.hot_state.packing import AssembleRequest, assemble
from hivemind.memory.hot_state.summaries import (
    AlarmSummary,
    DecisionSummary,
    QuestionSummary,
    TaskSummary,
)
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from waggle.clock import FakeClock


@dataclass
class _FakeSources:
    """A HotStateSources double whose answers are set directly, for deterministic packing tests."""

    tasks: tuple[TaskSummary, ...] = ()
    alarms: tuple[AlarmSummary, ...] = ()
    questions: tuple[QuestionSummary, ...] = ()
    decisions: tuple[DecisionSummary, ...] = ()
    pins_: tuple[Pin, ...] = field(default_factory=tuple)
    notes_: tuple[Note, ...] = ()

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        return self.tasks

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        return self.alarms

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        return self.questions

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        return self.decisions[:limit]

    async def pins(self) -> tuple[Pin, ...]:
        return self.pins_

    async def notes(self) -> tuple[Note, ...]:
        return self.notes_


def _request(clock: FakeClock, **overrides: object) -> AssembleRequest:
    fields: dict[str, object] = {
        "principal": make_principal(),
        "event": make_trigger_event(),
        "budget": make_token_budget(),
        "now": clock.now(),
    }
    fields.update(overrides)
    return AssembleRequest(**fields)


async def test_assemble_packs_a_pin_before_a_less_relevant_hot_state_item() -> None:
    clock = FakeClock()
    old_task = make_task_summary(clock=clock, updated_at=clock.now() - timedelta(days=10))
    pin = make_pin(clock=clock)
    sources = _FakeSources(tasks=(old_task,), pins_=(pin,))

    prompt = await assemble(_request(clock), sources, EstimateCounter())

    # A pin's score floor puts it ahead of any non-pin, so it is packed (and thus included) first.
    assert prompt.included[0] == pin.id
    assert SectionLabel.PINS in prompt.sections
    assert SectionLabel.HOT_STATE in prompt.sections


async def test_assemble_orders_alarms_by_severity_when_equally_recent() -> None:
    clock = FakeClock()
    now = clock.now()
    critical = make_alarm_summary(clock=clock, severity="CRITICAL", raised_at=now)
    info = make_alarm_summary(clock=clock, severity="INFO", raised_at=now)
    sources = _FakeSources(alarms=(info, critical))

    prompt = await assemble(_request(clock), sources, EstimateCounter())

    assert prompt.included.index(critical.id) < prompt.included.index(info.id)


async def test_assemble_drops_the_lowest_scored_items_on_overflow() -> None:
    clock = FakeClock()
    now = clock.now()
    # A pin (highest possible score, one token rendered) plus a very old note (lowest, several
    # tokens rendered): a budget that fits only the pin must keep it and drop the note, never the
    # other way around.
    pin = make_pin(clock=clock, text="p")
    stale_note = make_note(clock=clock, author="a", text="n", written_at=now - timedelta(days=30))
    sources = _FakeSources(pins_=(pin,), notes_=(stale_note,))
    # EstimateCounter: ceil(len(text) / 4 * 1.15). "p" alone renders to 1 token; "Note by a: n"
    # (12 chars) renders to 4 -- a 3-token budget fits the pin but not both.
    tiny_budget = make_token_budget(max_input_tokens=3, output_reserve=0)

    prompt = await assemble(_request(clock, budget=tiny_budget), sources, EstimateCounter())

    assert pin.id in prompt.included
    assert stale_note.id in prompt.dropped


async def test_assemble_replaces_an_item_over_the_item_cap_with_a_reference() -> None:
    clock = FakeClock()
    text = "a fact that is longer than a tiny cap allows here"
    pin = make_pin(clock=clock, text=text)
    sources = _FakeSources(pins_=(pin,))
    small_cap_budget = make_token_budget(item_cap_chars=10)

    prompt = await assemble(_request(clock, budget=small_cap_budget), sources, EstimateCounter())

    # Still packed (as a small reference), never dropped for being oversized.
    assert pin.id in prompt.included
    rendered = prompt.sections[SectionLabel.PINS]
    assert text not in rendered
    assert f"[ref {pin.id}: {len(text)} chars, fetch by id]" in rendered


async def test_assemble_filters_candidates_by_the_principals_clearance() -> None:
    clock = FakeClock()
    royal_note = make_note(clock=clock, clearance=HoneyClearance.C2)
    sources = _FakeSources(notes_=(royal_note,))
    principal = make_principal(clearance=HoneyClearance.C1)

    prompt = await assemble(_request(clock, principal=principal), sources, EstimateCounter())

    assert royal_note.id not in prompt.included
    assert royal_note.id not in prompt.dropped  # Filtered out before ranking, never even scored.


async def test_assemble_grants_a_task_linked_alarm_priority_over_an_unlinked_one() -> None:
    clock = FakeClock()
    now = clock.now()
    task = make_task_summary(clock=clock, updated_at=now)
    linked = make_alarm_summary(clock=clock, task_id=task.id, raised_at=now, severity="INFO")
    unlinked = make_alarm_summary(clock=clock, task_id=None, raised_at=now, severity="INFO")
    sources = _FakeSources(tasks=(task,), alarms=(linked, unlinked))

    prompt = await assemble(_request(clock), sources, EstimateCounter())

    assert prompt.included.index(linked.id) < prompt.included.index(unlinked.id)
