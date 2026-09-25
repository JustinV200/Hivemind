"""Tests for hivemind.honey_store.ripening.drafts: the HoneyDrafts one Nectar ripens into.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/drafts.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.ripening.drafts for the module under test.
"""

from __future__ import annotations

from builders.honey import make_nectar

from hivemind.cell import HoneyClearance
from hivemind.honey_store.models import HoneyPart
from hivemind.honey_store.ripening.chunk import TextChunk
from hivemind.honey_store.ripening.drafts import (
    SUMMARY_CHUNK_INDEX,
    metadata_draft,
    part_title,
    ripened_drafts,
)
from hivemind.honey_store.ripening.summarise import SummaryOutcome
from waggle.messages.honey.exchange import MAX_TITLE_CHARS

_OUTCOME = SummaryOutcome(
    title="Staging restarts",
    summary_text="The cluster restarts nightly.\n\n- At 02:00 UTC",
    clearance=HoneyClearance.C2,
    summarised=True,
    ripener_model="ripe-1",
)


def test_ripened_drafts_puts_the_summary_first_then_one_chunk_per_chunk() -> None:
    chunks = (TextChunk(0, 0, "first chunk"), TextChunk(1, 9, "second chunk"))

    summary, first, second = ripened_drafts(_OUTCOME, chunks)

    assert (summary.part, summary.chunk_index) == (HoneyPart.SUMMARY, SUMMARY_CHUNK_INDEX)
    assert summary.title == "Staging restarts"
    assert summary.body == summary.summary == _OUTCOME.summary_text
    assert (first.part, first.chunk_index, first.body) == (HoneyPart.CHUNK, 0, "first chunk")
    assert first.title == "Staging restarts (part 1 of 2)"
    assert second.title == "Staging restarts (part 2 of 2)"
    assert first.summary == _OUTCOME.summary_text
    assert {draft.clearance for draft in (summary, first, second)} == {HoneyClearance.C2}
    assert {draft.ripener_model for draft in (summary, first, second)} == {"ripe-1"}


def test_ripened_drafts_with_no_chunks_is_the_summary_alone() -> None:
    assert [draft.part for draft in ripened_drafts(_OUTCOME, ())] == [HoneyPart.SUMMARY]


def test_part_title_shortens_a_long_title_to_fit_the_suffix() -> None:
    title = part_title("t" * MAX_TITLE_CHARS, 11, 345)

    assert len(title) == MAX_TITLE_CHARS
    assert title.endswith(" (part 12 of 345)")


def test_metadata_draft_describes_a_binary_deposit_by_type_and_size() -> None:
    nectar = make_nectar(media_type="image/png", size_bytes=2_048, clearance=HoneyClearance.C2)

    draft = metadata_draft(nectar, is_binary=True)

    assert draft.part is HoneyPart.SUMMARY
    assert draft.title == nectar.title
    assert draft.summary == draft.body == "Binary content: image/png, 2048 bytes."
    assert draft.clearance is HoneyClearance.C2
    assert draft.ripener_model is None


def test_metadata_draft_says_a_blank_text_deposit_holds_no_words() -> None:
    draft = metadata_draft(make_nectar(title=""), is_binary=False)

    assert draft.summary.startswith("Text content with no words in it")
    assert draft.title == "FINDING deposit"
