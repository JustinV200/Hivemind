"""Define EmbedBackoff: when a failing EMBEDDER is tried again, and what each failure says.

An EMBEDDER binding that cannot answer (its provider down, a server with no embeddings route, a
model the server does not hold) fails every embedding call ripening makes, on every pass of the
House Bee's loop. Logged each time, that is a warning every `[honey.ripening] interval_s` (30
seconds by default) for as long as the Hive runs: the whole-system run of 2026-10-03 saw exactly
that against a model server with no embeddings route. `EmbedBackoff` keeps one binding's run of
failures between passes: after a failure no call is made until the next try is due, the wait
doubling from `FIRST_RETRY_S` up to `MAX_RETRY_S`, and the first success starts it over. The
first failure is logged in full; a later one only when its cause changes or, once the wait has
reached its cap, at each capped try, so a binding still broken hours later is still said to be.
An answer that the provider serves no embeddings at all (`no_embeddings`: HTTP 404, 405 or 501,
or a "not supported" error) is said once, in plain words naming the `[llm.slots]` binding to fix,
since no wait cures it, only the manifest; it is still tried at the capped pace, so a server
restarted with embeddings is picked up without restarting the Hive.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening.
    embed`. Built once per Hive by the composition root (`hivemind.cli.compose.honey`), carried
    to every ripening pass on `RipenerDeps.embed_backoff`, and consulted by `embed_texts` around
    each set of embedding calls. Calls into `hivemind.llm` (the error tree, BoundEmbedder),
    `hivemind.common.logging` and waggle's clock only.

Key invariants:
    - No embedding call is made before the next try is due; a fresh backoff, and one whose last
      call succeeded, never holds a call back.
    - The wait doubles from `FIRST_RETRY_S` with each failure in a row and never passes
      `MAX_RETRY_S`; the first success starts it over and says the embedder is back.
    - Nothing here holds or logs a text that was being embedded: causes, codes, counts and the
      error's own sentence only.

See Also:
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for "degrade, never fail closed".
    - hivemind.honey_store.ripening.embed.batches for embed_texts, the one caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.common.logging import get_logger
from hivemind.llm import BoundEmbedder, EmbeddingUnsupportedError, LLMError, ProviderRequestError
from waggle.clock import Clock

FIRST_RETRY_S = 30.0  # The House Bee's default pass interval: the first retry is the next pass.
MAX_RETRY_S = 900.0  # Fifteen minutes: a broken binding is tried four times an hour, no more.
# Statuses that mean the server has no such route, method or feature: no wait will change them.
_NO_ROUTE_STATUSES = frozenset({404, 405, 501})
# Words a server's error uses to say it serves no embeddings (llama.cpp's `not_supported_error`
# when started without --embeddings; vLLM's "does not support Embeddings API"), lower case.
_UNSUPPORTED_MARKERS = ("not_supported", "does not support embeddings")

log = get_logger(__name__)

__all__ = [
    "FIRST_RETRY_S",
    "MAX_RETRY_S",
    "MISMATCH",
    "EmbedBackoff",
    "EmbedFailure",
    "no_embeddings",
]


@dataclass(frozen=True, slots=True)
class EmbedFailure:
    """Why one set of embedding calls failed, as the backoff compares and says it.

    Attributes:
        cause: A stable name for the cause: an error's code, with its HTTP status when it has
            one; a change of cause is said again.
        detail: The error's own sentence, never a text that was being embedded.
        unsupported: The provider answered that it serves no embeddings at this binding.
    """

    cause: str
    detail: str
    unsupported: bool = False

    @classmethod
    def of(cls, error: LLMError) -> EmbedFailure:
        """Describe a failed embedding call by the error the gate raised.

        Args:
            error: What the call raised.

        Returns:
            The failure, its cause the same for every repeat of the same error.
        """
        # A status tells a 404 from a 401, which share ProviderRequestError's code.
        status = error.status_code if isinstance(error, ProviderRequestError) else None
        cause = error.code if status is None else f"{error.code}:{status}"
        return cls(cause=cause, detail=str(error), unsupported=no_embeddings(error))


# A reply the binding's own model did not make, or that is short of vectors (ADR-0036).
MISMATCH = EmbedFailure(
    cause="mismatch",
    detail="The reply came from another model, or carried the wrong number of vectors.",
)


def no_embeddings(error: LLMError) -> bool:
    """Return whether `error` says the provider serves no embeddings at all at this binding.

    Args:
        error: What an embedding call raised.

    Returns:
        True for a provider kind with no embeddings, a 404, 405 or 501, or an error whose type or
        sentence says embeddings are not supported; False for anything a wait might cure.
    """
    if isinstance(error, EmbeddingUnsupportedError):
        return True
    if not isinstance(error, ProviderRequestError):
        return False  # Unavailable, rate limited or timed out: another try may well succeed.
    if error.status_code in _NO_ROUTE_STATUSES:
        return True
    said = f"{error.error_type or ''} {error}".lower()
    return any(marker in said for marker in _UNSUPPORTED_MARKERS)


class EmbedBackoff:
    """One EMBEDDER binding's run of failures, kept between ripening passes.

    Owns its own mutable state in place (codingrules section 8.5), in memory only: a restarted
    Hive tries its embedder at once.
    """

    def __init__(
        self, clock: Clock, first_s: float = FIRST_RETRY_S, cap_s: float = MAX_RETRY_S
    ) -> None:
        """Start with no failure, so the first call goes ahead.

        Args:
            clock: Whose `monotonic()` decides when the next try is due.
            first_s: The wait after a first failure; > 0.
            cap_s: The longest wait between tries; >= `first_s`.
        """
        self._clock = clock
        self._first_s = first_s
        self._cap_s = cap_s
        self._failures = 0  # Failed tries in a row; 0 means healthy.
        self._wait_s = 0.0  # The wait the last failure set.
        self._due_at = 0.0  # When the next try is due, on the clock's monotonic scale.
        self._cause: str | None = None  # The last failure's cause, to tell a change.
        self._said_unsupported = False  # The plain-words "no embeddings" line, said once.

    @property
    def failures(self) -> int:
        """How many tries have failed in a row; 0 once one succeeds."""
        return self._failures

    def ready(self) -> bool:
        """Return whether an embedding call may be made now.

        Returns:
            True when healthy, or when the wait the last failure set has passed.
        """
        return self._failures == 0 or self._clock.monotonic() >= self._due_at

    def succeeded(self, bound: BoundEmbedder) -> None:
        """Start the waits over after a call that worked, saying so if it had been failing.

        Args:
            bound: The binding that answered.
        """
        if self._failures:
            log.info("honey_store.embed_recovered", binding=bound.binding, failures=self._failures)
        self._failures, self._wait_s, self._due_at = 0, 0.0, 0.0
        self._cause, self._said_unsupported = None, False

    def failed(self, bound: BoundEmbedder, failure: EmbedFailure) -> None:
        """Note a failed try: set when the next is due, and say what the policy says to say.

        Args:
            bound: The binding that failed.
            failure: Why.
        """
        self._failures += 1
        # Doubling from the first wait, never past the cap.
        self._wait_s = self._first_s if self._failures == 1 else min(self._wait_s * 2, self._cap_s)
        self._due_at = self._clock.monotonic() + self._wait_s
        changed = failure.cause != self._cause
        self._cause = failure.cause
        self._say(bound, failure, changed)

    def _say(self, bound: BoundEmbedder, failure: EmbedFailure, changed: bool) -> None:
        """Log one failure, or nothing, by the policy in the module docstring."""
        if failure.unsupported:
            # Once, in plain words: only the manifest cures it, so repeating it adds nothing.
            if not self._said_unsupported:
                self._said_unsupported = True
                log.warning(
                    "honey_store.embedder_unsupported",
                    binding=bound.binding,
                    advice=_advice(bound, failure, self._cap_s),
                )
            return
        if self._failures == 1 or changed:
            log.warning(
                "honey_store.embed_failed",
                binding=bound.binding,
                error=failure.cause,
                detail=failure.detail,
                failures=self._failures,
                retry_in_s=self._wait_s,
            )
        elif self._wait_s >= self._cap_s:
            # At the capped pace: still failing is worth one line per try, no more often.
            log.warning(
                "honey_store.embed_still_failing",
                binding=bound.binding,
                error=failure.cause,
                failures=self._failures,
                retry_in_s=self._wait_s,
            )


def _advice(bound: BoundEmbedder, failure: EmbedFailure, cap_s: float) -> str:
    """Say in plain words that a binding serves no embeddings, and which one to fix."""
    return (
        f"The EMBEDDER slot's binding [llm.slots.{bound.binding}] (provider "
        f"{bound.provider.name!r}, model {bound.model!r}) answered that it serves no embeddings: "
        f"{failure.detail} Honey is found by full-text search only until that binding names a "
        "provider and model that embed; ripening keeps trying it, waiting up to "
        f"{cap_s / 60:.0f} minutes between tries."
    )
