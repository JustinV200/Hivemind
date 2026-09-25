"""Tests for hivemind.honey_store.ripening.index: storing one ripened Nectar's rows and vectors.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/index.py (codingrules section 3), over a real
    SQLite store and trail (`builders.honey`).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.ripening.index for the module under test.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.honey import (
    make_honey_draft,
    make_nectar_draft,
    make_ripener_deps,
    open_test_honey_store_with_trail,
)

from hivemind.cell import HoneyClearance
from hivemind.honey_store.identity import honey_event
from hivemind.honey_store.models import HoneyPart, Nectar, NectarState
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.honey_store.ripening.drafts import PreparedPart
from hivemind.honey_store.ripening.index import RipenedNectar, index_ripened
from hivemind.pheromone import PheromoneEvent, SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock

_MODEL = "test-embed"
_CONTENT = b"The widget factory's staging config lives at /etc/widgets/staging.toml."


@dataclass(frozen=True, slots=True)
class _Harness:
    """Ripener deps over a real store, its trail, and one RECEIVED C1 Nectar to index."""

    deps: RipenerDeps
    trail: SqlitePheromoneTrail
    nectar: Nectar

    async def events(self, kind: str) -> tuple[PheromoneEvent, ...]:
        """Return every event of `kind` on the trail."""
        return await self.trail.query(TrailQuery(kind=kind))


@pytest.fixture
async def harness(tmp_path: Path) -> _Harness:
    """Store one C1 Nectar (no events) and build default deps around it."""
    clock = FakeClock()
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    draft = make_nectar_draft(clock=clock, content=_CONTENT)
    added = await store.add_nectar(draft, hashlib.sha256(_CONTENT).hexdigest(), lambda _: ())
    return _Harness(deps=make_ripener_deps(store, clock), trail=trail, nectar=added.nectar)


def _ripened(
    harness_: _Harness, parts: tuple[PreparedPart, ...], clearance: HoneyClearance
) -> RipenedNectar:
    """Wrap `parts` for `harness_.nectar` with fixed counts and the test model."""
    return RipenedNectar(
        nectar=harness_.nectar,
        parts=parts,
        clearance=clearance,
        chunks=2,
        deduped=1,
        summarised=True,
        embed_model=_MODEL,
    )


def _parts(clearance: HoneyClearance = HoneyClearance.C1) -> tuple[PreparedPart, ...]:
    """A SUMMARY with a vector, a CHUNK with a vector and a CHUNK with a zero vector."""
    chunk = make_honey_draft(part=HoneyPart.CHUNK, clearance=clearance)
    return (
        PreparedPart(draft=make_honey_draft(clearance=clearance), vector=(1.0, 0.0)),
        PreparedPart(draft=chunk.model_copy(update={"chunk_index": 0}), vector=(0.0, 1.0)),
        PreparedPart(draft=chunk.model_copy(update={"chunk_index": 1}), vector=(0.0, 0.0)),
    )


async def test_index_ripened_writes_rows_vectors_and_the_ripened_event(harness: _Harness) -> None:
    result = await index_ripened(harness.deps, _ripened(harness, _parts(), HoneyClearance.C1))

    assert result.is_stored
    assert [row.part for row in result.rows] == [
        HoneyPart.SUMMARY,
        HoneyPart.CHUNK,
        HoneyPart.CHUNK,
    ]
    assert result.embedded == 2
    pending = await harness.deps.store.pending_vectors(_MODEL, 10)
    assert [row.id for row in pending] == [result.rows[2].id]  # The zero vector's row waits.
    stored = await harness.deps.store.get_nectar(harness.nectar.id)
    assert stored.state is NectarState.RIPENED
    (event,) = await harness.events("honey.ripened")
    assert event.subject_id == harness.nectar.id
    assert event.payload == {
        "rows": 3,
        "chunks": 2,
        "deduped": 1,
        "summarised": True,
        "embedded": 2,
    }
    assert await harness.events("honey.label_raised") == ()


async def test_index_ripened_records_a_raise_the_summary_made(harness: _Harness) -> None:
    parts = _parts(HoneyClearance.C2)

    result = await index_ripened(harness.deps, _ripened(harness, parts, HoneyClearance.C2))

    assert {row.clearance for row in result.rows} == {HoneyClearance.C2}
    (event,) = await harness.events("honey.label_raised")
    assert event.subject_id == harness.nectar.id
    assert event.payload == {"from": "C1", "to": "C2"}


async def test_index_ripened_never_stores_below_a_label_raised_mid_pass(
    harness: _Harness,
) -> None:
    # Intake dedupes a more sensitive copy onto the Nectar while the pass is still summarising.
    raised = make_nectar_draft(clearance=HoneyClearance.C2, content=_CONTENT)
    digest = hashlib.sha256(_CONTENT).hexdigest()
    await harness.deps.store.add_nectar(raised, digest, lambda _: ())

    result = await index_ripened(harness.deps, _ripened(harness, _parts(), HoneyClearance.C1))

    assert {row.clearance for row in result.rows} == {HoneyClearance.C2}
    # The raise was intake's, not the summary's: the ripener records none of its own.
    assert await harness.events("honey.label_raised") == ()


async def test_index_ripened_leaves_a_nectar_another_runner_already_ripened(
    harness: _Harness,
) -> None:
    store = harness.deps.store
    event = honey_event(
        harness.deps.identity, harness.deps.clock, "honey.ripened", harness.nectar.id
    )
    await store.ripen(harness.nectar.id, (make_honey_draft(),), event)

    result = await index_ripened(harness.deps, _ripened(harness, _parts(), HoneyClearance.C1))

    assert not result.is_stored and result.rows == ()
    assert len(await store.honey_for_nectar(harness.nectar.id)) == 1
    assert len(await harness.events("honey.ripened")) == 1


async def test_index_ripened_stores_no_vector_without_an_embedding_model(
    harness: _Harness,
) -> None:
    parts = tuple(PreparedPart(draft=part.draft, vector=None) for part in _parts())
    ripened = dataclasses.replace(_ripened(harness, parts, HoneyClearance.C1), embed_model=None)

    result = await index_ripened(harness.deps, ripened)

    assert result.embedded == 0
    assert len(await harness.deps.store.pending_vectors(_MODEL, 10)) == 3
    (event,) = await harness.events("honey.ripened")
    assert event.payload["embedded"] == 0
