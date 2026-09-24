"""Ripen Nectar into Honey: decode, chunk, summarise, embed, deduplicate and index each deposit.

Nectar is raw information a bee brought back and the Honey Store (the Hive's knowledge base) took
in; Honey is what it becomes here: one SUMMARY row and one CHUNK row per slice of its text, each
labelled with a HoneyClearance (a data-sensitivity tier), filed under a scope, indexed for
full-text search and, when an EMBEDDER is bound, given a vector (ADR-0031). The House Bee (the
maintenance Worker) runs `Ripener.run_pass` on a timer beside the Queen, never inside her tick;
`hive honey ripen --now` and `hive honey reembed` run the same passes on demand. A concept needing
more than one file becomes a package (codingrules 5.2): one module per pipeline stage, composed
by `pipeline`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    by the House Bee (`hivemind.workers.roles.house_bee`) and the `hive honey` CLI; calls into
    `hivemind.llm` (the RIPENER and EMBEDDER slots, through the call and embed gates) and the rest
    of `hivemind.honey_store` (store, identity, clearance, errors, models), never into its
    `nectar` or `honey` sub-packages.

Key invariants:
    - No model or embedding failure ever fails a deposit: summaries fall back to a heuristic and
      rows without vectors stay pending, still found by full-text search (ADR-0032).
    - A label is only ever raised during ripening, never lowered.
    - Every model and embedding call has its own timeout, so a pass never hangs.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the pipeline's decisions.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for vectors and re-embedding.
    - hivemind.honey_store.nectar for intake, which fills the store this package ripens.

Public API:
    - Ripener, RipenOutcome, PassOutcome (pipeline): one pass, `ripen_pending` and
      `embed_pending`, and their counts.
    - RipenerDeps (deps): the store, identity, clock, settings and bindings a pass runs on.
    - TextChunk, decode_text, normalise_text, chunk_text (chunk): bytes to text to chunks.
    - RipenedSummary, SummaryOutcome, summarise, heuristic_summary (summarise): one deposit's
      title, summary, key facts and label.
    - PreparedPart, ripened_drafts, metadata_draft (drafts): the HoneyDrafts a Nectar ripens into.
    - embed_texts, embedding_text, embed_pending_rows, is_zero_vector (embed): vectors in
      batches.
    - NearDuplicateCheck, drop_exact_duplicates, drop_near_duplicates (dedupe): duplicate parts.
    - RipenedNectar, IndexResult, index_ripened (index): one Nectar's rows, vectors and events.
    - prune_vectors, PruneOutcome (prune): drop a superseded model's vectors on request
      (ADR-0033), never automatically.
"""

from hivemind.honey_store.ripening.chunk import TextChunk, chunk_text, decode_text, normalise_text
from hivemind.honey_store.ripening.dedupe import (
    NearDuplicateCheck,
    drop_exact_duplicates,
    drop_near_duplicates,
)
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.honey_store.ripening.drafts import PreparedPart, metadata_draft, ripened_drafts
from hivemind.honey_store.ripening.embed import (
    embed_pending_rows,
    embed_texts,
    embedding_text,
    is_zero_vector,
)
from hivemind.honey_store.ripening.index import IndexResult, RipenedNectar, index_ripened
from hivemind.honey_store.ripening.pipeline import PassOutcome, Ripener, RipenOutcome
from hivemind.honey_store.ripening.prune import PruneOutcome, prune_vectors
from hivemind.honey_store.ripening.summarise import (
    RipenedSummary,
    SummaryOutcome,
    heuristic_summary,
    summarise,
)

__all__ = [
    "IndexResult",
    "NearDuplicateCheck",
    "PassOutcome",
    "PreparedPart",
    "PruneOutcome",
    "RipenOutcome",
    "RipenedNectar",
    "RipenedSummary",
    "Ripener",
    "RipenerDeps",
    "SummaryOutcome",
    "TextChunk",
    "chunk_text",
    "decode_text",
    "drop_exact_duplicates",
    "drop_near_duplicates",
    "embed_pending_rows",
    "embed_texts",
    "embedding_text",
    "heuristic_summary",
    "index_ripened",
    "is_zero_vector",
    "metadata_draft",
    "normalise_text",
    "prune_vectors",
    "ripened_drafts",
    "summarise",
]
