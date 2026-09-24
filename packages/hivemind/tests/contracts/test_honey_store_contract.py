"""Contract suite for HoneyStore: one contract, run over three SqliteHoneyStore harnesses.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of
    hivemind.honey_store.store.protocol.HoneyStore's contract and runs against three harnesses of
    the one shipped implementation (`hivemind.honey_store.store.sqlite.SqliteHoneyStore`): a temp
    file with sqlite-vec, a temp file with the Python vector fallback forced on, and `:memory:`
    with sqlite-vec -- so both storage modes and both vector backends are exercised (ADR-0031). A
    future in-memory fake joins the same fixture's params and must pass here before it is used
    anywhere else (codingrules 14.3). The `Harness` shape and the small builders every clause
    shares (`event`, `nectar_events`, `prune_events`, `sha`, `ripen`) live in `honey_store_
    contract_harness.py`, imported below, so this file holds the fixture and contract clauses
    only and stays under codingrules 5.1's 400-line test-file limit.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.store.protocol for the HoneyStore contract under test.
    - packages/hivemind/tests/contracts/test_memory_store_contract.py for the pattern this mirrors.
    - packages/hivemind/tests/contracts/honey_store_contract_harness.py for the Harness shape and
      shared builders.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.honey import make_honey_draft, make_nectar_draft
from contracts.honey_store_contract_harness import (
    HARNESS_KINDS,
    Harness,
    event,
    nectar_events,
    open_harness,
    prune_events,
    ripen,
    sha,
)

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.errors import NectarNotFoundError, NectarNotRipenableError
from hivemind.honey_store.models import HoneyPart, NectarState, ReadFilter
from hivemind.honey_store.scope import HIVE_SCOPE, task_scope
from hivemind.honey_store.store import build_match
from hivemind.pheromone import TrailQuery
from waggle.ids import new_cell_id, new_nectar_id, new_task_id

_FORBIDDEN_ROW_COUNT = 20  # Enough that an accidental "filter after limit" bug cannot pass by luck.


@pytest.fixture(params=HARNESS_KINDS)
async def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Harness:
    """Build the parametrised harness: a fresh honey store over an already-trailed connection."""
    return await open_harness(request.param, tmp_path)


# ──────────────────────────────────────────────────────────────────────────────
# Nectar intake: dedupe, raise-only label merge
# ──────────────────────────────────────────────────────────────────────────────


async def test_add_nectar_dedupes_by_source_key_and_raises_stored_and_honey_labels(
    harness: Harness,
) -> None:
    draft = make_nectar_draft(
        clock=harness.clock,
        source_key="handoff:evt1",
        clearance=HoneyClearance.C0,
        task_id=None,
        bee=None,
    )
    first = await harness.store.add_nectar(draft, sha(draft), nectar_events(harness.clock))
    honey_rows = await harness.store.ripen(
        first.nectar.id,
        (make_honey_draft(clearance=HoneyClearance.C0),),
        event(harness.clock, "honey.ripened", first.nectar.id),
    )

    higher = draft.model_copy(update={"clearance": HoneyClearance.C2})
    second = await harness.store.add_nectar(higher, sha(draft), nectar_events(harness.clock))

    assert second.is_new is False
    assert second.raised_from == HoneyClearance.C0
    assert second.nectar.clearance == HoneyClearance.C2
    raised_honey = await harness.store.get_honey(honey_rows[0].id)
    assert raised_honey.clearance == HoneyClearance.C2  # Raised alongside the Nectar, same call.


async def test_add_nectar_dedupes_by_sha256_when_no_source_key(harness: Harness) -> None:
    draft = make_nectar_draft(clock=harness.clock, task_id=None, bee=None)
    first = await harness.store.add_nectar(draft, sha(draft), nectar_events(harness.clock))

    same_content = make_nectar_draft(
        clock=harness.clock,
        content=draft.content,
        clearance=draft.clearance,
        task_id=None,
        bee=None,
    )
    second = await harness.store.add_nectar(same_content, sha(draft), nectar_events(harness.clock))

    assert second.is_new is False
    assert second.nectar.id == first.nectar.id


async def test_add_nectar_duplicate_with_a_lower_label_never_lowers_the_stored_one(
    harness: Harness,
) -> None:
    draft = make_nectar_draft(
        clock=harness.clock, clearance=HoneyClearance.C2, task_id=None, bee=None
    )
    await harness.store.add_nectar(draft, sha(draft), nectar_events(harness.clock))

    lower = draft.model_copy(update={"clearance": HoneyClearance.C0})
    second = await harness.store.add_nectar(lower, sha(draft), nectar_events(harness.clock))

    assert second.raised_from is None
    assert second.nectar.clearance == HoneyClearance.C2


async def test_add_nectar_records_the_events_built_from_its_own_outcome(
    harness: Harness,
) -> None:
    draft = make_nectar_draft(clock=harness.clock, task_id=None, bee=None)

    first = await harness.store.add_nectar(draft, sha(draft), nectar_events(harness.clock))
    harness.clock.advance(
        1
    )  # Two events in one instant would come back in id order, not call order.
    second = await harness.store.add_nectar(draft, sha(draft), nectar_events(harness.clock))

    recorded = await harness.trail.query(TrailQuery(subject_id=first.nectar.id))
    # The builder saw the minted id and whether each call was new, inside the same transaction.
    assert [item.kind for item in recorded] == [
        "honey.nectar_received",
        "honey.nectar_deduplicated",
    ]
    assert second.nectar.id == first.nectar.id


async def test_add_nectar_records_nothing_when_the_builder_returns_no_event(
    harness: Harness,
) -> None:
    # ADR-0031: a Night Veil Cell's ephemeral deposit leaves no trail event at all.
    night_veil_cell = new_cell_id(harness.clock)
    draft = make_nectar_draft(
        clock=harness.clock,
        cell_id=night_veil_cell,
        ephemeral_cell_id=night_veil_cell,
        origin_tier=CombShieldLevel.NIGHT_VEIL,
        task_id=None,
        bee=None,
    )

    added = await harness.store.add_nectar(draft, sha(draft), lambda _added: ())

    assert added.nectar.state is NectarState.EPHEMERAL
    assert await harness.trail.query(TrailQuery(subject_id=added.nectar.id)) == ()


async def test_an_ordinary_deposit_never_dedupes_onto_an_ephemeral_row(harness: Harness) -> None:
    night_veil_cell = new_cell_id(harness.clock)
    ephemeral = make_nectar_draft(
        clock=harness.clock,
        cell_id=night_veil_cell,
        ephemeral_cell_id=night_veil_cell,
        origin_tier=CombShieldLevel.NIGHT_VEIL,
        task_id=None,
        bee=None,
    )
    ordinary = make_nectar_draft(
        clock=harness.clock, content=ephemeral.content, task_id=None, bee=None
    )
    await harness.store.add_nectar(ephemeral, sha(ephemeral), lambda _added: ())

    added = await harness.store.add_nectar(ordinary, sha(ordinary), nectar_events(harness.clock))
    await harness.store.purge_ephemeral(night_veil_cell)

    # Merged onto the ephemeral row, the ordinary deposit would have vanished with the purge.
    assert added.is_new is True
    assert (await harness.store.get_nectar(added.nectar.id)).state is NectarState.RECEIVED


async def test_an_ephemeral_deposit_never_dedupes_onto_an_ordinary_row(harness: Harness) -> None:
    ordinary = make_nectar_draft(
        clock=harness.clock, clearance=HoneyClearance.C0, task_id=None, bee=None
    )
    stored = await harness.store.add_nectar(ordinary, sha(ordinary), nectar_events(harness.clock))
    night_veil_cell = new_cell_id(harness.clock)
    ephemeral = make_nectar_draft(
        clock=harness.clock,
        content=ordinary.content,
        clearance=HoneyClearance.C2,
        cell_id=night_veil_cell,
        ephemeral_cell_id=night_veil_cell,
        origin_tier=CombShieldLevel.NIGHT_VEIL,
        task_id=None,
        bee=None,
    )

    added = await harness.store.add_nectar(ephemeral, sha(ephemeral), lambda _added: ())

    # A Night Veil Cell can never change a persistent row, not even by raising its label.
    assert added.is_new is True
    assert added.nectar.state is NectarState.EPHEMERAL
    assert (await harness.store.get_nectar(stored.nectar.id)).clearance == HoneyClearance.C0


# ──────────────────────────────────────────────────────────────────────────────
# Repeat sources: a content duplicate's extra provenance (ADR-0033)
# ──────────────────────────────────────────────────────────────────────────────


async def test_add_nectar_records_and_deduplicates_extra_sources(harness: Harness) -> None:
    original = make_nectar_draft(clock=harness.clock, source_key="handoff:evt1")
    added = await harness.store.add_nectar(original, sha(original), nectar_events(harness.clock))
    # Found by source_key (the row's own): never a content match, so no source at all.
    by_key = original.model_copy(update={"task_id": new_task_id(harness.clock)})
    await harness.store.add_nectar(by_key, sha(original), nectar_events(harness.clock))
    assert await harness.store.nectar_sources(added.nectar.id) == ()
    assert await harness.store.has_source("task_outcome:new") is False
    assert await harness.store.has_source("handoff:evt1") is True  # The row's own key.

    # A content match with a new source_key: a genuinely new source, found by content alone.
    second_task = new_task_id(harness.clock)
    fresh = original.model_copy(update={"task_id": second_task, "source_key": "task_outcome:new"})
    result = await harness.store.add_nectar(fresh, sha(original), nectar_events(harness.clock))
    # Unchanged by the second sender.
    assert (result.is_new, result.nectar.source_key) == (False, original.source_key)
    assert await harness.store.has_source("task_outcome:new") is True
    sources = await harness.store.nectar_sources(added.nectar.id)
    assert [(source.task_id, source.source_key) for source in sources] == [
        (second_task, "task_outcome:new")
    ]

    # The exact same source, delivered again: the store's own uniqueness rule (not a Python
    # pre-check) is what keeps this a no-op rather than a second row.
    await harness.store.add_nectar(fresh, sha(original), nectar_events(harness.clock))
    assert len(await harness.store.nectar_sources(added.nectar.id)) == 1


async def test_purge_ephemeral_removes_an_ephemeral_rows_source_rows_too(
    harness: Harness,
) -> None:
    night_veil_cell = new_cell_id(harness.clock)
    first = make_nectar_draft(
        clock=harness.clock,
        cell_id=night_veil_cell,
        ephemeral_cell_id=night_veil_cell,
        origin_tier=CombShieldLevel.NIGHT_VEIL,
        task_id=new_task_id(harness.clock),
        bee=None,
    )
    added = await harness.store.add_nectar(first, sha(first), lambda _added: ())
    # A second ephemeral deposit, same Cell and content, a different task: an extra source, since
    # the Night Veil boundary keeps its sha256 match scoped to this Cell's own ephemeral rows.
    second = first.model_copy(update={"task_id": new_task_id(harness.clock)})
    await harness.store.add_nectar(second, sha(first), lambda _added: ())
    assert len(await harness.store.nectar_sources(added.nectar.id)) == 1

    assert await harness.store.purge_ephemeral(night_veil_cell) == 1
    # nectar_sources on a now-purged id would trivially read back empty; the raw connection is
    # what actually proves the FK's ON DELETE CASCADE removed the child row, not just the parent.
    query = "SELECT COUNT(*) AS n FROM honey_nectar_sources WHERE nectar_id = ?"
    assert harness.connection.execute(query, (added.nectar.id,)).fetchone()["n"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# Ripening: idempotent per (nectar_id, part, chunk_index)
# ──────────────────────────────────────────────────────────────────────────────


async def test_ripen_is_idempotent_and_never_duplicates_a_part(harness: Harness) -> None:
    honey = await ripen(harness, body="the original body")

    again = await harness.store.ripen(
        honey.nectar_id,
        (make_honey_draft(body="a different body", summary="a different body"),),
        event(harness.clock, "honey.ripened", honey.nectar_id),
    )

    assert again[0].id == honey.id
    assert again[0].body == "the original body"  # Never overwritten by the second call.


async def test_ripen_never_stores_a_part_below_its_nectars_current_label(
    harness: Harness,
) -> None:
    # A more sensitive duplicate raised the Nectar while its summary was being written: the
    # drafts still carry the old label, and the store raises them in the same transaction.
    draft = make_nectar_draft(clock=harness.clock, clearance=HoneyClearance.C0, task_id=None)
    added = await harness.store.add_nectar(draft, sha(draft), nectar_events(harness.clock))
    higher = draft.model_copy(update={"clearance": HoneyClearance.C2})
    await harness.store.add_nectar(higher, sha(draft), nectar_events(harness.clock))

    rows = await harness.store.ripen(
        added.nectar.id,
        (make_honey_draft(clearance=HoneyClearance.C0),),
        event(harness.clock, "honey.ripened", added.nectar.id),
    )

    assert [row.clearance for row in rows] == [HoneyClearance.C2]
    stored = await harness.store.get_honey(rows[0].id)
    assert stored.clearance == HoneyClearance.C2


# ──────────────────────────────────────────────────────────────────────────────
# Search: FTS finds a word, vector search orders and isolates by model
# ──────────────────────────────────────────────────────────────────────────────


async def test_search_text_finds_a_word_in_the_body(harness: Harness) -> None:
    honey = await ripen(harness, body="The gizmo assembly needs realignment.")
    match = build_match("gizmo")
    assert match is not None
    filter_ = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)

    hits = await harness.store.search_text(match, filter_, 10)

    assert honey.id in {candidate.honey.id for candidate in hits}


async def test_a_relabelled_row_stays_findable_by_text(harness: Harness) -> None:
    # The FTS update trigger fires only on indexed text; a relabel must not drop the entry.
    honey = await ripen(harness, body="The sprocket gauge reads high.")
    await harness.store.raise_clearance(
        honey.id, HoneyClearance.C2, event(harness.clock, "honey.label_raised", honey.id)
    )
    match = build_match("sprocket")
    assert match is not None
    filter_ = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)

    hits = await harness.store.search_text(match, filter_, 10)

    assert [candidate.honey.id for candidate in hits] == [honey.id]


async def test_search_vectors_orders_nearest_first_and_isolates_by_model(harness: Harness) -> None:
    near = await ripen(harness, body="near")
    far = await ripen(harness, body="far")
    other_model = await ripen(harness, body="other model")
    await harness.store.set_vectors([(near.id, (1.0, 0.0)), (far.id, (0.0, 1.0))], "model-a", None)
    await harness.store.set_vectors([(other_model.id, (1.0, 0.0))], "model-b", None)
    filter_ = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)

    hits = await harness.store.search_vectors((0.9, 0.1), "model-a", filter_, 10)

    assert [candidate.honey.id for candidate in hits] == [near.id, far.id]  # Nearest first.
    assert other_model.id not in {candidate.honey.id for candidate in hits}  # Different model.


async def test_search_vectors_skips_a_stored_vector_of_another_length(harness: Harness) -> None:
    # Same model id, different output size (a reconfigured server): skipped, never a failed query.
    matching = await ripen(harness, body="two dims")
    stale = await ripen(harness, body="three dims")
    await harness.store.set_vectors([(matching.id, (1.0, 0.0))], "model-a", None)
    await harness.store.set_vectors([(stale.id, (1.0, 0.0, 0.0))], "model-a", None)
    filter_ = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)

    hits = await harness.store.search_vectors((1.0, 0.0), "model-a", filter_, 10)
    near = await harness.store.nearest_in_scope((1.0, 0.0), "model-a", HIVE_SCOPE, 10)

    assert [candidate.honey.id for candidate in hits] == [matching.id]
    assert [candidate.honey.id for candidate in near] == [matching.id]


# ──────────────────────────────────────────────────────────────────────────────
# Filtering happens before limiting, on every axis (ADR-0031)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("axis", ["scope", "requested_scope", "clearance", "tainted", "retired"])
async def test_list_honey_filters_every_axis_before_limiting(harness: Harness, axis: str) -> None:
    forbidden_scope = f"cell:{new_cell_id(harness.clock)}"
    permitted = await ripen(
        harness, scope=HIVE_SCOPE, clearance=HoneyClearance.C1, body="permitted"
    )
    harness.clock.advance(1)

    if axis == "scope":
        filter_ = ReadFilter(readable=(HIVE_SCOPE,), max_clearance=HoneyClearance.C2)
    elif axis == "requested_scope":
        filter_ = ReadFilter(
            readable=("*",), max_clearance=HoneyClearance.C2, requested=(HIVE_SCOPE,)
        )
    else:
        filter_ = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C1)

    for _ in range(_FORBIDDEN_ROW_COUNT):
        if axis in ("scope", "requested_scope"):
            await ripen(
                harness, scope=forbidden_scope, clearance=HoneyClearance.C1, body="forbidden"
            )
        elif axis == "clearance":
            await ripen(harness, scope=HIVE_SCOPE, clearance=HoneyClearance.C2, body="forbidden")
        else:
            row = await ripen(
                harness, scope=HIVE_SCOPE, clearance=HoneyClearance.C1, body="forbidden"
            )
            if axis == "tainted":
                harness.connection.execute("UPDATE honey SET tainted = 1 WHERE id = ?", (row.id,))
            else:
                await harness.store.retire(row.id, event(harness.clock, "honey.retired", row.id))
        harness.clock.advance(1)

    results = await harness.store.list_honey(filter_, scope_prefix=None, limit=1, offset=0)

    assert [row.id for row in results] == [permitted.id]


async def test_count_withheld_counts_top_matches_the_filter_excludes(harness: Harness) -> None:
    await ripen(harness, scope=HIVE_SCOPE, clearance=HoneyClearance.C1, body="widget appears once")
    forbidden_scope = f"cell:{new_cell_id(harness.clock)}"
    for _ in range(3):
        await ripen(
            harness, scope=forbidden_scope, clearance=HoneyClearance.C1, body="widget widget widget"
        )
    match = build_match("widget")
    assert match is not None
    filter_ = ReadFilter(readable=(HIVE_SCOPE,), max_clearance=HoneyClearance.C2)

    withheld = await harness.store.count_withheld(match, filter_, 10)

    assert withheld == 3


async def test_scope_counts_respects_ceiling_readable_globs_taint_and_retirement(
    harness: Harness,
) -> None:
    visible, hidden, over = (task_scope(new_task_id(harness.clock)) for _ in range(3))
    await ripen(harness, scope=visible, clearance=HoneyClearance.C1)
    await ripen(harness, scope=hidden, clearance=HoneyClearance.C1)  # Not in readable at all.
    await ripen(harness, scope=over, clearance=HoneyClearance.C2)  # Readable, above the ceiling.
    tainted = await ripen(harness, scope=task_scope(new_task_id(harness.clock)))
    harness.connection.execute("UPDATE honey SET tainted = 1 WHERE id = ?", (tainted.id,))
    retired = await ripen(harness, scope=task_scope(new_task_id(harness.clock)))
    await harness.store.retire(retired.id, event(harness.clock, "honey.retired", retired.id))
    filter_ = ReadFilter(
        readable=(visible, over, tainted.scope, retired.scope), max_clearance=HoneyClearance.C1
    )

    counts = await harness.store.scope_counts("task", filter_)

    assert counts == {visible: 1}


# ──────────────────────────────────────────────────────────────────────────────
# Vectors: pending after an embedder change; ephemeral purge; atomicity; stats
# ──────────────────────────────────────────────────────────────────────────────


async def test_pending_vectors_shows_a_row_pending_again_after_an_embedder_change(
    harness: Harness,
) -> None:
    honey = await ripen(harness)
    await harness.store.set_vectors([(honey.id, (1.0, 0.0))], "old-model", None)

    still_pending_for_old = await harness.store.pending_vectors("old-model", 10)
    pending_for_new = await harness.store.pending_vectors("new-model", 10)

    assert honey.id not in {row.id for row in still_pending_for_old}
    assert honey.id in {row.id for row in pending_for_new}


async def test_prune_vectors_refuses_until_covered_then_drops_every_other_model(
    harness: Harness,
) -> None:
    covered = await ripen(harness, body="covered")
    uncovered = await ripen(harness, body="not covered yet")
    await harness.store.set_vectors([(covered.id, (1.0, 0.0))], "kept", None)
    await harness.store.set_vectors([(covered.id, (0.0, 1.0))], "superseded", None)

    refusal = await harness.store.prune_vectors("kept", prune_events(harness.clock, covered.id))

    assert (refusal.missing, refusal.dropped) == (1, {})
    stats = await harness.store.stats()
    assert stats.vectors_by_model == {"kept": 1, "superseded": 1}  # Nothing was deleted.
    assert await harness.trail.query(TrailQuery(kind="honey.vectors_pruned")) == ()

    await harness.store.set_vectors([(uncovered.id, (1.0, 0.0))], "kept", None)
    success = await harness.store.prune_vectors("kept", prune_events(harness.clock, covered.id))

    assert (success.missing, success.dropped) == (0, {"superseded": 1})
    stats = await harness.store.stats()
    assert stats.vectors_by_model == {"kept": 2}  # Both rows' kept vectors survive untouched.
    assert len(await harness.trail.query(TrailQuery(kind="honey.vectors_pruned"))) == 1


async def test_purge_ephemeral_deletes_only_that_cells_ephemeral_rows(harness: Harness) -> None:
    night_veil_cell = new_cell_id(harness.clock)
    draft = make_nectar_draft(
        clock=harness.clock,
        cell_id=night_veil_cell,
        ephemeral_cell_id=night_veil_cell,
        origin_tier=CombShieldLevel.NIGHT_VEIL,
        task_id=None,
        bee=None,
    )
    added = await harness.store.add_nectar(draft, sha(draft), nectar_events(harness.clock))
    assert added.nectar.state is NectarState.EPHEMERAL
    other_honey = await ripen(harness)  # A different Cell's ordinary row; must survive the purge.

    purged = await harness.store.purge_ephemeral(night_veil_cell)

    assert purged == 1
    with pytest.raises(NectarNotFoundError):
        await harness.store.get_nectar(added.nectar.id)
    assert (await harness.store.get_honey(other_honey.id)).id == other_honey.id


async def test_ripen_refuses_an_ephemeral_nectar_and_leaves_it_purgeable(harness: Harness) -> None:
    night_veil_cell = new_cell_id(harness.clock)
    draft = make_nectar_draft(
        clock=harness.clock,
        cell_id=night_veil_cell,
        ephemeral_cell_id=night_veil_cell,
        origin_tier=CombShieldLevel.NIGHT_VEIL,
        task_id=None,
        bee=None,
    )
    added = await harness.store.add_nectar(draft, sha(draft), lambda _added: ())

    with pytest.raises(NectarNotRipenableError):
        await harness.store.ripen(
            added.nectar.id,
            (make_honey_draft(),),
            event(harness.clock, "honey.ripened", added.nectar.id),
        )

    assert await harness.store.honey_for_nectar(added.nectar.id) == ()
    assert await harness.store.purge_ephemeral(night_veil_cell) == 1


async def test_a_failing_write_leaves_no_event_on_the_trail(harness: Harness) -> None:
    bogus_id = new_nectar_id(harness.clock)  # Well-formed, never stored.
    failed_event = event(harness.clock, "honey.ripen_failed", bogus_id)

    with pytest.raises(NectarNotFoundError):
        await harness.store.mark_nectar_failed(bogus_id, discard=False, event=failed_event)

    assert await harness.trail.query(TrailQuery(subject_id=bogus_id)) == ()


async def test_stats_reports_live_counts_and_the_active_vector_backend(harness: Harness) -> None:
    await ripen(harness)

    stats = await harness.store.stats()

    assert stats.honey_by_part.get(HoneyPart.SUMMARY, 0) >= 1
    assert stats.vector_backend in ("sqlite_vec", "python")


async def test_watermark_and_proposal_round_trip(harness: Harness) -> None:
    assert await harness.store.get_watermark("cursor") is None
    await harness.store.set_watermark("cursor", "42")
    assert await harness.store.get_watermark("cursor") == "42"

    proposal_id = await harness.store.add_proposal(
        HIVE_SCOPE,
        "A note",
        "note text",
        event(harness.clock, "honey.note_proposed", new_nectar_id(harness.clock)),
    )
    pending = await harness.store.pending_proposals(10)
    assert proposal_id in {proposal.id for proposal in pending}

    honey = await ripen(harness)
    await harness.store.mark_proposal_drained(proposal_id, honey.nectar_id)
    still_pending = await harness.store.pending_proposals(10)
    assert proposal_id not in {proposal.id for proposal in still_pending}
