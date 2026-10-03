"""Tests for hivemind.honey_store.ripening.embed.backoff: a failing embedder, tried and said less.

A binding that cannot embed used to be called, and warned about, on every ripening pass: every 30
seconds for as long as the Hive ran. The backoff tries it again at a doubling interval up to a
cap, says the first failure in full and a later one only when its cause changes or at the capped
pace, starts over at the first success, and says once, in plain words naming the binding, when the
provider serves no embeddings at all. Time comes from a FakeClock.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/embed/backoff.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.llm import make_bound_embedder
from structlog.testing import capture_logs

from hivemind.honey_store.ripening.embed import (
    FIRST_RETRY_S,
    MAX_RETRY_S,
    MISMATCH,
    EmbedBackoff,
    EmbedFailure,
    no_embeddings,
)
from hivemind.llm import (
    BoundEmbedder,
    EmbeddingUnsupportedError,
    FakeEmbedding,
    LLMError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
)
from waggle.clock import FakeClock

_DOWN = EmbedFailure.of(ProviderUnavailableError("embeds", "connection refused"))
_NO_ROUTE = EmbedFailure.of(ProviderRequestError("embeds", 404, detail="Not Found"))


def _bound() -> BoundEmbedder:
    """An EMBEDDER binding over a fake provider named `embeds`."""
    provider = FakeEmbedding("embeds", clock=FakeClock(), model="test-embed")
    return make_bound_embedder(provider=provider, model="test-embed")


def _waits(backoff: EmbedBackoff, clock: FakeClock, failures: int) -> list[float]:
    """Fail `failures` times in a row, each as soon as allowed; return each wait in turn."""
    waits: list[float] = []
    for _ in range(failures):
        backoff.failed(_bound(), _DOWN)
        waited = 0.0
        # Step a second at a time until the next try is due: the wait the failure set.
        while not backoff.ready():
            clock.advance(1.0)
            waited += 1.0
        waits.append(waited)
    return waits


def test_a_fresh_backoff_holds_no_call_back() -> None:
    assert EmbedBackoff(FakeClock()).ready()


def test_each_failure_in_a_row_doubles_the_wait_up_to_its_cap() -> None:
    clock = FakeClock()

    waits = _waits(EmbedBackoff(clock), clock, 8)

    assert waits == [30.0, 60.0, 120.0, 240.0, 480.0, 900.0, 900.0, 900.0]
    assert (FIRST_RETRY_S, MAX_RETRY_S) == (30.0, 900.0)


def test_a_success_starts_the_waits_over_and_says_the_embedder_is_back() -> None:
    clock = FakeClock()
    backoff = EmbedBackoff(clock)
    _waits(backoff, clock, 3)

    with capture_logs() as logs:
        backoff.succeeded(_bound())

    assert backoff.ready() and backoff.failures == 0
    assert [entry["event"] for entry in logs] == ["honey_store.embed_recovered"]
    assert _waits(backoff, clock, 1) == [30.0]  # From the first wait again.


def test_the_first_failure_is_said_in_full_and_a_repeat_only_when_its_cause_changes() -> None:
    clock = FakeClock()
    backoff = EmbedBackoff(clock)

    with capture_logs() as logs:
        backoff.failed(_bound(), _DOWN)
        backoff.failed(_bound(), _DOWN)  # The same cause again: nothing new to say.
        backoff.failed(_bound(), MISMATCH)  # A new cause is said, with its own detail.

    said = [(entry["event"], entry["error"]) for entry in logs]
    assert said == [
        ("honey_store.embed_failed", _DOWN.cause),
        ("honey_store.embed_failed", "mismatch"),
    ]
    assert logs[0]["detail"] == _DOWN.detail and logs[0]["retry_in_s"] == FIRST_RETRY_S


def test_at_the_capped_wait_each_try_is_said_again() -> None:
    clock = FakeClock()
    backoff = EmbedBackoff(clock)
    _waits(backoff, clock, 5)  # 30, 60, 120, 240, 480: below the cap, said once.

    with capture_logs() as logs:
        _waits(backoff, clock, 2)  # 900 and 900: a binding still broken is still said to be.

    assert [entry["event"] for entry in logs] == ["honey_store.embed_still_failing"] * 2
    assert all(entry["retry_in_s"] == MAX_RETRY_S for entry in logs)


def test_a_provider_with_no_embeddings_is_said_once_in_plain_words_naming_the_binding() -> None:
    clock = FakeClock()
    backoff = EmbedBackoff(clock)
    bound = _bound()

    with capture_logs() as logs:
        for _ in range(8):  # Past the cap too: still said only once.
            backoff.failed(bound, _NO_ROUTE)
            clock.advance(MAX_RETRY_S)

    [said] = logs
    assert said["event"] == "honey_store.embedder_unsupported"
    advice = said["advice"]
    assert f"[llm.slots.{bound.binding}]" in advice and "'embeds'" in advice
    assert "serves no embeddings" in advice and "full-text search only" in advice


@pytest.mark.parametrize(
    ("error", "unsupported"),
    [
        (ProviderRequestError("p", 404, detail="Not Found"), True),
        (ProviderRequestError("p", 405, detail="Method Not Allowed"), True),
        (ProviderRequestError("p", 501, "not_supported_error", "Start it with --embeddings"), True),
        (ProviderRequestError("p", 400, detail="The model does not support Embeddings API"), True),
        (EmbeddingUnsupportedError("p", "anthropic"), True),
        (ProviderRequestError("p", 401, detail="Unauthorized"), False),
        (ProviderRequestError("p", 400, detail="bad input"), False),
        (ProviderUnavailableError("p", "connection refused"), False),
        (RateLimitedError("p", retry_after_s=2.0), False),
    ],
)
def test_no_embeddings_is_a_missing_route_or_feature_never_a_passing_trouble(
    error: LLMError, unsupported: bool
) -> None:
    assert no_embeddings(error) is unsupported
    assert EmbedFailure.of(error).unsupported is unsupported


def test_a_status_tells_two_request_errors_apart() -> None:
    not_found = EmbedFailure.of(ProviderRequestError("p", 404))
    unauthorized = EmbedFailure.of(ProviderRequestError("p", 401))

    assert not_found.cause != unauthorized.cause
    assert not_found.cause.endswith(":404")
