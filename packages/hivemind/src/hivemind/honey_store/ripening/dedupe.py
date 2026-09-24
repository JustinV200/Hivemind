"""Drop duplicate CHUNK parts before they are stored: exact within one Nectar, near across a scope.

Ripening turns one Nectar (a raw deposit in the Honey Store, the Hive's knowledge base) into a
SUMMARY part and many CHUNK parts (ADR-0031). A transcript repeats itself, and the same finding
arrives from two tasks, so storing every chunk would fill search results with copies. Two passes
remove them. `drop_exact_duplicates` (pure) drops a CHUNK whose whitespace- and case-normalised
body repeats one already kept for the same Nectar -- the SUMMARY's own body included -- or is
empty once normalised. `drop_near_duplicates` runs once vectors exist: a CHUNK whose nearest live
row in the same scope, embedded by the same model, is at cosine similarity at or above
`[honey.ripening] near_duplicate_similarity` is not stored, provided that row's clearance is no
higher than the chunk's own, so every reader who could have found the new chunk still finds the
old one. SUMMARY parts are never dropped: each Nectar keeps its own entry point.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening`.
    Called by `hivemind.honey_store.ripening.pipeline.Ripener` between embedding and indexing.
    Calls into `hivemind.honey_store` (models, store) and this package's `drafts` and `embed`
    modules only.

Key invariants:
    - Order is preserved; only CHUNK parts are ever dropped.
    - A near-duplicate is judged only against rows of the same scope and the same embedding model
      (`HoneyStore.nearest_in_scope`), never across models (ADR-0032).
    - A chunk is never dropped in favour of a row labelled above it: that would hide the content
      from a reader cleared for the chunk but not for the row.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the SUMMARY/CHUNK shape.
    - hivemind.manifest.schema.honey for `near_duplicate_similarity`.
    - hivemind.honey_store.store.protocol for `nearest_in_scope`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hivemind.honey_store.models import HoneyDraft, HoneyPart, VectorCandidate
from hivemind.honey_store.ripening.drafts import PreparedPart
from hivemind.honey_store.ripening.embed import is_zero_vector
from hivemind.honey_store.store import HoneyStore

NEAR_DUPLICATE_CANDIDATES = 4  # Enough to look past a nearest row labelled above the chunk.

__all__ = [
    "NEAR_DUPLICATE_CANDIDATES",
    "NearDuplicateCheck",
    "drop_exact_duplicates",
    "drop_near_duplicates",
    "is_near_duplicate",
    "normalised_body",
]


@dataclass(frozen=True, slots=True)
class NearDuplicateCheck:
    """Where and how one Nectar's chunks are compared against the rows already stored."""

    store: HoneyStore  # Answers `nearest_in_scope`.
    model: str  # The embedding model every compared vector must come from.
    scope: str  # The Nectar's own scope; only rows filed there are compared.
    threshold: float  # `[honey.ripening] near_duplicate_similarity`: cosine similarity, 0 to 1.


def normalised_body(body: str) -> str:
    """Return `body` with whitespace runs collapsed to one space, trimmed and case-folded.

    Args:
        body: A draft's body.

    Returns:
        The key two bodies are compared on for exact duplication.
    """
    return " ".join(body.split()).casefold()


def drop_exact_duplicates(drafts: Sequence[HoneyDraft]) -> tuple[tuple[HoneyDraft, ...], int]:
    """Drop CHUNK drafts whose normalised body is empty or repeats one already kept.

    Args:
        drafts: One Nectar's drafts, SUMMARY first.

    Returns:
        The kept drafts in their original order, and how many were dropped.
    """
    seen: set[str] = set()
    kept: list[HoneyDraft] = []
    # Walk in order so the first copy of a body is the one kept; a SUMMARY is always kept but its
    # body still counts as seen, so a chunk that merely repeats it is dropped.
    for draft in drafts:
        key = normalised_body(draft.body)
        if draft.part is HoneyPart.CHUNK and (not key or key in seen):
            continue
        seen.add(key)
        kept.append(draft)
    return tuple(kept), len(drafts) - len(kept)


def is_near_duplicate(
    draft: HoneyDraft, candidates: Sequence[VectorCandidate], threshold: float
) -> bool:
    """Decide whether some stored row already says what `draft` says, for everyone who could see it.

    Args:
        draft: A CHUNK draft.
        candidates: The rows nearest to its vector, same scope and model.
        threshold: The cosine similarity at or above which two rows say the same thing.

    Returns:
        True when a candidate is at least `threshold` similar and labelled no higher than `draft`.
    """
    return any(
        1.0 - candidate.distance >= threshold
        and candidate.honey.clearance.rank <= draft.clearance.rank
        for candidate in candidates
    )


async def drop_near_duplicates(
    check: NearDuplicateCheck, parts: Sequence[PreparedPart]
) -> tuple[tuple[PreparedPart, ...], int]:
    """Drop CHUNK parts that near-duplicate a live row already stored in the same scope.

    Args:
        check: The store, model, scope and similarity threshold to compare with.
        parts: One Nectar's parts with their vectors, SUMMARY first.

    Returns:
        The kept parts in their original order, and how many were dropped.
    """
    kept: list[PreparedPart] = []
    # One nearest-neighbour lookup per chunk that has a usable vector; everything else is kept.
    for part in parts:
        vector = part.vector
        if part.draft.part is HoneyPart.CHUNK and vector is not None and not is_zero_vector(vector):
            # Local SQLite (an exact scan of the scope's vectors), bounded by the busy timeout.
            candidates = await check.store.nearest_in_scope(
                vector, check.model, check.scope, NEAR_DUPLICATE_CANDIDATES
            )
            if is_near_duplicate(part.draft, candidates, check.threshold):
                continue
        kept.append(part)
    return tuple(kept), len(parts) - len(kept)
