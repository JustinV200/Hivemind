"""Embed Honey text on the EMBEDDER slot, in batches, degrading to "no vectors" on any failure.

Honey (the Honey Store's ripened, searchable rows) is found two ways: full-text search, and
nearest-neighbour search over one vector per row from the EMBEDDER model slot (ADR-0035). This
module is the embedding half of ripening. `embed_texts` sends texts in batches no larger than the
manifest's `embed_batch` or the provider's own `max_batch`, each call under a timeout, and returns
one vector per text in order -- or None when any batch fails, times out, or comes back from a
model other than the binding's own, because a partial or mixed set of vectors is worse than none
(rows without a vector stay pending and full-text search still finds them, ADR-0036).
`embedding_text` says what text stands for a row, and `embed_pending_rows` is the per-pass
re-embed: live rows with no vector yet for the current model, oldest first, bounded per pass.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening`.
    Called by `hivemind.honey_store.ripening.dedupe` (vectors for a Nectar's fresh drafts) and by
    `hivemind.honey_store.ripening.pipeline.Ripener.embed_pending`. Calls into `hivemind.llm`
    (EmbeddingRequest, the embed gate, LLMError) and `hivemind.honey_store` (store, identity,
    models) only.

Key invariants:
    - Every vector returned or stored came from `bound.model` (the response's own model is
      checked), so the store never tags a vector with a model that did not make it (ADR-0036).
    - Order is preserved: the i-th vector belongs to the i-th text.
    - A zero-norm vector is never handed to the store (`HoneyStore.set_vectors` refuses one); its
      row is skipped and counted, and stays pending.
    - Nothing here raises for a provider failure: every `LLMError` and every call's own timeout
      ends in None (or zero rows re-embedded).

See Also:
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for "every vector names its model"
      and the progressive re-embed this module's `embed_pending_rows` performs.
    - hivemind.llm.embedding for EmbeddingRequest, EmbeddingResponse, BoundEmbedder and EmbedGate.
    - hivemind.honey_store.store.protocol for set_vectors and pending_vectors.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from hivemind.common.logging import get_logger
from hivemind.honey_store.identity import honey_event
from hivemind.honey_store.models import Honey, HoneyDraft, HoneyPart
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.llm import MAX_EMBED_TEXTS, BoundEmbedder, EmbeddingRequest, EmbedGate, LLMError

EMBED_BATCH_TIMEOUT_S = 60.0  # One batch of chunks on a local CPU server; a pass never hangs on it.
_TEXT_SEPARATOR = "\n\n"  # Between a row's title and its summary or body.

__all__ = [
    "EMBED_BATCH_TIMEOUT_S",
    "embed_pending_rows",
    "embed_texts",
    "embedding_text",
    "is_zero_vector",
]

log = get_logger(__name__)


async def embed_texts(
    texts: Sequence[str], bound: BoundEmbedder, gate: EmbedGate, batch: int
) -> tuple[tuple[float, ...], ...] | None:
    """Embed `texts` on `bound` in batches, preserving order; None when any batch fails.

    Args:
        texts: Non-empty texts to embed; empty input makes no call at all.
        bound: The EMBEDDER binding; every vector must come back tagged with `bound.model`.
        gate: How each call is made (the Fanner's lane in production).
        batch: `[honey.ripening] embed_batch`; the provider's `max_batch` wins when lower.

    Returns:
        One vector per text, in order; None after an `LLMError`, a timeout, a reply from another
        model or a reply with the wrong number of vectors.
    """
    size = max(1, min(batch, bound.provider.capabilities.max_batch, MAX_EMBED_TEXTS))
    vectors: list[tuple[float, ...]] = []
    # One request per slice of `size` texts; the first failure abandons the whole set.
    for offset in range(0, len(texts), size):
        piece = tuple(texts[offset : offset + size])
        try:
            # External await: one embedding call, well under a second on a local server; a stuck
            # one is abandoned at EMBED_BATCH_TIMEOUT_S and the texts stay unembedded.
            async with asyncio.timeout(EMBED_BATCH_TIMEOUT_S):
                response = await gate.embed(bound, EmbeddingRequest(texts=piece))
        except (LLMError, TimeoutError) as error:
            log.warning(
                "honey_store.embed_failed", binding=bound.binding, error=type(error).__name__
            )
            return None
        # Vectors from another model are not comparable with the store's (ADR-0036), and a short
        # reply cannot be matched back to its texts: both are treated as a failed batch.
        if response.model != bound.model or len(response.vectors) != len(piece):
            log.warning("honey_store.embed_mismatch", binding=bound.binding, texts=len(piece))
            return None
        vectors.extend(response.vectors)
    return tuple(vectors)


def embedding_text(item: HoneyDraft | Honey) -> str:
    """Return the text that stands for one Honey row in vector search.

    Args:
        item: A draft about to be stored, or a stored row being (re-)embedded.

    Returns:
        The title and the summary for a SUMMARY part, the title and the body for a CHUNK part;
        never empty (the part's name stands in for a row with no text at all), since an embedding
        request refuses an empty text.
    """
    detail = item.summary if item.part is HoneyPart.SUMMARY else item.body
    text = _TEXT_SEPARATOR.join(part for part in (item.title.strip(), detail.strip()) if part)
    return text or item.part.value


def is_zero_vector(vector: Sequence[float]) -> bool:
    """Return whether every component of `vector` is zero (it has no direction to compare).

    Args:
        vector: An embedding.

    Returns:
        True when the store would refuse it (`HoneyStore.set_vectors`).
    """
    return all(component == 0.0 for component in vector)


async def embed_pending_rows(deps: RipenerDeps) -> int:
    """Embed live rows with no vector yet for the current embedder's model, oldest first.

    Args:
        deps: The store, identity, clock, `[honey.ripening]` settings and EMBEDDER binding.

    Returns:
        How many rows got a vector; 0 when there is no embedder, nothing is pending, or the
        embedding call failed (the rows stay pending for the next pass).
    """
    embedder = deps.embedder
    if embedder is None:
        return 0  # No EMBEDDER bound: the store stays full-text only, which is not an error.
    # Local SQLite, bounded by the connection's busy timeout.
    rows = await deps.store.pending_vectors(embedder.model, deps.ripening.max_embed_per_pass)
    if not rows:
        return 0
    texts = [embedding_text(row) for row in rows]
    vectors = await embed_texts(texts, embedder, deps.embedding_gate(), deps.ripening.embed_batch)
    if vectors is None:
        return 0
    pairs = [
        (row.id, vector)
        for row, vector in zip(rows, vectors, strict=True)
        if not is_zero_vector(vector)
    ]
    if not pairs:
        return 0
    event = honey_event(
        deps.identity,
        deps.clock,
        "honey.reembedded",
        pairs[0][0],
        rows=len(pairs),
        skipped=len(rows) - len(pairs),
        model=embedder.model,
    )
    # One transaction for every vector and the pass's one reembedded event; local SQLite.
    return await deps.store.set_vectors(pairs, embedder.model, event)
