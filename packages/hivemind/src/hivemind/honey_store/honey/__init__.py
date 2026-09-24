"""Retrieve Honey: hybrid search of ripened knowledge, filtered by scope and clearance, budgeted.

Honey is the Hive's ripened, labelled knowledge: Nectar (raw findings) once the House Bee (the
maintenance Worker) has chunked, summarised, embedded and indexed it. This sub-package is the
Honey Store's read side (roadmap step 7.7): `retrieve.HoneyRetriever` answers one query by
searching full text and, when an embedder is bound, vectors, under the reader's own scope and
clearance filter; `rank` normalises and fuses the two sides' scores and chooses the hits; `budget`
packs them into the asker's token budget. Split by responsibility (codingrules 5.2) so the two
pure steps are testable without a store.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package.
    Called by the Queen (a bee's `HoneyQuery`, her dispatch pre-check and her planner) and by
    `hive honey`; calls into hivemind.honey_store's store, scope, models and identity, and into
    hivemind.llm for the query's own embedding. Never imports hivemind.memory: a caller hands the
    hits to `hivemind.memory.assemble` as data.

Key invariants:
    - Every search is filtered by the reader's scope and clearance in the store, before ranking.
    - A search never fails for want of vectors: it degrades to full text and says so.
    - Retrieval never writes Honey; its one write is the `honey.queried` trail event, and none
      at all for a Night Veil reader.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for hybrid ranking and filtering.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the vector side's rules.
    - hivemind.honey_store.store for the HoneyStore protocol the retriever reads through.

Public API:
    - HoneyRetriever, RetrieverDeps, HoneyReader, HoneySearch, SearchOutcome (retrieve): answer
      one query (`search`), or answer it and say whether vectors took part (`search_outcome`).
    - QUERIED_KIND, EMPTY_QUERY_REASON, NO_READABLE_SCOPE_REASON, TEXT_ONLY_WEIGHT (retrieve):
      the event kind and the fixed answers a search can give.
    - text_score, vector_score, fuse, select, RankedHoney, Selection (rank): hybrid ranking.
    - estimate_tokens, hit_tokens, pack_hits, PackResult, MIN_EXCERPT_CHARS (budget): the token
      budget.
"""

from hivemind.honey_store.honey.budget import (
    MIN_EXCERPT_CHARS,
    PackResult,
    estimate_tokens,
    hit_tokens,
    pack_hits,
)
from hivemind.honey_store.honey.rank import (
    RankedHoney,
    Selection,
    fuse,
    select,
    text_score,
    vector_score,
)
from hivemind.honey_store.honey.retrieve import (
    EMPTY_QUERY_REASON,
    NO_READABLE_SCOPE_REASON,
    QUERIED_KIND,
    TEXT_ONLY_WEIGHT,
    HoneyReader,
    HoneyRetriever,
    HoneySearch,
    RetrieverDeps,
    SearchOutcome,
)

__all__ = [
    "EMPTY_QUERY_REASON",
    "MIN_EXCERPT_CHARS",
    "NO_READABLE_SCOPE_REASON",
    "QUERIED_KIND",
    "TEXT_ONLY_WEIGHT",
    "HoneyReader",
    "HoneyRetriever",
    "HoneySearch",
    "PackResult",
    "RankedHoney",
    "RetrieverDeps",
    "SearchOutcome",
    "Selection",
    "estimate_tokens",
    "fuse",
    "hit_tokens",
    "pack_hits",
    "select",
    "text_score",
    "vector_score",
]
