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
    make_cell_wax_summary,
    make_handoff,
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
from hivemind.memory.handoff import Handoff
from hivemind.memory.hot_state.packing import (
    MAX_HANDOFF_LIST_ITEMS_SHOWN,
    AssembleRequest,
    assemble,
)
from hivemind.memory.hot_state.summaries import (
    AlarmSummary,
    CellWaxSummary,
    DecisionSummary,
    QuestionSummary,
    TaskSummary,
)
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id


@dataclass
class _FakeSources:
    """A HotStateSources double whose answers are set directly, for deterministic packing tests."""

    tasks: tuple[TaskSummary, ...] = ()
    alarms: tuple[AlarmSummary, ...] = ()
    questions: tuple[QuestionSummary, ...] = ()
    decisions: tuple[DecisionSummary, ...] = ()
    pins_: tuple[Pin, ...] = field(default_factory=tuple)
    notes_: tuple[Note, ...] = ()
    wax_: tuple[CellWaxSummary, ...] = ()
    handoff_: Handoff | None = None

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

    async def wax(self, cells: frozenset[CellId]) -> tuple[CellWaxSummary, ...]:
        # A real HotStateSources.wax only ever returns notes about `cells`; this fake mirrors
        # that same contract, rather than returning `self.wax_` unconditionally, so a test that
        # forgets to set `cells_in_play` on its AssembleRequest is caught here, not silently
        # passed.
        return tuple(item for item in self.wax_ if item.cell_id in cells)

    async def handoff(self) -> Handoff | None:
        return self.handoff_


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


async def test_assemble_on_drop_collects_exactly_the_dropped_items() -> None:
    # Roadmap step 4.4: assemble's own on_drop callback is how a caller (hivemind.queen.awake.
    # episode.decide_awake, hivemind.wardens.awake.episode.decide_awake) finds out which items to
    # archive into Bee Bread so "every dropped item is findable in Bee Bread by id" holds.
    clock = FakeClock()
    now = clock.now()
    pin = make_pin(clock=clock, text="p")
    stale_note = make_note(clock=clock, author="a", text="n", written_at=now - timedelta(days=30))
    sources = _FakeSources(pins_=(pin,), notes_=(stale_note,))
    tiny_budget = make_token_budget(max_input_tokens=3, output_reserve=0)
    collected: list[object] = []

    prompt = await assemble(
        _request(clock, budget=tiny_budget), sources, EstimateCounter(), on_drop=collected.append
    )

    assert collected == [stale_note]
    assert prompt.dropped == (stale_note.id,)


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


async def test_assemble_includes_cell_wax_only_when_its_cell_is_in_play() -> None:
    """Roadmap step 4.2a: a CAUTION appears only when its Cell is a candidate (cells_in_play)."""
    clock = FakeClock()
    caution = make_cell_wax_summary(clock=clock)
    sources = _FakeSources(wax_=(caution,))

    in_play = await assemble(
        _request(clock, cells_in_play=frozenset({caution.cell_id})), sources, EstimateCounter()
    )
    not_in_play = await assemble(_request(clock), sources, EstimateCounter())

    assert caution.id in in_play.included
    assert SectionLabel.HOT_STATE in in_play.sections
    assert caution.text in in_play.sections[SectionLabel.HOT_STATE]
    assert caution.id not in not_in_play.included
    assert caution.id not in not_in_play.dropped  # Never even fetched: HotStateSources.wax's own
    # contract (module docstring) is to return nothing for a Cell not in `cells_in_play`.


async def test_assemble_ranks_a_block_wax_note_above_a_note_severity_one() -> None:
    """Roadmap step 4.2a: BLOCK > CAUTION > NOTE for Cell Wax's own severity bonus."""
    clock = FakeClock()
    now = clock.now()
    cell_id = new_cell_id(clock)
    block = make_cell_wax_summary(
        clock=clock, id="wax-block", cell_id=cell_id, severity="BLOCK", written_at=now
    )
    note = make_cell_wax_summary(
        clock=clock, id="wax-note", cell_id=cell_id, severity="NOTE", written_at=now
    )
    sources = _FakeSources(wax_=(note, block))

    prompt = await assemble(
        _request(clock, cells_in_play=frozenset({cell_id})), sources, EstimateCounter()
    )

    assert prompt.included.index(block.id) < prompt.included.index(note.id)


# ──────────────────────────────────────────────────────────────────────────────
# Defect 2: a resumed Handoff renders into its own delimited HOT_STATE block
# ──────────────────────────────────────────────────────────────────────────────


def _full_handoff(clock: FakeClock) -> Handoff:
    """Build a Handoff with every optional field populated, so a test can check each one renders."""
    return make_handoff(
        clock=clock,
        goal="Finish the four scratch files.",
        progress="Wrote step_1.txt and step_2.txt.",
        tried_and_failed=("run_command build failed: state=ROLLED_BACK",),
        constraints=("write_file step_2.txt: blocked by the diff size cap.",),
        open_threads=("In progress: was about to call write_file (step_3.txt).",),
        next_steps=("Write step_3.txt.", "Write step_4.txt."),
        do_not_redo=("Do not redo writing to step_1.txt; it already succeeded.",),
        pinned_facts=("The scratch dir quota is 10MB.",),
        notes="A short freeform note.",
    )


async def test_assemble_renders_a_resumed_handoff_as_its_own_delimited_hot_state_block() -> None:
    """Every Handoff field defect 2 asks for reaches the assembled HOT_STATE text, delimited."""
    clock = FakeClock()
    handoff = _full_handoff(clock)
    sources = _FakeSources(handoff_=handoff)

    prompt = await assemble(_request(clock), sources, EstimateCounter())

    hot_state = prompt.sections[SectionLabel.HOT_STATE]
    assert "<<<handoff>>>" in hot_state
    assert "<<<end handoff>>>" in hot_state
    assert handoff.goal in hot_state
    assert handoff.progress in hot_state
    assert handoff.tried_and_failed[0] in hot_state
    assert handoff.constraints[0] in hot_state
    assert handoff.open_threads[0] in hot_state
    assert handoff.pinned_facts[0] in hot_state
    assert handoff.notes in hot_state


async def test_assemble_renders_do_not_redo_and_next_steps_as_instruction_lists() -> None:
    """do_not_redo and next_steps read as explicit instructions, per the module's own contract."""
    clock = FakeClock()
    handoff = _full_handoff(clock)
    sources = _FakeSources(handoff_=handoff)

    prompt = await assemble(_request(clock), sources, EstimateCounter())

    hot_state = prompt.sections[SectionLabel.HOT_STATE]
    assert "Already done, do not repeat:" in hot_state
    assert f"- {handoff.do_not_redo[0]}" in hot_state
    assert "Next:" in hot_state
    assert f"- {handoff.next_steps[0]}" in hot_state
    assert f"- {handoff.next_steps[1]}" in hot_state


async def test_assemble_omits_the_handoff_block_when_not_resuming_one() -> None:
    """No Handoff (a fresh episode): no <<<handoff>>> block at all, not even an empty one."""
    clock = FakeClock()
    sources = _FakeSources(tasks=(make_task_summary(clock=clock),))

    prompt = await assemble(_request(clock), sources, EstimateCounter())

    assert "<<<handoff>>>" not in prompt.sections.get(SectionLabel.HOT_STATE, "")


async def test_assemble_never_drops_a_resumed_handoff_for_budget() -> None:
    """The handoff block renders even with a packing budget far too small for anything else.

    It is safety-critical, not a relevance-ranked candidate (module docstring).
    """
    clock = FakeClock()
    handoff = _full_handoff(clock)
    sources = _FakeSources(handoff_=handoff)
    tiny_budget = make_token_budget(max_input_tokens=1, output_reserve=0)

    prompt = await assemble(_request(clock, budget=tiny_budget), sources, EstimateCounter())

    assert "<<<handoff>>>" in prompt.sections[SectionLabel.HOT_STATE]


async def test_assemble_truncates_an_oversized_handoff_list_with_a_count() -> None:
    """A do_not_redo list past MAX_HANDOFF_LIST_ITEMS_SHOWN is truncated, with how many were cut."""
    clock = FakeClock()
    items = tuple(
        f"Do not redo writing to step_{i}.txt again."
        for i in range(MAX_HANDOFF_LIST_ITEMS_SHOWN + 3)
    )
    handoff = make_handoff(clock=clock, do_not_redo=items)
    sources = _FakeSources(handoff_=handoff)

    prompt = await assemble(_request(clock), sources, EstimateCounter())

    hot_state = prompt.sections[SectionLabel.HOT_STATE]
    for item in items[:MAX_HANDOFF_LIST_ITEMS_SHOWN]:
        assert item in hot_state
    for item in items[MAX_HANDOFF_LIST_ITEMS_SHOWN:]:
        assert item not in hot_state
    assert "(+3 more, truncated)" in hot_state


async def test_assemble_hard_truncates_a_handoff_block_still_over_the_item_cap() -> None:
    """Even after the per-list cap, a still-oversized block is hard-truncated with a char count."""
    clock = FakeClock()
    handoff = make_handoff(clock=clock, notes="x" * 1_500)  # Under Handoff.MAX_NOTES_CHARS (2000).
    sources = _FakeSources(handoff_=handoff)
    small_cap_budget = make_token_budget(item_cap_chars=200)

    prompt = await assemble(_request(clock, budget=small_cap_budget), sources, EstimateCounter())

    hot_state = prompt.sections[SectionLabel.HOT_STATE]
    assert "...[handoff truncated, " in hot_state
    assert "more chars]" in hot_state
