"""Tests for hivemind.honey_store.ripening.dedupe: exact and near-duplicate CHUNK parts.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/dedupe.py (codingrules section 3). Near duplicates
    are judged against rows in a real SQLite store (`builders.honey`).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.ripening.dedupe for the module under test.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from builders.honey import (
    make_honey_draft,
    make_honey_identity,
    make_nectar_draft,
    open_test_honey_store,
)

from hivemind.cell import HoneyClearance
from hivemind.honey_store.identity import honey_event
from hivemind.honey_store.models import Honey, HoneyDraft, HoneyPart, VectorCandidate
from hivemind.honey_store.ripening.dedupe import (
    NearDuplicateCheck,
    drop_exact_duplicates,
    drop_near_duplicates,
    is_near_duplicate,
    normalised_body,
)
from hivemind.honey_store.ripening.drafts import PreparedPart
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from waggle.clock import FakeClock

_MODEL = "test-embed"
_EAST = (1.0, 0.0, 0.0)  # The stored row's own vector.
_NORTH = (0.0, 1.0, 0.0)  # Orthogonal to it: cosine similarity 0.


def _chunk(body: str, clearance: HoneyClearance = HoneyClearance.C1) -> HoneyDraft:
    """Build a CHUNK draft with `body` at `clearance`."""
    return make_honey_draft(part=HoneyPart.CHUNK, chunk_index=1, body=body, clearance=clearance)


async def _stored_row(
    store: SqliteHoneyStore, clock: FakeClock, clearance: HoneyClearance
) -> Honey:
    """Ripen one hive-scope row at `clearance` and give it the `_EAST` vector."""
    draft = make_nectar_draft(clock=clock, content=f"stored {clearance.value}".encode())
    added = await store.add_nectar(draft, hashlib.sha256(draft.content).hexdigest(), lambda _: ())
    event = honey_event(make_honey_identity(clock), clock, "honey.ripened", added.nectar.id)
    (row,) = await store.ripen(added.nectar.id, (_chunk("stored", clearance),), event)
    await store.set_vectors([(row.id, _EAST)], _MODEL, None)
    return row


# ──────────────────────────────────────────────────────────────────────────────
# Exact duplicates (pure)
# ──────────────────────────────────────────────────────────────────────────────


def test_normalised_body_ignores_whitespace_runs_and_case() -> None:
    assert normalised_body("  The  Staging\n\tCONFIG ") == "the staging config"


def test_drop_exact_duplicates_drops_repeats_blank_chunks_and_copies_of_the_summary() -> None:
    summary = make_honey_draft(body="Summary text.")
    drafts = (
        summary,
        _chunk("first chunk"),
        _chunk("FIRST   chunk"),
        _chunk("  \n "),
        _chunk("summary TEXT."),
        _chunk("second chunk"),
    )

    kept, dropped = drop_exact_duplicates(drafts)

    assert [draft.body for draft in kept] == ["Summary text.", "first chunk", "second chunk"]
    assert dropped == 3


# ──────────────────────────────────────────────────────────────────────────────
# Near duplicates
# ──────────────────────────────────────────────────────────────────────────────


async def test_is_near_duplicate_needs_similarity_and_a_label_no_higher(tmp_path: Path) -> None:
    clock = FakeClock()
    row = await _stored_row(await open_test_honey_store(tmp_path, clock), clock, HoneyClearance.C1)
    close = VectorCandidate(honey=row, distance=0.02)
    far = VectorCandidate(honey=row, distance=0.2)

    assert is_near_duplicate(_chunk("x", HoneyClearance.C1), [close], 0.97)
    assert is_near_duplicate(_chunk("x", HoneyClearance.C2), [far, close], 0.97)
    assert not is_near_duplicate(_chunk("x", HoneyClearance.C1), [far], 0.97)
    assert not is_near_duplicate(_chunk("x", HoneyClearance.C0), [close], 0.97)


async def test_drop_near_duplicates_drops_only_chunks_close_to_a_visible_row(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = await open_test_honey_store(tmp_path, clock)
    await _stored_row(store, clock, HoneyClearance.C1)
    check = NearDuplicateCheck(store=store, model=_MODEL, scope="hive", threshold=0.97)
    parts = (
        PreparedPart(draft=make_honey_draft(body="summary"), vector=_EAST),
        PreparedPart(draft=_chunk("same"), vector=_EAST),
        PreparedPart(draft=_chunk("different"), vector=_NORTH),
        PreparedPart(draft=_chunk("unembedded"), vector=None),
        PreparedPart(draft=_chunk("no direction"), vector=(0.0, 0.0, 0.0)),
        PreparedPart(draft=_chunk("hidden from C0", HoneyClearance.C0), vector=_EAST),
    )

    kept, dropped = await drop_near_duplicates(check, parts)

    assert [part.draft.body for part in kept] == [
        "summary",
        "different",
        "unembedded",
        "no direction",
        "hidden from C0",
    ]
    assert dropped == 1


async def test_drop_near_duplicates_never_compares_across_scopes_or_models(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = await open_test_honey_store(tmp_path, clock)
    await _stored_row(store, clock, HoneyClearance.C1)
    parts = (PreparedPart(draft=_chunk("same"), vector=_EAST),)

    other_scope = NearDuplicateCheck(store=store, model=_MODEL, scope="task:t1", threshold=0.97)
    other_model = NearDuplicateCheck(store=store, model="another", scope="hive", threshold=0.97)

    assert (await drop_near_duplicates(other_scope, parts))[1] == 0
    assert (await drop_near_duplicates(other_model, parts))[1] == 0
