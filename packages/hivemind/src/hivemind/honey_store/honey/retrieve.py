"""Answer a Honey query: hybrid full-text and vector search, filtered, ranked, packed and trailed.

Honey is the Hive's ripened, labelled knowledge (ADR-0031), and a query is one bee's question of
it. `HoneyRetriever.search` answers one: it reduces the words to a safe FTS5 MATCH (`build_match`),
builds the reader's `ReadFilter` from its `honey:read` capabilities (which scopes it may read) and
its clearance ceiling (the most sensitive label it may see), then asks the store for full-text
candidates and, when an embedder is bound, for the nearest vectors to the query's own embedding.
The store applies that filter before it ranks anything, so a forbidden row can never crowd out a
permitted one or slip through a cut-off. Both sides' scores are fused and the best rows chosen
(`hivemind.honey_store.honey.rank`), then packed into the asker's token budget
(`hivemind.honey_store.honey.budget`). Every failure on the vector side -- no embedder, a
provider error, a timeout, a zero query vector -- degrades to full-text search with the cause in
the response's `reason`, never to an error (ADR-0032: "Degrade, never fail closed"). One
`honey.queried` event records the counts, never the words, and nothing at all is recorded for a
reader on a Night Veil Cell (the Hive's most isolated tier, whose records never outlive it).
`search_outcome` runs the very same search and also says whether vectors took part, for a caller
that trails its own consultation of Honey.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    retrieval side. Called by the Queen for a Worker's or Warden's `HoneyQuery`, by her dispatch
    pre-check and planner, and by `hive honey query`; each caller builds the `HoneyReader` from
    what it knows of the asker. Calls into hivemind.honey_store's store, scope, models, identity,
    `honey.rank` and `honey.budget`, and into hivemind.llm (BoundEmbedder, EmbedGate,
    EmbeddingRequest, LLMError), hivemind.pheromone (HoneyEvent), hivemind.guard, hivemind.cell,
    hivemind.manifest and waggle. Its `HoneyResponse` goes back to the asker over Waggle, or
    straight into a `TaskAssign`.

Key invariants:
    - The filter is applied by the store before ranking or limiting (ADR-0031); a reader with no
      `honey:read` capability at all gets an empty response (fail closed), never every row.
    - Vector candidates are searched only for the embedding response's own model, so a query
      never compares vectors from two models (ADR-0032).
    - The query's words reach nothing but the embedding request: no event payload, reason or log
      line carries them (codingrules section 12).
    - A Night Veil reader's query records no event at all (ADR-0031).
    - The one await on a model runs under `[honey.retrieval] embed_timeout_s`.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for filtering, hybrid ranking and the
      Night Veil rule.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the per-model vectors and
      the degrade-to-text rule.
    - hivemind.honey_store.honey.rank and .budget for the pure steps this composes.
    - hivemind.honey_store.clearance.reader_ceiling for the ceiling a caller computes first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from hivemind.cell import HoneyClearance
from hivemind.guard import CapabilitySet
from hivemind.honey_store.honey.budget import pack_hits
from hivemind.honey_store.honey.rank import fuse, select, text_score, vector_score
from hivemind.honey_store.identity import HoneyIdentity, honey_event
from hivemind.honey_store.models import (
    Honey,
    HoneyPart,
    ReadFilter,
    TextCandidate,
    VectorCandidate,
)
from hivemind.honey_store.scope import readable_globs
from hivemind.honey_store.store import HoneyStore, build_match
from hivemind.llm import BoundEmbedder, EmbeddingRequest, EmbedGate, LLMError
from hivemind.manifest.schema.honey import HoneyRetrievalSection
from hivemind.pheromone import HoneyEvent
from waggle.clock import Clock
from waggle.messages.honey import HoneyHit, HoneyResponse

QUERIED_KIND = "honey.queried"  # The one trail event a search records.
EMPTY_QUERY_REASON = "The query has no word to search for, so nothing was searched."
NO_READABLE_SCOPE_REASON = "The reader holds no honey:read capability, so no scope is readable."
# Text-only fusion weight. A one-sided weighted mean is that side's own score whatever its weight,
# so 1.0 changes nothing, and it stays defined when the manifest's fts_weight is 0.
TEXT_ONLY_WEIGHT = 1.0

__all__ = [
    "EMPTY_QUERY_REASON",
    "NO_READABLE_SCOPE_REASON",
    "QUERIED_KIND",
    "TEXT_ONLY_WEIGHT",
    "HoneyReader",
    "HoneyRetriever",
    "HoneySearch",
    "RetrieverDeps",
    "SearchOutcome",
]


@dataclass(frozen=True, slots=True)
class HoneyReader:
    """Who is asking: its id, what it may read, how sensitive a row it may see, and its tier."""

    requester: str  # The asker's id (a worker_, warden_ or hive_ id): the event's subject.
    capabilities: CapabilitySet  # Its `honey:read:<scope glob>` capabilities decide the scopes.
    ceiling: HoneyClearance  # Already `clearance.reader_ceiling`'s result; the caller's job.
    is_night_veil: bool  # True for a reader on a Night Veil Cell: its query records no event.


@dataclass(frozen=True, slots=True)
class HoneySearch:
    """One query to answer: its words, its reader, and the scopes, hits and budget it asks for."""

    text: str  # The query's words; embedded and matched, never recorded anywhere.
    reader: HoneyReader  # Who is asking.
    requested_scopes: tuple[str, ...]  # Exact scopes to narrow to; empty means every readable one.
    max_hits: int  # The most hits to return (`HoneyQuery.max_hits`).
    max_tokens: int  # The asker's own budget; capped again by `max_budget_tokens`.


@dataclass(frozen=True, slots=True)
class RetrieverDeps:
    """Everything a HoneyRetriever needs: the store, its identity and clock, policy and embedder."""

    store: HoneyStore  # Where candidates come from and where `honey.queried` is recorded.
    identity: HoneyIdentity  # The Hive, node and actor stamped on the event.
    clock: Clock  # Mints the event's id and time.
    retrieval: HoneyRetrievalSection  # `[honey.retrieval]`: weights, floor, caps, embed timeout.
    embedder: BoundEmbedder | None = None  # The EMBEDDER slot; None searches full text only.
    embed_gate: EmbedGate | None = None  # The metered seam every embed call passes through.


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """A search's wire response, plus how it ran, for a caller that trails its own consultation.

    The Queen's pre-check and planner record `queen.honey_consulted` with whether vectors took
    part, which `HoneyResponse` carries only as prose in `reason`.
    """

    response: HoneyResponse  # What goes back to the asker, or into a TaskAssign.
    vector_used: bool  # Whether vector candidates took part in the ranking.


@dataclass(frozen=True, slots=True)
class _VectorSide:
    """The vector side's candidates, or the clause saying why it was not used."""

    candidates: tuple[VectorCandidate, ...]  # Empty when `unavailable` is set.
    unavailable: str | None  # None when the vector side ran; else a clause for the reason.


@dataclass(frozen=True, slots=True)
class _Answer:
    """One search's outcome, before it becomes a HoneyResponse and a `honey.queried` event."""

    hits: tuple[HoneyHit, ...]  # Packed, best first.
    token_count: int  # What `hits` cost, by `budget.hit_tokens`.
    is_truncated: bool  # The budget or max_hits cut a ranked hit.
    withheld: int  # Top text matches the reader's filter excluded.
    reason: str  # How the search ran, in one sentence.
    vector_used: bool  # Whether vector candidates took part.


class HoneyRetriever:
    """Answer Honey queries for any reader; holds no state beyond its injected deps."""

    def __init__(self, deps: RetrieverDeps) -> None:
        """Keep the collaborators every search shares.

        Args:
            deps: The store, identity, clock, `[honey.retrieval]` policy and optional embedder.
        """
        self._deps = deps

    async def search(self, search: HoneySearch) -> HoneyResponse:
        """Answer one query with the best hits the reader may see, within its budget.

        `filtered_count` counts the top full-text matches (`max_hits * candidate_multiplier` of
        them) the reader's scope and clearance filter excluded; the vector side has no such
        count, because nearest neighbours of an unreadable row say nothing about the query.

        Args:
            search: The words, the reader, and the scopes, hits and budget asked for.

        Returns:
            The response; an empty one with a reason for an empty or punctuation-only query or a
            reader with no readable scope, never an error.

        Raises:
            HoneyStoreError: The store itself failed; the vector side's own failures never raise.
        """
        outcome = await self.search_outcome(search)
        return outcome.response

    async def search_outcome(self, search: HoneySearch) -> SearchOutcome:
        """Answer one query exactly as `search` does, and also say whether vectors took part.

        Args:
            search: The words, the reader, and the scopes, hits and budget asked for.

        Returns:
            `search`'s own response, with `vector_used` beside it.

        Raises:
            HoneyStoreError: The store itself failed; the vector side's own failures never raise.
        """
        answer = await self._answer(search)
        if not search.reader.is_night_veil:
            # ADR-0031: a Night Veil reader's query leaves no trail at all; everyone else's leaves
            # counts only. Local SQLite on the store's own thread, milliseconds; no model involved.
            event = _queried_event(self._deps, search.reader.requester, answer)
            await self._deps.store.record(event)
        response = HoneyResponse(
            hits=answer.hits,
            token_count=answer.token_count,
            is_truncated=answer.is_truncated,
            filtered_count=answer.withheld,
            reason=answer.reason,
        )
        return SearchOutcome(response=response, vector_used=answer.vector_used)

    async def _answer(self, search: HoneySearch) -> _Answer:
        """Run both searches under the reader's filter and rank, select and pack the result."""
        # The words never reach MATCH as typed: only quoted word tokens do (store.fts).
        match = build_match(search.text)
        if match is None:
            return _empty(EMPTY_QUERY_REASON, withheld=0)  # No word at all: nothing to match.
        # Policy, not ranking: the store applies this before it ranks or limits anything.
        filter_ = ReadFilter(
            readable=readable_globs(search.reader.capabilities),
            max_clearance=search.reader.ceiling,
            requested=search.requested_scopes,
        )
        # Each side ranks a multiple of the hits asked for, so the floor and the per-Nectar cap
        # can discard candidates and still leave a full page.
        limit = search.max_hits * self._deps.retrieval.candidate_multiplier
        # Local SQLite on the store's own thread (milliseconds), like every store read in the Hive.
        withheld = await self._deps.store.count_withheld(match, filter_, limit)
        if not filter_.readable:
            # Fail closed, and skip the embedding call: nothing could be found anyway.
            return _empty(NO_READABLE_SCOPE_REASON, withheld=withheld)
        text_candidates = await self._deps.store.search_text(match, filter_, limit)
        vector_side = await self._vector_side(search.text, filter_, limit)
        return self._ranked(search, text_candidates, vector_side, withheld)

    def _ranked(
        self,
        search: HoneySearch,
        text_candidates: Sequence[TextCandidate],
        vector_side: _VectorSide,
        withheld: int,
    ) -> _Answer:
        """Fuse both sides' scores, choose the hits, and pack them into the budget."""
        retrieval = self._deps.retrieval
        vector_used = vector_side.unavailable is None
        # A row both sides found is the same stored row either way; keep one copy per id.
        honey_by_id = {candidate.honey.id: candidate.honey for candidate in text_candidates}
        honey_by_id.update((c.honey.id, c.honey) for c in vector_side.candidates)
        # With no vector side the manifest's weights no longer apply: text alone decides.
        fused = fuse(
            {candidate.honey.id: text_score(candidate.bm25) for candidate in text_candidates},
            {
                candidate.honey.id: vector_score(candidate.distance)
                for candidate in vector_side.candidates
            },
            retrieval.fts_weight if vector_used else TEXT_ONLY_WEIGHT,
            retrieval.vector_weight if vector_used else 0.0,
        )
        selection = select(
            fused,
            honey_by_id,
            min_score=retrieval.min_score,
            max_hits=search.max_hits,
            max_hits_per_nectar=retrieval.max_hits_per_nectar,
        )
        hits = tuple(
            ranked.honey.to_hit(ranked.score, _excerpt(ranked.honey)) for ranked in selection.ranked
        )
        # The asker's budget, never past the manifest's hard ceiling however large its window.
        packed = pack_hits(hits, min(search.max_tokens, retrieval.max_budget_tokens))
        # Truncated when either the budget or max_hits left a ranked, eligible hit out.
        truncated = packed.is_truncated or selection.is_cut
        return _Answer(
            hits=packed.hits,
            token_count=packed.token_count,
            is_truncated=truncated,
            withheld=withheld,
            reason=_reason(vector_side.unavailable, len(packed.hits), withheld, truncated),
            vector_used=vector_used,
        )

    async def _vector_side(self, text: str, filter_: ReadFilter, limit: int) -> _VectorSide:
        """Embed the query and fetch its nearest readable rows, or say why the side is skipped."""
        deps = self._deps
        if deps.embedder is None or deps.embed_gate is None:
            return _VectorSide(candidates=(), unavailable="no embedder is bound")
        if deps.retrieval.vector_weight == 0:
            # The operator turned the vector side off; an embedding would only cost a call.
            return _VectorSide(candidates=(), unavailable="[honey.retrieval] vector_weight is 0")
        embedded = await self._embed_query(deps.embedder, deps.embed_gate, text)
        if isinstance(embedded, str):
            return _VectorSide(candidates=(), unavailable=embedded)
        vector, model = embedded
        # Local SQLite on the store's own thread; only rows embedded by `model` are compared.
        candidates = await deps.store.search_vectors(vector, model, filter_, limit)
        return _VectorSide(candidates=candidates, unavailable=None)

    async def _embed_query(
        self, embedder: BoundEmbedder, gate: EmbedGate, text: str
    ) -> tuple[tuple[float, ...], str] | str:
        """Embed the query's words: its vector and model, or a clause saying why not."""
        timeout_s = self._deps.retrieval.embed_timeout_s
        try:
            # External await: one embedding call, milliseconds on a local server and a second or
            # so hosted; past `embed_timeout_s` the query searches full text only rather than wait.
            async with asyncio.timeout(timeout_s):
                response = await gate.embed(embedder, EmbeddingRequest(texts=(text,)))
        except TimeoutError:
            return f"the embedder did not answer within {timeout_s:g}s"
        except LLMError as exc:
            return f"the embedder failed ({exc.code})"
        if len(response.vectors) != 1:
            # One text in, so exactly one vector out; anything else is a broken adapter.
            return f"the embedder returned {len(response.vectors)} vectors for one query"
        vector = response.vectors[0]
        if all(component == 0.0 for component in vector):
            # No direction to compare against: the store's own zero-norm rule, applied to a query.
            return "the query embedded to a zero vector"
        return vector, response.model


def _queried_event(deps: RetrieverDeps, requester: str, answer: _Answer) -> HoneyEvent:
    """Mint the one `honey.queried` event a search records: counts and flags, never the words."""
    return honey_event(
        deps.identity,
        deps.clock,
        QUERIED_KIND,
        requester,
        hits=len(answer.hits),
        withheld=answer.withheld,
        tokens=answer.token_count,
        vector_used=answer.vector_used,
        truncated=answer.is_truncated,
    )


def _excerpt(honey: Honey) -> str:
    """Return the passage a hit shows: the summary for a SUMMARY row, the body for a CHUNK row."""
    return honey.summary if honey.part is HoneyPart.SUMMARY else honey.body


def _empty(reason: str, *, withheld: int) -> _Answer:
    """Build an answer with no hits, for a query that never reached ranking."""
    return _Answer(
        hits=(),
        token_count=0,
        is_truncated=False,
        withheld=withheld,
        reason=reason,
        vector_used=False,
    )


def _reason(unavailable: str | None, hits: int, withheld: int, truncated: bool) -> str:
    """Say in one sentence how a search ran: its mode, its counts, and whether it was cut."""
    if unavailable is None:
        mode = "Hybrid search (full text and vectors)"
    else:
        mode = f"Full-text search only ({unavailable})"
    cut = "; the token budget or max_hits cut the ranked result" if truncated else ""
    return (
        f"{mode}: {hits} hits returned, {withheld} top matches withheld by scope or clearance{cut}."
    )
