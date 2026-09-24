"""Tests for hivemind.entrance.auth.challenges: single-use, short-lived, bounded challenges.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/challenges.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.challenges for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from hivemind.entrance.auth import NONCE_BYTES, ChallengeBook, b64url_decode
from hivemind.entrance.errors import ChallengeRejectedError
from waggle.clock import FakeClock

_TTL = timedelta(seconds=60)  # ADR-0033's login challenge lifetime.
_SUBJECT = "a" * 64  # An invite's code hash.
_KEY = "session-binding-key"  # Stands for a browser's session-binding public key.


@pytest.fixture
def clock() -> FakeClock:
    """The book's clock, advanced by the tests."""
    return FakeClock()


@pytest.fixture
def book(clock: FakeClock) -> ChallengeBook:
    """An empty book with the login lifetime."""
    return ChallengeBook(clock, _TTL)


def test_issue_mints_fresh_random_bytes_bound_to_the_subject(
    book: ChallengeBook, clock: FakeClock
) -> None:
    first = book.issue(_SUBJECT)
    second = book.issue(_SUBJECT)

    assert len(first.challenge_bytes) == NONCE_BYTES
    assert b64url_decode(first.nonce) == first.challenge_bytes
    assert first.nonce != second.nonce
    assert (first.subject, first.binding_key) == (_SUBJECT, None)
    assert first.expires_at == clock.now() + _TTL
    assert len(book) == 2


def test_a_challenge_is_taken_exactly_once(book: ChallengeBook) -> None:
    challenge = book.issue(_SUBJECT)

    taken = book.take(challenge.nonce, _SUBJECT)

    assert taken == challenge
    assert len(book) == 0
    with pytest.raises(ChallengeRejectedError):
        book.take(challenge.nonce, _SUBJECT)


def test_an_unknown_nonce_is_refused(book: ChallengeBook) -> None:
    book.issue(_SUBJECT)

    with pytest.raises(ChallengeRejectedError):
        book.take("A" * 43, _SUBJECT)
    assert len(book) == 1


def test_an_answer_for_another_subject_is_refused_and_spends_the_challenge(
    book: ChallengeBook,
) -> None:
    challenge = book.issue(_SUBJECT)

    with pytest.raises(ChallengeRejectedError):
        book.take(challenge.nonce, "b" * 64)
    with pytest.raises(ChallengeRejectedError):
        book.take(challenge.nonce, _SUBJECT)


@pytest.mark.parametrize(
    ("issued_with", "answered_with"), [(_KEY, None), (None, _KEY), (_KEY, "x")]
)
def test_the_binding_key_must_match_exactly(
    book: ChallengeBook, issued_with: str | None, answered_with: str | None
) -> None:
    challenge = book.issue(_SUBJECT, issued_with)

    with pytest.raises(ChallengeRejectedError):
        book.take(challenge.nonce, _SUBJECT, answered_with)


def test_a_challenge_bound_to_a_key_is_taken_with_that_key(book: ChallengeBook) -> None:
    challenge = book.issue(_SUBJECT, _KEY)

    assert book.take(challenge.nonce, _SUBJECT, _KEY).binding_key == _KEY


def test_an_expired_challenge_is_refused(book: ChallengeBook, clock: FakeClock) -> None:
    challenge = book.issue(_SUBJECT)
    clock.advance(_TTL.total_seconds())

    with pytest.raises(ChallengeRejectedError):
        book.take(challenge.nonce, _SUBJECT)


def test_lapsed_challenges_are_swept_when_the_next_one_is_issued(
    book: ChallengeBook, clock: FakeClock
) -> None:
    book.issue(_SUBJECT)
    book.issue(_SUBJECT)
    clock.advance(_TTL.total_seconds() + 1)

    fresh = book.issue(_SUBJECT)

    assert len(book) == 1
    assert book.take(fresh.nonce, _SUBJECT) == fresh


def test_past_its_capacity_the_book_evicts_the_oldest_challenge(clock: FakeClock) -> None:
    book = ChallengeBook(clock, _TTL, capacity=3)
    oldest = book.issue(_SUBJECT)
    kept = [book.issue(_SUBJECT) for _ in range(3)]

    assert len(book) == 3
    with pytest.raises(ChallengeRejectedError):
        book.take(oldest.nonce, _SUBJECT)
    assert [book.take(challenge.nonce, _SUBJECT) for challenge in kept] == kept


@pytest.mark.parametrize(("ttl", "capacity"), [(timedelta(0), 1), (_TTL, 0)])
def test_a_book_needs_a_positive_lifetime_and_capacity(
    clock: FakeClock, ttl: timedelta, capacity: int
) -> None:
    with pytest.raises(ValueError, match="positive lifetime"):
        ChallengeBook(clock, ttl, capacity)


def test_a_challenge_needs_a_subject(book: ChallengeBook) -> None:
    with pytest.raises(ValueError, match="subject"):
        book.issue("")


def test_every_refusal_reads_the_same(book: ChallengeBook, clock: FakeClock) -> None:
    expired = book.issue(_SUBJECT)
    clock.advance(_TTL.total_seconds())
    wrong_subject = book.issue(_SUBJECT)

    messages = set()
    for nonce, subject in ((expired.nonce, _SUBJECT), (wrong_subject.nonce, "c" * 64)):
        with pytest.raises(ChallengeRejectedError) as excinfo:
            book.take(nonce, subject)
        messages.add(str(excinfo.value))
    assert len(messages) == 1
