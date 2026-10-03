"""Embed Honey on the EMBEDDER slot: vectors in batches, and a backoff for an embedder that fails.

Ripening gives every Honey row a vector from the EMBEDDER model slot when one is bound (ADR-0035,
ADR-0036). `batches` makes the calls: texts in batches, every vector checked against the
binding's model, None on any failure, and the per-pass re-embed of rows still pending. `backoff`
keeps one binding's run of failures between passes, so a binding that cannot answer is tried at a
growing interval instead of on every pass, and said to be failing once rather than every 30
seconds; a binding whose provider serves no embeddings at all is said so once, in plain words.
A package because the embedding concept needed that second file (codingrules 5.2).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening`.
    Called by the ripening pipeline (`dedupe`, `index`, `pipeline`) and, for the backoff, built
    by the composition root (`hivemind.cli.compose.honey`). Calls into `hivemind.llm`,
    `hivemind.honey_store` (store, identity, models) and the ripening `deps` only.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the embedder's degrade rule.

Public API:
    - embed_texts, embedding_text, embed_pending_rows, is_zero_vector, EMBED_BATCH_TIMEOUT_S:
      vectors in batches, and the per-pass re-embed (batches).
    - EmbedBackoff, EmbedFailure, MISMATCH, no_embeddings, FIRST_RETRY_S, MAX_RETRY_S: when a
      failing embedder is tried again, and what each failure says (backoff).
"""

from hivemind.honey_store.ripening.embed.backoff import (
    FIRST_RETRY_S,
    MAX_RETRY_S,
    MISMATCH,
    EmbedBackoff,
    EmbedFailure,
    no_embeddings,
)
from hivemind.honey_store.ripening.embed.batches import (
    EMBED_BATCH_TIMEOUT_S,
    embed_pending_rows,
    embed_texts,
    embedding_text,
    is_zero_vector,
)

__all__ = [
    "EMBED_BATCH_TIMEOUT_S",
    "FIRST_RETRY_S",
    "MAX_RETRY_S",
    "MISMATCH",
    "EmbedBackoff",
    "EmbedFailure",
    "embed_pending_rows",
    "embed_texts",
    "embedding_text",
    "is_zero_vector",
    "no_embeddings",
]
