"""Contract suite for HoneyStore: one contract, run over three SqliteHoneyStore harnesses.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of
    hivemind.honey_store.store.protocol.HoneyStore's contract and runs against three harnesses of
    the one shipped implementation (`hivemind.honey_store.store.sqlite.SqliteHoneyStore`): a temp
    file with sqlite-vec, a temp file with the Python vector fallback forced on, and `:memory:`
    with sqlite-vec -- so both storage modes and both vector backends are exercised (ADR-0031). A
    future in-memory fake joins the same fixture's params and must pass here before it is used
    anywhere else (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.store.protocol for the HoneyStore contract under test.
    - packages/hivemind/tests/contracts/test_memory_store_contract.py for the pattern this mirrors.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.honey import make_honey_draft, make_nectar_draft

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.honey_store.errors import NectarNotFoundError, NectarNotRipenableError
from hivemind.honey_store.models import Honey, HoneyPart, NectarDraft, NectarState, ReadFilter
from hivemind.honey_store.scope import HIVE_SCOPE
from hivemind.honey_store.store import NectarAdded, NectarEvents, build_match
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.pheromone import HoneyEvent, PheromoneTrail, SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_hive_id, new_nectar_id, new_node_id

_HARNESS_KINDS = ("tempfile_sqlite_vec", "tempfile_python", "memory_sqlite_vec")
_FORBIDDEN_ROW_COUNT = 20  # Enough that an accidental "filter after limit" bug cannot pass by luck.


@dataclass(frozen=True, slots=True)
class _Harness:
    """One SqliteHoneyStore harness: its store, trail, raw connection and shared clock."""

    store: SqliteHoneyStore
    trail: PheromoneTrail
    connection: sqlite3.Connection
    clock: FakeClock


@pytest.fixture(params=_HARNESS_KINDS)
async def harness(request: pytest.FixtureRequest, tmp_path: Path) -> _Harness:
    """Build the parametrised harness: a fresh honey store over an already-trailed connection."""
    clock = FakeClock()
    is_memory = request.param == "memory_sqlite_vec"
    connection = connect(":memory:" if is_memory else tmp_path / "hive.sqlite3")
    trail = await SqlitePheromoneTrail.create(connection, clock)
    force_python = request.param == "tempfile_python"
    store = await SqliteHoneyStore.create(connection, clock, force_python_vectors=force_python)
    return _Harness(store=store, trail=trail, connection=connection, clock=clock)


def _event(clock: FakeClock, kind: str, subject_id: str) -> HoneyEvent:
    """Build a well-formed HoneyEvent whose subject is `subject_id`, minting a fresh id/node."""
    return HoneyEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload={},
    )


def _nectar_events(clock: FakeClock) -> NectarEvents:
    """Build `add_nectar`'s events from its outcome: received for a new row, else deduplicated."""

    def build(added: NectarAdded) -> tuple[HoneyEvent, ...]:
        kind = "honey.nectar_received" if added.is_new else "honey.nectar_deduplicated"
        return (_event(clock, kind, added.nectar.id),)

    return build


def _sha(draft: NectarDraft) -> str:
    """Digest a NectarDraft's own content, the value `add_nectar`'s caller always supplies."""
    return hashlib.sha256(draft.content).hexdigest()


async def _ripen(
    harness_: _Harness,
    *,
    scope: str = HIVE_SCOPE,
    clearance: HoneyClearance = HoneyClearance.C1,
    body: str = "a widget finding",
) -> Honey:
    """Deposit one fresh Nectar and ripen it into a single SUMMARY Honey row; return that row.

    `add_nectar` dedupes by content sha256, and several tests deliberately ripen many rows that
    share the same `body` text (e.g. 20 "forbidden" rows); a fresh id salts the content each call
    so those still land as genuinely distinct Nectar rows instead of deduping onto one another.
    """
    salt = new_nectar_id(harness_.clock)
    draft = make_nectar_draft(
        clock=harness_.clock,
        scope=scope,
        clearance=clearance,
        task_id=None,
        bee=None,
        content=f"{body} ({salt})".encode(),
    )
    added = await harness_.store.add_nectar(draft, _sha(draft), _nectar_events(harness_.clock))
    rows = await harness_.store.ripen(
        added.nectar.id,
        (make_honey_draft(body=body, summary=body, clearance=clearance),),
        _event(harness_.clock, "honey.ripened", added.nectar.id),
    )
    return rows[0]


# ──────────────────────────────────────────────────────────────────────────────
# Nectar intake: dedupe, raise-only label merge
# ──────────────────────────────────────────────────────────────────────────────


async def test_add_nectar_dedupes_by_source_key_and_raises_stored_and_honey_labels(
    harness: _Harness,
) -> None:
    draft = make_nectar_draft(
        clock=harness.clock,
        source_key="handoff:evt1",
        clearance=HoneyClearance.C0,
        task_id=None,
        bee=None,
    )
    first = await harness.store.add_nectar(draft, _sha(draft), _nectar_events(harness.clock))
    honey_rows = await harness.store.ripen(
        first.nectar.id,
        (make_honey_draft(clearance=HoneyClearance.C0),),
        _event(harness.clock, "honey.ripened", first.nectar.id),
    )

    higher = draft.model_copy(update={"clearance": HoneyClearance.C2})
    second = await harness.store.add_nectar(higher, _sha(draft), _nectar_events(harness.clock))

    assert second.is_new is False
    assert second.raised_from == HoneyClearance.C0
    assert second.nectar.clearance == HoneyClearance.C2
    raised_honey = await harness.store.get_honey(honey_rows[0].id)
    assert raised_honey.clearance == HoneyClearance.C2  # Raised alongside the Nectar, same call.


async def test_add_nectar_dedupes_by_sha256_when_no_source_key(harness: _Harness) -> None:
    draft = make_nectar_draft(clock=harness.clock, task_id=None, bee=None)
    first = await harness.store.add_nectar(draft, _sha(draft), _nectar_events(harness.clock))

    same_content = make_nectar_draft(
        clock=harness.clock,
        content=draft.content,
        clearance=draft.clearance,
        task_id=None,
        bee=None,
    )
    second = await harness.store.add_nectar(
        same_content, _sha(draft), _nectar_events(harness.clock)
    )

    assert second.is_new is False
    assert second.nectar.id == first.nectar.id


async def test_add_nectar_duplicate_with_a_lower_label_never_lowers_the_stored_one(
    harness: _Harness,
) -> None:
    draft = make_nectar_draft(
        clock=harness.clock, clearance=HoneyClearance.C2, task_id=None, bee=None
    )
    await harness.store.add_nectar(draft, _sha(draft), _nectar_events(harness.clock))

    lower = draft.model_copy(update={"clearance": HoneyClearance.C0})
    second = await harness.store.add_nectar(lower, _sha(draft), _nectar_events(harness.clock))

    assert second.raised_from is None
    assert second.nectar.clearance == HoneyClearance.C2


async def test_add_nectar_records_the_events_built_from_its_own_outcome(
    harness: _Harness,
) -> None:
    draft = make_nectar_draft(clock=harness.clock, task_id=None, bee=None)

    first = await harness.store.add_nectar(draft, _sha(draft), _nectar_events(harness.clock))
    harness.clock.advance(
        1
    )  # Two events in one instant would come back in id order, not call order.
    second = await harness.store.add_nectar(draft, _sha(draft), _nectar_events(harness.clock))

    events = await harness.trail.query(TrailQuery(subject_id=first.nectar.id))
    # The builder saw the minted id and whether each call was new, inside the same transaction.
    assert [event.kind for event in events] == [
        "honey.nectar_received",
        "honey.nectar_deduplicated",
    ]
    assert second.nectar.id == first.nectar.id


async def test_add_nectar_records_nothing_when_the_builder_returns_no_event(
    harness: _Harness,
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

    added = await harness.store.add_nectar(draft, _sha(draft), lambda _added: ())

    assert added.nectar.state is NectarState.EPHEMERAL
    assert await harness.trail.query(TrailQuery(subject_id=added.nectar.id)) == ()


async def test_an_ordinary_deposit_never_dedupes_onto_an_ephemeral_row(harness: _Harness) -> None:
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
    await harness.store.add_nectar(ephemeral, _sha(ephemeral), lambda _added: ())

    added = await harness.store.add_nectar(ordinary, _sha(ordinary), _nectar_events(harness.clock))
    await harness.store.purge_ephemeral(night_veil_cell)

    # Merged onto the ephemeral row, the ordinary deposit would have vanished with the purge.
    assert added.is_new is True
    assert (await harness.store.get_nectar(added.nectar.id)).state is NectarState.RECEIVED


async def test_an_ephemeral_deposit_never_dedupes_onto_an_ordinary_row(harness: _Harness) -> None:
    ordinary = make_nectar_draft(
        clock=harness.clock, clearance=HoneyClearance.C0, task_id=None, bee=None
    )
    stored = await harness.store.add_nectar(ordinary, _sha(ordinary), _nectar_events(harness.clock))
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

    added = await harness.store.add_nectar(ephemeral, _sha(ephemeral), lambda _added: ())

    # A Night Veil Cell can never change a persistent row, not even by raising its label.
    assert added.is_new is True
    assert added.nectar.state is NectarState.EPHEMERAL
    assert (await harness.store.get_nectar(stored.nectar.id)).clearance == HoneyClearance.C0


# ──────────────────────────────────────────────────────────────────────────────
# Ripening: idempotent per (nectar_id, part, chunk_index)
# ──────────────────────────────────────────────────────────────────────────────


async def test_ripen_is_idempotent_and_never_duplicates_a_part(harness: _Harness) -> None:
    honey = await _ripen(harness, body="the original body")

    again = await harness.store.ripen(
        honey.nectar_id,
        (make_honey_draft(body="a different body", summary="a different body"),),
        _event(harness.clock, "honey.ripened", honey.nectar_id),
    )

    assert again[0].id == honey.id
    assert again[0].body == "the original body"  # Never overwritten by the second call.


# ──────────────────────────────────────────────────────────────────────────────
# Search: FTS finds a word, vector search orders and isolates by model
# ──────────────────────────────────────────────────────────────────────────────


async def test_search_text_finds_a_word_in_the_body(harness: _Harness) -> None:
    honey = await _ripen(harness, body="The gizmo assembly needs realignment.")
    match = build_match("gizmo")
    assert match is not None
    filter_ = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)

    hits = await harness.store.search_text(match, filter_, 10)

    assert honey.id in {candidate.honey.id for candidate in hits}


async def test_a_relabelled_row_stays_findable_by_text(harness: _Harness) -> None:
    # The FTS update trigger fires only on indexed text; a relabel must not drop the entry.
    honey = await _ripen(harness, body="The sprocket gauge reads high.")
    await harness.store.raise_clearance(
        honey.id, HoneyClearance.C2, _event(harness.clock, "honey.label_raised", honey.id)
    )
    match = build_match("sprocket")
    assert match is not None
    filter_ = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)

    hits = await harness.store.search_text(match, filter_, 10)

    assert [candidate.honey.id for candidate in hits] == [honey.id]


async def test_search_vectors_orders_nearest_first_and_isolates_by_model(harness: _Harness) -> None:
    near = await _ripen(harness, body="near")
    far = await _ripen(harness, body="far")
    other_model = await _ripen(harness, body="other model")
    await harness.store.set_vectors([(near.id, (1.0, 0.0)), (far.id, (0.0, 1.0))], "model-a", None)
    await harness.store.set_vectors([(other_model.id, (1.0, 0.0))], "model-b", None)
    filter_ = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)

    hits = await harness.store.search_vectors((0.9, 0.1), "model-a", filter_, 10)

    assert [candidate.honey.id for candidate in hits] == [near.id, far.id]  # Nearest first.
    assert other_model.id not in {candidate.honey.id for candidate in hits}  # Different model.


async def test_search_vectors_skips_a_stored_vector_of_another_length(harness: _Harness) -> None:
    # Same model id, different output size (a reconfigured server): skipped, never a failed query.
    matching = await _ripen(harness, body="two dims")
    stale = await _ripen(harness, body="three dims")
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
async def test_list_honey_filters_every_axis_before_limiting(harness: _Harness, axis: str) -> None:
    forbidden_scope = f"cell:{new_cell_id(harness.clock)}"
    permitted = await _ripen(
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
            await _ripen(
                harness, scope=forbidden_scope, clearance=HoneyClearance.C1, body="forbidden"
            )
        elif axis == "clearance":
            await _ripen(harness, scope=HIVE_SCOPE, clearance=HoneyClearance.C2, body="forbidden")
        else:
            row = await _ripen(
                harness, scope=HIVE_SCOPE, clearance=HoneyClearance.C1, body="forbidden"
            )
            if axis == "tainted":
                harness.connection.execute("UPDATE honey SET tainted = 1 WHERE id = ?", (row.id,))
            else:
                await harness.store.retire(row.id, _event(harness.clock, "honey.retired", row.id))
        harness.clock.advance(1)

    results = await harness.store.list_honey(filter_, scope_prefix=None, limit=1, offset=0)

    assert [row.id for row in results] == [permitted.id]


async def test_count_withheld_counts_top_matches_the_filter_excludes(harness: _Harness) -> None:
    await _ripen(harness, scope=HIVE_SCOPE, clearance=HoneyClearance.C1, body="widget appears once")
    forbidden_scope = f"cell:{new_cell_id(harness.clock)}"
    for _ in range(3):
        await _ripen(
            harness, scope=forbidden_scope, clearance=HoneyClearance.C1, body="widget widget widget"
        )
    match = build_match("widget")
    assert match is not None
    filter_ = ReadFilter(readable=(HIVE_SCOPE,), max_clearance=HoneyClearance.C2)

    withheld = await harness.store.count_withheld(match, filter_, 10)

    assert withheld == 3


# ──────────────────────────────────────────────────────────────────────────────
# Vectors: pending after an embedder change; ephemeral purge; atomicity; stats
# ──────────────────────────────────────────────────────────────────────────────


async def test_pending_vectors_shows_a_row_pending_again_after_an_embedder_change(
    harness: _Harness,
) -> None:
    honey = await _ripen(harness)
    await harness.store.set_vectors([(honey.id, (1.0, 0.0))], "old-model", None)

    still_pending_for_old = await harness.store.pending_vectors("old-model", 10)
    pending_for_new = await harness.store.pending_vectors("new-model", 10)

    assert honey.id not in {row.id for row in still_pending_for_old}
    assert honey.id in {row.id for row in pending_for_new}


async def test_purge_ephemeral_deletes_only_that_cells_ephemeral_rows(harness: _Harness) -> None:
    night_veil_cell = new_cell_id(harness.clock)
    draft = make_nectar_draft(
        clock=harness.clock,
        cell_id=night_veil_cell,
        ephemeral_cell_id=night_veil_cell,
        origin_tier=CombShieldLevel.NIGHT_VEIL,
        task_id=None,
        bee=None,
    )
    added = await harness.store.add_nectar(draft, _sha(draft), _nectar_events(harness.clock))
    assert added.nectar.state is NectarState.EPHEMERAL
    other_honey = await _ripen(harness)  # A different Cell's ordinary row; must survive the purge.

    purged = await harness.store.purge_ephemeral(night_veil_cell)

    assert purged == 1
    with pytest.raises(NectarNotFoundError):
        await harness.store.get_nectar(added.nectar.id)
    assert (await harness.store.get_honey(other_honey.id)).id == other_honey.id


async def test_ripen_refuses_an_ephemeral_nectar_and_leaves_it_purgeable(harness: _Harness) -> None:
    night_veil_cell = new_cell_id(harness.clock)
    draft = make_nectar_draft(
        clock=harness.clock,
        cell_id=night_veil_cell,
        ephemeral_cell_id=night_veil_cell,
        origin_tier=CombShieldLevel.NIGHT_VEIL,
        task_id=None,
        bee=None,
    )
    added = await harness.store.add_nectar(draft, _sha(draft), lambda _added: ())

    with pytest.raises(NectarNotRipenableError):
        await harness.store.ripen(
            added.nectar.id,
            (make_honey_draft(),),
            _event(harness.clock, "honey.ripened", added.nectar.id),
        )

    assert await harness.store.honey_for_nectar(added.nectar.id) == ()
    assert await harness.store.purge_ephemeral(night_veil_cell) == 1


async def test_a_failing_write_leaves_no_event_on_the_trail(harness: _Harness) -> None:
    bogus_id = new_nectar_id(harness.clock)  # Well-formed, never stored.
    event = _event(harness.clock, "honey.ripen_failed", bogus_id)

    with pytest.raises(NectarNotFoundError):
        await harness.store.mark_nectar_failed(bogus_id, discard=False, event=event)

    assert await harness.trail.query(TrailQuery(subject_id=bogus_id)) == ()


async def test_stats_reports_live_counts_and_the_active_vector_backend(harness: _Harness) -> None:
    await _ripen(harness)

    stats = await harness.store.stats()

    assert stats.honey_by_part.get(HoneyPart.SUMMARY, 0) >= 1
    assert stats.vector_backend in ("sqlite_vec", "python")


async def test_watermark_and_proposal_round_trip(harness: _Harness) -> None:
    assert await harness.store.get_watermark("cursor") is None
    await harness.store.set_watermark("cursor", "42")
    assert await harness.store.get_watermark("cursor") == "42"

    proposal_id = await harness.store.add_proposal(
        HIVE_SCOPE,
        "A note",
        "note text",
        _event(harness.clock, "honey.note_proposed", new_nectar_id(harness.clock)),
    )
    pending = await harness.store.pending_proposals(10)
    assert proposal_id in {proposal.id for proposal in pending}

    honey = await _ripen(harness)
    await harness.store.mark_proposal_drained(proposal_id, honey.nectar_id)
    still_pending = await harness.store.pending_proposals(10)
    assert proposal_id not in {proposal.id for proposal in still_pending}
