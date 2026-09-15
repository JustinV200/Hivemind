"""Provide FakeJudgeReviewer: an honest, scriptable JudgeReviewer for tests and demo paths.

A hand-written fake that implements `JudgeReviewer` honestly (codingrules section 14.4: "fakes
live in src/ beside their Protocol"), answering from a scripted FIFO queue instead of a real model
call, so a unit test, a `JudgeCheck` contract test, or a demo path can exercise everything above
`hivemind.supervision.capping.checks.judge` with no provider, no `ModelSlot.JUDGE` binding and no
network. Mirrors `hivemind.llm.fake.FakeLLMProvider`'s own shape: `calls` records every
`JudgeRequest` this fake actually saw, in call order, and an empty queue raises a typed error
(`JudgeUnavailableError`) rather than `IndexError`, so a caller that forgot to script enough
verdicts gets the same error shape a real reviewer's own unavailability would produce.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    checks`. Used by tests of `JudgeCheck` and `hivemind.supervision.capping.audit`, and by any
    demo path that wants a `JudgeReviewer` with no model behind it. Calls into `hivemind.
    supervision.capping.checks.judge` and `hivemind.supervision.capping.errors` only.

Key invariants:
    - `calls` records every JudgeRequest this fake actually saw, in call order; never named
      `history`/`messages` (`scripts/check_no_transcripts.py`'s reserved names for a growing
      conversation transcript, which this is not).
    - The scripted queue is FIFO: `script(a, b)` then two calls return `a` then `b`.
    - An empty queue raises `JudgeUnavailableError`, never a bare `IndexError`.

See Also:
    - .claude/codingrules.md section 14.4 for the fakes-over-mocks rule this module follows.
    - hivemind.llm.fake for FakeLLMProvider, the sibling shape this module's `calls` follows.
    - hivemind.supervision.capping.checks.judge for the JudgeReviewer protocol this class
      implements, and JudgeRequest/JudgeVerdict, the values it records and returns.
    - hivemind.supervision.capping.errors for JudgeUnavailableError, raised on an empty queue.
"""

from __future__ import annotations

from collections import deque

from hivemind.supervision.capping.checks.judge import JudgeRequest, JudgeVerdict
from hivemind.supervision.capping.errors import JudgeUnavailableError

__all__ = ["FakeJudgeReviewer"]


class FakeJudgeReviewer:
    """A JudgeReviewer that answers from a scripted FIFO queue instead of a model call."""

    def __init__(self, *verdicts: JudgeVerdict) -> None:
        """Build a FakeJudgeReviewer, optionally pre-scripted with `verdicts`.

        Args:
            *verdicts: Verdicts to answer with, in order; more may be queued later via `script`.
        """
        self._verdicts: deque[JudgeVerdict] = deque(verdicts)
        self.calls: list[JudgeRequest] = []

    def script(self, *verdicts: JudgeVerdict) -> None:
        """Append `verdicts` to the FIFO queue `review` answers from.

        Args:
            *verdicts: Verdicts to answer with, after whatever is already queued.
        """
        self._verdicts.extend(verdicts)

    async def review(self, request: JudgeRequest) -> JudgeVerdict:
        """Record `request` on `calls` and pop the next scripted verdict.

        Args:
            request: The review request; recorded, never inspected to choose an answer.

        Returns:
            The next scripted JudgeVerdict.

        Raises:
            JudgeUnavailableError: Nothing is left in the scripted queue.
        """
        self.calls.append(request)
        if not self._verdicts:
            # A caller that forgot to script enough verdicts gets the same error shape a real
            # reviewer's own outage would produce, never a bare IndexError.
            raise JudgeUnavailableError()
        return self._verdicts.popleft()
