"""Flood tests: ten thousand events, and ten thousand retrieved hits, never overflow a budget.

"A synthetic flood test pushes ten thousand events through the Queen and asserts the assembled
prompt never exceeds the budget and every dropped item is findable in Bee Bread." Ten thousand
Alarms are escalated to a real `hivemind.queen.human_inbox.HumanInbox` (the uncapped hot-state
category: `hivemind.queen.awake.episode.QueenSources.open_alarms` returns every one of them,
unlike `active_tasks`/`notes`/`recent_decisions`, each bounded at the source by its own limit
constant, so pushing volume there would never reach `hivemind.memory.hot_state.packing.assemble`'s
own packing loop at all); `assemble` is then called directly, through a real `QueenSources` built
from `builders.queen.make_queen_deps`'s own chamber and memory store, at a deliberately small
`TokenBudget`, so the packer has to drop most of what it was handed. Roadmap step 7.7 adds the
cold tier to the same flood: ten thousand retrieved Honey hits (`AssembleRequest.retrieved`) on
top of the ten thousand Alarms, proving the packed prompt still never exceeds the budget and the
RETRIEVED section never exceeds its own share (`TokenBudget.retrieved_fraction`).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 4.4 for the flood test's own requirement, verbatim.
    - .claude/roadmap.md step 7.7 for the cold tier folded into assemble.
    - hivemind.memory.hot_state.retrieved for pack_retrieved, the retrieved half under flood.
    - hivemind.memory.hot_state.packing for assemble, the function under flood.
    - hivemind.memory.bee_bread.deposit for deposit_dropped_items, the archive path this test
      proves every dropped Alarm reaches.
"""

from __future__ import annotations

import time

import pytest
from builders.hot_state import make_verdict
from builders.memory import make_trigger_event
from builders.queen import make_queen_deps
from builders.supervision import make_alarm

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.llm import SectionLabel
from hivemind.memory import (
    AssembleRequest,
    EstimateCounter,
    MemoryContext,
    Principal,
    RetrievedItem,
    Scorable,
    TokenBudget,
    assemble,
    deposit_dropped_items,
)
from hivemind.memory.hot_state.summaries import AlarmSummary
from hivemind.queen import QueenDeps
from hivemind.queen.awake import QueenSources
from hivemind.queen.human_inbox import HumanInbox
from waggle.clock import Clock
from waggle.messages.honey import HoneyHit, HoneyProvenance
from waggle.messages.labels import CombShieldLevel
from waggle.messages.labels import HoneyClearance as WireClearance

pytestmark = pytest.mark.e2e

_EVENT_COUNT = 10_000  # Roadmap step 4.4's own number, verbatim.
_SMALL_BUDGET = TokenBudget(max_input_tokens=2_000, output_reserve=200, item_cap_chars=4_000)
# A window ten thousand Alarms cannot fill (about 230,000 tokens of them), so what holds the
# retrieved hits back is their own share of the target, not merely what hot state left over.
_ROOMY_BUDGET = TokenBudget(
    max_input_tokens=1_000_000, output_reserve=4_000, retrieved_fraction=0.25
)
_CLEARANCES = (WireClearance.C0, WireClearance.C1, WireClearance.C2)  # Every label, in rotation.


async def test_ten_thousand_alarms_never_overflow_the_budget_and_every_drop_is_in_bee_bread() -> (
    None
):
    deps, _link, _warden_end = make_queen_deps()
    human_inbox = HumanInbox()
    clock = deps.clock
    for i in range(_EVENT_COUNT):
        alarm = make_alarm(
            clock=clock,
            detail=f"synthetic flood alarm number {i}",
            raised_at=clock.now(),
        )
        human_inbox.add_alarm(alarm)
    assert len(human_inbox.alarms) == _EVENT_COUNT

    sources = QueenSources(deps.chamber, deps.memory, human_inbox)
    principal = Principal(
        id=deps.identity.hive_id, slot=ModelSlot.QUEEN, clearance=HoneyClearance.C2, role="queen"
    )
    request = AssembleRequest(principal=principal, event=make_trigger_event(), budget=_SMALL_BUDGET)
    dropped: list[Scorable] = []

    started = time.monotonic()
    prompt = await assemble(request, sources, EstimateCounter(), on_drop=dropped.append)
    elapsed_s = time.monotonic() - started

    # The budget held by construction: the packed PINS/HOT_STATE sections alone never exceed what
    # was asked for (request.budget.max_input_tokens - output_reserve); the triggering event's own
    # text rides on top, uncounted against that packing target (codingrules 8.9's budget is for
    # "assembled content", the event is what triggered assembling it), so the full prompt is
    # compared against the packing target plus the event's own (tiny, fixed) token cost.
    target_tokens = _SMALL_BUDGET.max_input_tokens - _SMALL_BUDGET.output_reserve
    event_tokens = await EstimateCounter().count(prompt.event_text)
    assert prompt.token_count <= target_tokens + event_tokens
    # Almost everything had to be dropped at this budget; only a handful of alarms fit.
    assert len(dropped) >= _EVENT_COUNT * 0.9
    assert len(prompt.included) < _EVENT_COUNT * 0.1
    assert all(isinstance(item, AlarmSummary) for item in dropped)

    await _assert_every_drop_is_in_bee_bread(dropped, deps)

    # A generous wall-clock ceiling, not a performance assertion in itself: ten thousand items
    # scored, sorted and packed in pure Python should finish in well under this on any CI box.
    assert elapsed_s < 30.0, f"flood assemble() took {elapsed_s:.1f}s, expected well under 30s"


@pytest.mark.parametrize(
    ("budget", "hits_fit"),
    [(_SMALL_BUDGET, False), (_ROOMY_BUDGET, True)],
    ids=["small-hot-state-fills-it", "roomy-share-binds"],
)
async def test_ten_thousand_hits_on_top_of_the_alarm_flood_never_overflow_either_budget(
    budget: TokenBudget, hits_fit: bool
) -> None:
    deps, _link, _warden_end = make_queen_deps()
    human_inbox = HumanInbox()
    for i in range(_EVENT_COUNT):
        human_inbox.add_alarm(make_alarm(clock=deps.clock, detail=f"flood alarm {i}"))
    # Scanned as retrieval hands them over (roadmap 10.6b): a PASS verdict on every excerpt.
    verdict = make_verdict()
    hits = tuple(
        RetrievedItem.from_hit(_flood_hit(deps.clock, i), verdict) for i in range(_EVENT_COUNT)
    )
    principal = Principal(
        id=deps.identity.hive_id, slot=ModelSlot.QUEEN, clearance=HoneyClearance.C2, role="queen"
    )
    request = AssembleRequest(
        principal=principal, event=make_trigger_event(), budget=budget, retrieved=hits
    )
    sources = QueenSources(deps.chamber, deps.memory, human_inbox)
    counter = EstimateCounter()
    dropped: list[Scorable] = []

    started = time.monotonic()
    prompt = await assemble(request, sources, counter, on_drop=dropped.append)
    elapsed_s = time.monotonic() - started

    target_tokens = budget.max_input_tokens - budget.output_reserve
    assert prompt.token_count <= target_tokens + await counter.count(prompt.event_text)
    # The retrieved section never exceeds its own share, whatever hot state left over.
    share = int(budget.retrieved_fraction * target_tokens)
    retrieved = prompt.sections.get(SectionLabel.RETRIEVED, "")
    assert await counter.count(retrieved) <= share
    # Every hit is accounted for, once: packed or dropped for budget (all are C2-visible here).
    honey_ids = [item for item in (*prompt.included, *prompt.dropped) if item.startswith("honey:")]
    assert len(honey_ids) == len(set(honey_ids)) == _EVENT_COUNT
    # Small: hot state already fills the target, and no hit ever takes room from it. Roomy: hits
    # pack until their own share is spent, and the rest are dropped.
    packed_hits = [item for item in prompt.included if item.startswith("honey:")]
    assert bool(packed_hits) is hits_fit
    assert len(packed_hits) < _EVENT_COUNT
    assert all(isinstance(item, AlarmSummary) for item in dropped)  # on_drop never sees a hit.
    assert elapsed_s < 30.0, f"flood assemble() took {elapsed_s:.1f}s, expected well under 30s"


def _flood_hit(clock: Clock, i: int) -> HoneyHit:
    """Build the `i`th flood hit: a distinct reference, a varied score, label and excerpt size."""
    return HoneyHit(
        honey_ref=f"/hive/honey_{i:026d}",
        title=f"Flood finding {i}",
        excerpt=f"Flood excerpt {i}. " * (1 + i % 50),
        score=(i % 1_000) / 1_000,
        scope="hive",
        clearance=_CLEARANCES[i % len(_CLEARANCES)],
        origin_tier=CombShieldLevel.MEADOW,
        provenance=HoneyProvenance(task_id=None, cell_id=None, bee=None, observed_at=clock.now()),
    )


async def _assert_every_drop_is_in_bee_bread(dropped: list[Scorable], deps: QueenDeps) -> None:
    """Deposit every dropped item and prove each one is findable in Bee Bread by id (4.4)."""
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    entries = await deposit_dropped_items(dropped, ctx)
    assert len(entries) == len(dropped)
    for item, entry in zip(dropped, entries, strict=True):
        assert isinstance(item, AlarmSummary)
        assert item.id in entry.ref_ids
        fetched = await deps.memory.get_bee_bread_entry(entry.id, HoneyClearance.C2)
        assert fetched.id == entry.id
