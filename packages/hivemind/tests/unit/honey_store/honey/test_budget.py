"""Tests for hivemind.honey_store.honey.budget: token estimates and packing hits into a budget.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/honey/budget.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.honey.budget for the module under test.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from hivemind.honey_store.honey.budget import (
    HIT_METADATA_TOKENS,
    MIN_EXCERPT_CHARS,
    TOKEN_MARGIN,
    TRUNCATION_MARK,
    estimate_tokens,
    hit_tokens,
    pack_hits,
)
from waggle.clock import FakeClock
from waggle.messages.honey import HoneyHit, HoneyProvenance
from waggle.messages.honey.hit import MAX_EXCERPT_CHARS
from waggle.messages.labels import CombShieldLevel, HoneyClearance

_CLOCK = FakeClock()


def _hit(index: int, excerpt_chars: int = 400) -> HoneyHit:
    """Build a valid hit whose reference names `index`, with an excerpt of `excerpt_chars`."""
    return HoneyHit(
        honey_ref=f"/hive/honey_{index:026d}",
        title=f"Finding {index}",
        excerpt="x" * excerpt_chars,
        score=0.5,
        scope="hive",
        clearance=HoneyClearance.C1,
        origin_tier=CombShieldLevel.MEADOW,
        provenance=HoneyProvenance(task_id=None, cell_id=None, bee=None, observed_at=_CLOCK.now()),
    )


def test_estimate_tokens_is_chars_over_four_rounded_up_plus_the_margin() -> None:
    assert estimate_tokens("") == TOKEN_MARGIN
    assert estimate_tokens("abcd") == 1 + TOKEN_MARGIN
    assert estimate_tokens("abcde") == 2 + TOKEN_MARGIN


def test_hit_tokens_counts_reference_title_and_excerpt_plus_metadata() -> None:
    hit = _hit(1)

    expected = estimate_tokens(f"{hit.honey_ref} {hit.title} {hit.excerpt}") + HIT_METADATA_TOKENS

    assert hit_tokens(hit) == expected


def test_pack_hits_keeps_every_hit_that_fits_untruncated() -> None:
    hits = [_hit(index) for index in range(3)]

    result = pack_hits(hits, max_tokens=10_000)

    assert result.hits == tuple(hits)
    assert result.token_count == sum(hit_tokens(hit) for hit in hits)
    assert result.is_truncated is False


def test_pack_hits_stops_at_the_first_hit_that_does_not_fit() -> None:
    # A big second hit, then a tiny third one that would fit on its own: it never jumps ahead.
    first, big, tiny = _hit(1, 100), _hit(2, MAX_EXCERPT_CHARS), _hit(3, 10)
    budget = hit_tokens(first) + hit_tokens(tiny)

    result = pack_hits([first, big, tiny], max_tokens=budget)

    assert result.hits == (first,)
    assert result.is_truncated is True


def test_pack_hits_shortens_the_hit_that_does_not_fit_when_enough_survives() -> None:
    first, second = _hit(1, 100), _hit(2, MAX_EXCERPT_CHARS)
    budget = hit_tokens(first) + hit_tokens(_hit(2, MIN_EXCERPT_CHARS * 2))

    result = pack_hits([first, second], max_tokens=budget)

    assert result.hits[0] == first
    shortened = result.hits[1]
    assert shortened.honey_ref == second.honey_ref
    assert shortened.excerpt.endswith(TRUNCATION_MARK)
    assert len(shortened.excerpt) - len(TRUNCATION_MARK) >= MIN_EXCERPT_CHARS
    assert result.token_count <= budget
    assert result.is_truncated is True


def test_pack_hits_leaves_a_hit_out_when_too_little_of_it_would_survive() -> None:
    first, second = _hit(1, 100), _hit(2, MAX_EXCERPT_CHARS)
    budget = hit_tokens(first) + hit_tokens(_hit(2, MIN_EXCERPT_CHARS // 2))

    result = pack_hits([first, second], max_tokens=budget)

    assert result.hits == (first,)
    assert result.is_truncated is True


def test_pack_hits_of_nothing_is_empty_and_untruncated() -> None:
    result = pack_hits([], max_tokens=100)

    assert result.hits == ()
    assert result.token_count == 0
    assert result.is_truncated is False


@given(
    excerpt_lengths=st.lists(st.integers(min_value=0, max_value=MAX_EXCERPT_CHARS), max_size=12),
    max_tokens=st.integers(min_value=0, max_value=4_000),
)
def test_pack_hits_never_exceeds_the_budget_and_keeps_ranked_order(
    excerpt_lengths: list[int], max_tokens: int
) -> None:
    hits = [_hit(index, length) for index, length in enumerate(excerpt_lengths)]

    result = pack_hits(hits, max_tokens)

    assert result.token_count == sum(hit_tokens(hit) for hit in result.hits)
    assert result.token_count <= max_tokens
    # A prefix of the ranked input, in order; only the last one may have been shortened.
    refs = [hit.honey_ref for hit in result.hits]
    assert refs == [hit.honey_ref for hit in hits[: len(refs)]]
    whole = max(0, len(refs) - 1)
    assert result.hits[:whole] == tuple(hits[:whole])
    assert result.is_truncated == (result.hits != tuple(hits))
