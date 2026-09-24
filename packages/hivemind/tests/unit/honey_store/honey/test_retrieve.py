"""Tests for hivemind.honey_store.honey.retrieve: HoneyRetriever over a real SQLite Honey Store.

Every test ripens real rows into a real `SqliteHoneyStore` (a temp file, the Pheromone Trail's
migrations applied first) and embeds them with `FakeEmbedding`, whose vectors are lexical rather
than semantic (ADR-0032): word tokens and character trigrams. So the "paraphrase" the hybrid test
uses is a lexical one -- misspelled words the FTS5 porter stemmer cannot match but whose trigrams
the fake embedder still places near the right row.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/honey/retrieve.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.honey.retrieve for the module under test.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the rules exercised here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from builders.honey import make_honey_draft, make_nectar_draft

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.forage.slots import ModelSlot
from hivemind.guard import CapabilitySet
from hivemind.honey_store.honey.retrieve import (
    EMPTY_QUERY_REASON,
    NO_READABLE_SCOPE_REASON,
    QUERIED_KIND,
    HoneyReader,
    HoneyRetriever,
    HoneySearch,
    RetrieverDeps,
)
from hivemind.honey_store.identity import HoneyIdentity, honey_event
from hivemind.honey_store.models import Honey, HoneyPart
from hivemind.honey_store.scope import queen_read_capabilities, task_scope, worker_read_capabilities
from hivemind.honey_store.store import NectarAdded
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.llm import (
    BoundEmbedder,
    DirectEmbedGate,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbedGate,
    FakeEmbedding,
)
from hivemind.llm.models import Usage
from hivemind.manifest.schema.honey import HoneyRetrievalSection
from hivemind.pheromone import HoneyEvent, SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import (
    new_cell_id,
    new_hive_id,
    new_nectar_id,
    new_node_id,
    new_task_id,
    new_worker_id,
)

_MODEL = "test-embed"  # A neutral embedding model id (codingrules 8.6).
_TARGET_BODY = (
    "The staging configuration for the widget factory lives at /etc/widgets/staging.toml."
)


@dataclass(frozen=True, slots=True)
class _Hive:
    """One test's store, trail, identity, clock and fake embedder."""

    store: SqliteHoneyStore
    trail: SqlitePheromoneTrail
    identity: HoneyIdentity
    clock: FakeClock
    embedding: FakeEmbedding

    def retriever(
        self, *, embedded: bool = True, gate: EmbedGate | None = None, **retrieval: object
    ) -> HoneyRetriever:
        """Build a retriever: the fake embedder behind `gate` (direct by default), or none."""
        bound = BoundEmbedder(ModelSlot.EMBEDDER, "embedder", self.embedding, _MODEL, None)
        return HoneyRetriever(
            RetrieverDeps(
                store=self.store,
                identity=self.identity,
                clock=self.clock,
                retrieval=HoneyRetrievalSection(**retrieval),
                embedder=bound if embedded else None,
                embed_gate=(gate or DirectEmbedGate()) if embedded else None,
            )
        )


@pytest.fixture
async def hive(tmp_path: Path) -> _Hive:
    """Open a fresh temp-file Honey Store with a readable trail beside it."""
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")
    trail = await SqlitePheromoneTrail.create(connection, clock)
    store = await SqliteHoneyStore.create(connection, clock)
    identity = HoneyIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system")
    return _Hive(store, trail, identity, clock, FakeEmbedding(clock=clock, model=_MODEL))


async def _ripen(
    hive_: _Hive,
    title: str,
    body: str,
    *,
    chunks: tuple[str, ...] = (),
    **nectar: object,
) -> tuple[Honey, ...]:
    """Deposit one Nectar, ripen it into a SUMMARY row plus one CHUNK row per chunk, embed all."""
    clock, identity = hive_.clock, hive_.identity
    draft = make_nectar_draft(
        clock=clock, content=f"{body} {new_nectar_id(clock)}".encode(), **nectar
    )

    def events(added: NectarAdded) -> tuple[HoneyEvent, ...]:
        return (honey_event(identity, clock, "honey.nectar_received", added.nectar.id),)

    sha = hashlib.sha256(draft.content).hexdigest()
    added = await hive_.store.add_nectar(draft, sha, events)
    parts = [make_honey_draft(title=title, summary=body, body=body, clearance=draft.clearance)]
    parts += [
        make_honey_draft(
            part=HoneyPart.CHUNK, chunk_index=index, title=title, summary=body, body=chunk
        )
        for index, chunk in enumerate(chunks, start=1)
    ]
    event = honey_event(identity, clock, "honey.ripened", added.nectar.id)
    rows = await hive_.store.ripen(added.nectar.id, parts, event)
    texts = tuple(f"{row.title} {row.body}" for row in rows)
    embedded = await hive_.embedding.embed(EmbeddingRequest(texts=texts))
    await hive_.store.set_vectors(
        list(zip((row.id for row in rows), embedded.vectors, strict=True)), _MODEL, None
    )
    return rows


async def _ripen_filler(hive_: _Hive, count: int = 3) -> None:
    """Ripen unrelated rows, so a query's words are rare enough for bm25 to weigh them.

    FTS5's bm25 gives a term found in half the rows or more a near-zero weight (an IDF floor of
    1e-6); `rank.text_scores`' relative floor keeps such a store searchable (see the young-store
    test below), but a test of how bm25 itself ranks wants rows where its weights mean something.
    """
    for index in range(count):
        await _ripen(
            hive_, f"Lunch menu {index}", f"Soup, bread and apples are served on day {index}."
        )


def _reader(
    hive_: _Hive,
    capabilities: CapabilitySet | None = None,
    ceiling: HoneyClearance = HoneyClearance.C2,
    is_night_veil: bool = False,
) -> HoneyReader:
    """Build a reader: Queen-like (reads every scope, up to C2) unless overridden."""
    return HoneyReader(
        requester=new_worker_id(hive_.clock),
        capabilities=capabilities if capabilities is not None else queen_read_capabilities(),
        ceiling=ceiling,
        is_night_veil=is_night_veil,
    )


def _search(
    hive_: _Hive,
    text: str,
    reader: HoneyReader | None = None,
    *,
    max_hits: int = 10,
    max_tokens: int = 4_000,
) -> HoneySearch:
    """Build a search over every readable scope, by `reader` (a Queen-like one by default)."""
    return HoneySearch(
        text=text,
        reader=reader if reader is not None else _reader(hive_),
        requested_scopes=(),
        max_hits=max_hits,
        max_tokens=max_tokens,
    )


async def _queried_events(hive_: _Hive) -> list[HoneyEvent]:
    """Return every honey.queried event on the trail."""
    events = await hive_.trail.query(TrailQuery(kind=QUERIED_KIND, limit=100))
    return [event for event in events if isinstance(event, HoneyEvent)]


class _HangingGate:
    """An EmbedGate whose call never returns, so only the retriever's own timeout can end it."""

    async def embed(self, bound: BoundEmbedder, request: EmbeddingRequest) -> EmbeddingResponse:
        await asyncio.Event().wait()
        raise AssertionError("unreachable: the event above is never set")


class _ZeroGate:
    """An EmbedGate that answers every text with an all-zero vector (no direction at all)."""

    async def embed(self, bound: BoundEmbedder, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=tuple((0.0, 0.0, 0.0) for _ in request.texts),
            model=bound.model,
            dimensions=3,
            usage=Usage(input_tokens=1, output_tokens=0, cost_usd=None),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Hybrid ranking
# ──────────────────────────────────────────────────────────────────────────────


async def test_search_hybrid_finds_a_misspelled_paraphrase_that_text_only_misses(
    hive: _Hive,
) -> None:
    target = (await _ripen(hive, "Staging configuration", _TARGET_BODY))[0]
    await _ripen(hive, "Office plants", "Water the office plants every Monday morning.")
    query = "stagign configuraton"  # Typos: no porter stem matches, many trigrams still do.

    text_only = await hive.retriever(embedded=False).search(_search(hive, query))
    hybrid = await hive.retriever().search(_search(hive, query))

    assert text_only.hits == ()
    assert text_only.reason.startswith("Full-text search only (no embedder is bound)")
    assert hybrid.hits[0].honey_ref == target.path
    assert hybrid.reason.startswith("Hybrid search")


async def test_search_ranks_by_the_fused_score_and_shows_the_summary_as_the_excerpt(
    hive: _Hive,
) -> None:
    target = (await _ripen(hive, "Staging configuration", _TARGET_BODY))[0]
    await _ripen(hive, "Widget colours", "The widget factory paints widgets blue on Tuesdays.")

    response = await hive.retriever().search(_search(hive, "staging configuration widget"))

    assert response.hits[0].honey_ref == target.path
    assert response.hits[0].excerpt == target.summary
    assert response.hits[0].score >= response.hits[-1].score
    assert 0 < response.token_count <= 4_000


# ──────────────────────────────────────────────────────────────────────────────
# Policy: scope, clearance, fail closed
# ──────────────────────────────────────────────────────────────────────────────


async def test_search_withholds_and_counts_a_forbidden_scope_and_a_forbidden_clearance(
    hive: _Hive,
) -> None:
    clock = hive.clock
    task, cell, bee = new_task_id(clock), new_cell_id(clock), new_worker_id(clock)
    visible = (await _ripen(hive, "Widget note", "The widget factory opens at nine."))[0]
    await _ripen(
        hive,
        "Widget secret",
        "The widget factory key is kept offsite.",
        clearance=HoneyClearance.C2,
    )
    other_task = task_scope(new_task_id(clock))
    await _ripen(
        hive, "Widget task", "The widget factory task notes.", scope=other_task, task_id=None
    )
    caps = worker_read_capabilities(task, task, cell, bee)

    response = await hive.retriever().search(
        _search(hive, "widget factory", _reader(hive, caps, HoneyClearance.C1))
    )

    assert [hit.honey_ref for hit in response.hits] == [visible.path]
    assert response.filtered_count == 2


async def test_search_narrows_to_the_requested_scopes(hive: _Hive) -> None:
    cell_scope_ = f"cell:{new_cell_id(hive.clock)}"
    in_cell = (
        await _ripen(hive, "Widget cell", "The widget factory Cell runs hot.", scope=cell_scope_)
    )[0]
    await _ripen(hive, "Widget hive", "The widget factory is shared knowledge.")

    response = await hive.retriever().search(
        replace(_search(hive, "widget factory"), requested_scopes=(cell_scope_,))
    )

    assert [hit.honey_ref for hit in response.hits] == [in_cell.path]


async def test_search_fails_closed_for_a_reader_with_no_readable_scope(hive: _Hive) -> None:
    await _ripen(hive, "Widget note", "The widget factory opens at nine.")

    embed_calls = hive.embedding.calls

    response = await hive.retriever().search(
        _search(hive, "widget", _reader(hive, CapabilitySet.empty()))
    )

    assert response.hits == ()
    assert response.reason == NO_READABLE_SCOPE_REASON
    assert response.filtered_count == 1  # The match exists; policy withheld it.
    assert hive.embedding.calls == embed_calls  # Nothing could be found: no query embedding.


async def test_search_returns_an_empty_response_for_a_punctuation_only_query(hive: _Hive) -> None:
    await _ripen(hive, "Widget note", "The widget factory opens at nine.")

    response = await hive.retriever().search(_search(hive, "?! ... --"))

    assert response.hits == ()
    assert response.reason == EMPTY_QUERY_REASON
    assert response.filtered_count == 0


# ──────────────────────────────────────────────────────────────────────────────
# Degrading to full text
# ──────────────────────────────────────────────────────────────────────────────


async def test_search_falls_back_to_text_with_the_reason_when_the_embedder_is_down(
    hive: _Hive,
) -> None:
    row = (await _ripen(hive, "Widget note", "The widget factory opens at nine."))[0]
    await _ripen_filler(hive)
    hive.embedding.set_available(False)

    response = await hive.retriever().search(_search(hive, "widget factory"))

    assert [hit.honey_ref for hit in response.hits] == [row.path]
    assert "Full-text search only (the embedder failed (hivemind.llm.provider_unavailable))" in (
        response.reason
    )
    events = await _queried_events(hive)
    assert events[0].payload["vector_used"] is False


async def test_search_falls_back_to_text_when_the_embedder_times_out(hive: _Hive) -> None:
    row = (await _ripen(hive, "Widget note", "The widget factory opens at nine."))[0]
    await _ripen_filler(hive)
    retriever = hive.retriever(gate=_HangingGate(), embed_timeout_s=0.01)

    response = await retriever.search(_search(hive, "widget factory"))

    assert [hit.honey_ref for hit in response.hits] == [row.path]
    assert "the embedder did not answer within 0.01s" in response.reason


async def test_search_falls_back_to_text_on_a_zero_query_vector(hive: _Hive) -> None:
    row = (await _ripen(hive, "Widget note", "The widget factory opens at nine."))[0]
    await _ripen_filler(hive)
    retriever = hive.retriever(gate=_ZeroGate())

    response = await retriever.search(_search(hive, "widget factory"))

    assert [hit.honey_ref for hit in response.hits] == [row.path]
    assert "the query embedded to a zero vector" in response.reason


# ──────────────────────────────────────────────────────────────────────────────
# Budget, max hits and the per-Nectar cap
# ──────────────────────────────────────────────────────────────────────────────


async def test_search_truncates_to_the_token_budget(hive: _Hive) -> None:
    for index in range(4):
        await _ripen(hive, f"Widget report {index}", f"Widget factory report {index}. " * 40)

    response = await hive.retriever().search(_search(hive, "widget factory report", max_tokens=400))

    assert 0 < len(response.hits) < 4
    assert response.token_count <= 400
    assert response.is_truncated is True
    assert "cut the ranked result" in response.reason


async def test_search_caps_max_hits_and_says_it_was_truncated(hive: _Hive) -> None:
    for index in range(3):
        await _ripen(hive, f"Widget note {index}", f"The widget factory note number {index}.")

    response = await hive.retriever().search(_search(hive, "widget factory note", max_hits=2))

    assert len(response.hits) == 2
    assert response.is_truncated is True


async def test_search_takes_at_most_max_hits_per_nectar_from_one_deposit(hive: _Hive) -> None:
    chunks = tuple(f"Widget factory chunk {index} about widget factory work." for index in range(3))
    rows = await _ripen(hive, "Widget factory log", "Widget factory log summary.", chunks=chunks)

    response = await hive.retriever(max_hits_per_nectar=2).search(_search(hive, "widget factory"))

    assert len(response.hits) == 2
    assert {hit.honey_ref for hit in response.hits} <= {row.path for row in rows}


async def test_search_outcome_says_whether_vectors_took_part(hive: _Hive) -> None:
    await _ripen_filler(hive)
    await _ripen(hive, "Widget note", "The widget factory opens at nine.")

    hybrid = await hive.retriever().search_outcome(_search(hive, "widget factory"))
    hive.embedding.set_available(False)
    text_only = await hive.retriever().search_outcome(_search(hive, "widget factory"))

    assert hybrid.vector_used is True
    assert text_only.vector_used is False
    assert text_only.response.reason.startswith("Full-text search only")


# ──────────────────────────────────────────────────────────────────────────────
# The trail
# ──────────────────────────────────────────────────────────────────────────────


async def test_search_records_honey_queried_with_counts_and_never_the_words(hive: _Hive) -> None:
    await _ripen(hive, "Widget note", "The widget factory opens at nine.")
    search = _search(hive, "widget factory opening hours")

    response = await hive.retriever().search(search)

    [event] = await _queried_events(hive)
    assert event.subject_id == search.reader.requester
    assert event.payload == {
        "hits": len(response.hits),
        "withheld": 0,
        "tokens": response.token_count,
        "vector_used": True,
        "truncated": False,
    }
    assert "opening" not in json.dumps(event.model_dump(mode="json"))


async def test_search_records_no_event_for_a_night_veil_reader(hive: _Hive) -> None:
    await _ripen(hive, "Widget note", "The widget factory opens at nine.")

    response = await hive.retriever().search(
        _search(hive, "widget factory", _reader(hive, is_night_veil=True))
    )

    assert len(response.hits) == 1  # Answered in full...
    assert await _queried_events(hive) == []  # ...and nothing about it outlives the Cell.


async def test_a_young_store_finds_its_only_deposit_by_full_text_alone(hive: _Hive) -> None:
    # One Nectar ripened into a SUMMARY and a CHUNK row sharing its words: every word is in half
    # the rows or more, so bm25 weighs each at about 1e-6. Without the relative floor this query
    # scored about 3e-6, under min_score, and a Hive with no embedder found nothing at all.
    (summary, chunk) = await _ripen(hive, "Staging config", _TARGET_BODY, chunks=(_TARGET_BODY,))

    response = await hive.retriever(embedded=False).search(
        _search(hive, "where does the widget staging configuration live?")
    )

    assert {hit.honey_ref for hit in response.hits} == {summary.path, chunk.path}
    assert "Full-text search only" in response.reason
