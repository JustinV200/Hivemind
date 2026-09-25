"""Estimate what Honey hits cost in tokens and pack them, best first, into a reader's budget.

A Honey query (a search of the Hive's ripened knowledge) carries a token budget the asker scaled
from its own model's context window (`HoneyQuery.max_tokens`, capped by `[honey.retrieval]
max_budget_tokens`), because hits are pasted into that model's prompt. The Honey Store cannot count
tokens with the reader's own tokenizer (the reader may be on another model or another machine), so
`estimate_tokens` uses the same rule of thumb as `hivemind.memory.counter`'s estimate, four
characters to a token rounded up, plus a small fixed margin per text; `hit_tokens` adds a fixed
allowance for the metadata a reader renders beside every excerpt. `pack_hits` then takes the ranked
hits in order and stops at the first one that does not fit, the same single-pass rule hot-state
packing follows (codingrules 8.9), except that the hit which does not fit may still go in with its
excerpt shortened, when enough of it survives to judge the hit by.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    retrieval side. Called by `hivemind.honey_store.honey.retrieve.HoneyRetriever.search` after
    ranking; calls into `waggle.messages.honey` (HoneyHit) only. Its result becomes
    `HoneyResponse.hits`, `.token_count` and, together with ranking's own cut, `.is_truncated`.

Key invariants:
    - `pack_hits` never returns hits whose `hit_tokens` sum past `max_tokens` (property-tested).
    - Hits keep their ranked order; a hit is only ever shortened (its excerpt, never its title or
      reference), never reordered or split.
    - `PackResult.is_truncated` is True exactly when a hit was shortened or left out.

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for "the rest are packed by score into
      the caller's token budget".
    - hivemind.memory.counter for the chars-per-token estimate this mirrors.
    - hivemind.honey_store.honey.retrieve for the one caller.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from waggle.messages.honey import HoneyHit

CHARS_PER_TOKEN = 4  # The common rule of thumb, as hivemind.memory.counter's own estimate uses.
TOKEN_MARGIN = 4  # Per text: ids, paths and hashes tokenise finer than prose's four characters.
HIT_METADATA_TOKENS = 48  # A reader renders scope, clearance, three provenance ids, a timestamp and
# the score beside every excerpt: about 190 characters, whatever the hit says.
MIN_EXCERPT_CHARS = 200  # A few sentences: any less and a shortened hit is not worth its tokens.
TRUNCATION_MARK = " [...]"  # Ends a shortened excerpt, so the reader knows the passage goes on.

__all__ = [
    "CHARS_PER_TOKEN",
    "HIT_METADATA_TOKENS",
    "MIN_EXCERPT_CHARS",
    "TOKEN_MARGIN",
    "TRUNCATION_MARK",
    "PackResult",
    "estimate_tokens",
    "hit_tokens",
    "pack_hits",
]


@dataclass(frozen=True, slots=True)
class PackResult:
    """The hits that fit a budget, what they cost, and whether anything was cut to make them."""

    hits: tuple[HoneyHit, ...]  # In ranked order; the last one may carry a shortened excerpt.
    token_count: int  # The sum of `hit_tokens` over `hits`; never above the budget.
    is_truncated: bool  # True when a hit was shortened or left out for the budget.


def estimate_tokens(text: str) -> int:
    """Estimate `text`'s token count: its length over four, rounded up, plus `TOKEN_MARGIN`.

    Args:
        text: Any text a reader's prompt will carry.

    Returns:
        The estimate; `TOKEN_MARGIN` for an empty text.
    """
    return math.ceil(len(text) / CHARS_PER_TOKEN) + TOKEN_MARGIN


def hit_tokens(hit: HoneyHit) -> int:
    """Estimate what one hit costs a reader: its reference, title and excerpt, plus its metadata.

    Args:
        hit: The hit as it will be sent.

    Returns:
        `estimate_tokens` over the reference, title and excerpt, plus `HIT_METADATA_TOKENS`.
    """
    return estimate_tokens(_counted_text(hit)) + HIT_METADATA_TOKENS


def pack_hits(candidates: Sequence[HoneyHit], max_tokens: int) -> PackResult:
    """Pack ranked hits into `max_tokens`, in order, stopping at the first that does not fit.

    The hit that does not fit still goes in, as the last one, when shortening its excerpt to the
    room left keeps at least `MIN_EXCERPT_CHARS` of it; every hit after it is left out either
    way, so a smaller, lower-ranked hit never jumps ahead of a better one.

    Args:
        candidates: Hits in ranked order, best first.
        max_tokens: The budget; `HoneyResponse.token_count` never exceeds it.

    Returns:
        The hits that fit, their total cost, and whether any hit was shortened or left out.
    """
    packed: list[HoneyHit] = []
    total = 0
    # Best first: take each hit whole while it fits; the first that does not ends the pass.
    for hit in candidates:
        cost = hit_tokens(hit)
        if total + cost <= max_tokens:
            packed.append(hit)
            total += cost
            continue
        shortened = _shortened_to_fit(hit, max_tokens - total)
        if shortened is not None:
            # Enough of the excerpt survives to judge the hit by: it goes in as the last hit.
            packed.append(shortened)
            total += hit_tokens(shortened)
        return PackResult(hits=tuple(packed), token_count=total, is_truncated=True)
    return PackResult(hits=tuple(packed), token_count=total, is_truncated=False)


def _counted_text(hit: HoneyHit) -> str:
    """Join the parts of a hit whose length varies: its reference, title and excerpt."""
    return f"{hit.honey_ref} {hit.title} {hit.excerpt}"


def _shortened_to_fit(hit: HoneyHit, room: int) -> HoneyHit | None:
    """Return `hit` with its excerpt cut to fit `room` tokens, or None when too little survives.

    Args:
        hit: A hit whose whole cost is over `room`.
        room: The tokens left in the budget.

    Returns:
        A copy of `hit` whose `hit_tokens` is at most `room`, its excerpt cut and ending in
        `TRUNCATION_MARK`; None when fewer than `MIN_EXCERPT_CHARS` of the excerpt would remain.
    """
    # Invert estimate_tokens: the longest counted text whose estimate plus metadata fits `room`.
    max_chars = (room - TOKEN_MARGIN - HIT_METADATA_TOKENS) * CHARS_PER_TOKEN
    fixed_chars = len(_counted_text(hit)) - len(hit.excerpt)
    keep = max_chars - fixed_chars - len(TRUNCATION_MARK)
    if keep < MIN_EXCERPT_CHARS:
        return None  # Too little would survive to judge the hit by; leave it out whole.
    return hit.model_copy(update={"excerpt": hit.excerpt[:keep] + TRUNCATION_MARK})
