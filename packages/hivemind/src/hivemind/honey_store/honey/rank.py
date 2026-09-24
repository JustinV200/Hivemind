"""Rank Honey search candidates: normalise both sides' scores, fuse them, and choose the hits.

Honey is the Hive's ripened, labelled knowledge; a query searches it two ways at once (ADR-0031's
"Hybrid ranking"): full-text search ranks rows by SQLite FTS5's `bm25` (negative, lower is a
better match) and vector search ranks them by cosine distance between embeddings (0 is identical).
The two raw numbers live on different scales, so each is first normalised into [0, 1]
(`text_scores`, `vector_score`), then `fuse` takes their weighted mean with the manifest's
`[honey.retrieval]` weights -- a row found by only one side scores zero on the other -- and
`select` keeps the best of them: nothing under the score floor, at most `max_hits_per_nectar` hits
from any one Nectar (the raw deposit a row was ripened from), at most `max_hits` in all, best
first. A weighted mean rather than reciprocal rank fusion is ADR-0031's choice: it keeps how good a
match is, so the nearest of a set of irrelevant vectors still scores low enough for the floor to
drop it. The text side has one more rule: FTS5's bm25 treats a word found in half the rows or
more as worthless (its weight falls to a 1e-6 floor), which on a young store -- one ripened Nectar
is a SUMMARY row and a CHUNK row sharing its text -- scores every match near zero however exact it
is. `text_scores` therefore floors each match at `RELATIVE_TEXT_FLOOR` times its strength relative
to the query's best match, so the best match (and those near it) stays findable there, while on an
established store the absolute score is already the larger of the two for any good match.
Everything here is pure (codingrules 8.3): numbers and rows in, numbers and rows out.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    retrieval side. Called by `hivemind.honey_store.honey.retrieve.HoneyRetriever.search` once
    the store has returned both sides' candidates (already filtered by scope and clearance);
    calls into `hivemind.honey_store.models` (Honey) and `waggle.messages.honey.hit` (the score
    bounds) only. What `select` returns becomes the hits `hivemind.honey_store.honey.budget`
    packs into the reader's token budget.

Key invariants:
    - `text_score`, `text_scores`, `vector_score` and every value `fuse` returns lie in [0, 1],
      and each is monotone in its input: a better bm25, a nearer vector or a higher side score
      never lowers a fused score (property-tested in `tests/unit/honey_store/honey/test_rank.py`).
    - `fuse` refuses two zero weights (the mean would divide by zero); a caller with no vector
      side passes `vector_weight = 0` and a positive text weight.
    - `select` is deterministic: score descending, then newer `created_at`, then id ascending.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the "Hybrid ranking" formulas.
    - hivemind.manifest.schema.honey for HoneyRetrievalSection, the weights and bounds used here.
    - hivemind.honey_store.honey.retrieve for the one caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.honey_store.models import Honey
from waggle.ids import HoneyId, NectarId
from waggle.messages.honey.hit import MAX_SCORE, MIN_SCORE

RELATIVE_TEXT_FLOOR = 0.5  # A query's best full-text match scores at least this on the text
# side, and every other match this times its share of the best one's strength (module docstring).

__all__ = [
    "RELATIVE_TEXT_FLOOR",
    "RankedHoney",
    "Selection",
    "fuse",
    "select",
    "text_score",
    "text_scores",
    "vector_score",
]


@dataclass(frozen=True, slots=True)
class RankedHoney:
    """One Honey row chosen for a result, with the fused score it was chosen by."""

    honey: Honey  # The row itself, as the store returned it.
    score: float  # Its fused relevance score, in [0, 1].


@dataclass(frozen=True, slots=True)
class Selection:
    """What `select` chose, and whether `max_hits` left any otherwise-eligible row out."""

    ranked: tuple[RankedHoney, ...]  # Best first; at most `max_hits` long.
    is_cut: bool  # True when an eligible row ranked past `max_hits` (a truncated result).


def text_score(bm25: float) -> float:
    """Normalise one full-text match's raw bm25 into [0, 1): `x / (1 + x)` for `x = max(0, -bm25)`.

    SQLite's bm25 is negative for a match and more negative for a better one, with no fixed
    floor; `x / (1 + x)` maps any non-negative `x` into [0, 1) smoothly, so a strong match
    approaches 1 without ever reaching it and a positive (non-matching) bm25 scores 0.

    Args:
        bm25: The candidate's raw `bm25(honey_fts)` value.

    Returns:
        The normalised text score, in [0, 1).
    """
    strength = max(0.0, -bm25)
    return strength / (1.0 + strength)


def text_scores(bm25_by_id: Mapping[HoneyId, float]) -> dict[HoneyId, float]:
    """Normalise one query's full-text candidates: absolute score, floored relative to the best.

    Args:
        bm25_by_id: Every full-text candidate's raw `bm25(honey_fts)`, by row id.

    Returns:
        Per row, the larger of `text_score(bm25)` and `RELATIVE_TEXT_FLOOR` times its strength
        (`-bm25`) as a fraction of the strongest candidate's; in [0, 1).
    """
    strengths = {honey_id: max(0.0, -bm25) for honey_id, bm25 in bm25_by_id.items()}
    best = max(strengths.values(), default=0.0)
    scores: dict[HoneyId, float] = {}
    for honey_id, strength in strengths.items():
        # A best strength of 0 means every match is equally weak: each keeps the plain floor.
        share = strength / best if best > 0 else 1.0
        scores[honey_id] = max(text_score(-strength), RELATIVE_TEXT_FLOOR * share)
    return scores


def vector_score(distance: float) -> float:
    """Normalise one vector match's cosine distance into [0, 1]: `1 - distance`, clamped.

    Cosine distance runs from 0 (same direction) to 2 (opposite); anything at or past 1
    (orthogonal or worse) says nothing about relevance, so it scores 0 rather than negative.

    Args:
        distance: The candidate's cosine distance from the query vector.

    Returns:
        The normalised vector score, in [0, 1].
    """
    return max(MIN_SCORE, min(MAX_SCORE, 1.0 - distance))


def fuse(
    text_scores: Mapping[HoneyId, float],
    vector_scores: Mapping[HoneyId, float],
    fts_weight: float,
    vector_weight: float,
) -> dict[HoneyId, float]:
    """Fuse both sides' normalised scores into one weighted mean per row (ADR-0031).

    Args:
        text_scores: `text_score` per row the full-text side found.
        vector_scores: `vector_score` per row the vector side found; empty when that side was
            unavailable, in which case the caller also passes `vector_weight = 0`.
        fts_weight: The full-text side's weight; must be >= 0.
        vector_weight: The vector side's weight; must be >= 0.

    Returns:
        One fused score in [0, 1] for every row either side found; a row found by only one side
        scores 0 on the other.

    Raises:
        ValueError: A weight is negative, or both are zero (the mean would be undefined).
    """
    # Checked here rather than trusted from the manifest: the retriever substitutes its own
    # weights when the vector side is down, and a zero total would divide by zero below.
    if fts_weight < 0 or vector_weight < 0 or fts_weight + vector_weight <= 0:
        raise ValueError(
            f"fuse needs non-negative weights with a positive sum, got fts_weight={fts_weight} "
            f"and vector_weight={vector_weight}."
        )
    total = fts_weight + vector_weight
    # Every row either side found gets a score; the missing side contributes 0, never skips it.
    fused: dict[HoneyId, float] = {}
    for honey_id in text_scores.keys() | vector_scores.keys():
        weighted = fts_weight * text_scores.get(honey_id, 0.0)
        weighted += vector_weight * vector_scores.get(honey_id, 0.0)
        # Clamp against float rounding only: a mean of values in [0, 1] is already in [0, 1].
        fused[honey_id] = max(MIN_SCORE, min(MAX_SCORE, weighted / total))
    return fused


def select(
    scored: Mapping[HoneyId, float],
    honey_by_id: Mapping[HoneyId, Honey],
    *,
    min_score: float,
    max_hits: int,
    max_hits_per_nectar: int,
) -> Selection:
    """Choose the hits for one result from the fused scores (ADR-0031).

    Args:
        scored: The fused score per row (`fuse`'s result).
        honey_by_id: Every row `scored` names, by id; the caller builds it from the same
            candidates `scored` came from.
        min_score: The floor a hit's score must reach (`[honey.retrieval] min_score`).
        max_hits: The most hits to choose.
        max_hits_per_nectar: The most hits any one Nectar may contribute, so one long deposit's
            chunks never fill a whole page.

    Returns:
        The chosen hits, best first (score descending, then newer `created_at`, then id), plus
        whether `max_hits` cut an otherwise-eligible row.
    """
    ordered = sorted(scored.items(), key=lambda item: _order_key(item, honey_by_id))
    chosen: list[RankedHoney] = []
    per_nectar: dict[NectarId, int] = {}
    # Walk best first, keeping what clears the floor and the per-Nectar cap, until max_hits fill.
    for honey_id, score in ordered:
        if score < min_score:
            break  # Sorted by score: every later row is under the floor too.
        honey = honey_by_id[honey_id]
        if per_nectar.get(honey.nectar_id, 0) >= max_hits_per_nectar:
            continue  # This deposit already contributed its share; a later one may still fit.
        if len(chosen) >= max_hits:
            return Selection(ranked=tuple(chosen), is_cut=True)  # An eligible row did not fit.
        chosen.append(RankedHoney(honey=honey, score=score))
        per_nectar[honey.nectar_id] = per_nectar.get(honey.nectar_id, 0) + 1
    return Selection(ranked=tuple(chosen), is_cut=False)


def _order_key(
    item: tuple[HoneyId, float], honey_by_id: Mapping[HoneyId, Honey]
) -> tuple[float, float, str]:
    """Sort key for `select`: score descending, then newer `created_at`, then id ascending."""
    honey_id, score = item
    return (-score, -honey_by_id[honey_id].created_at.timestamp(), honey_id)
