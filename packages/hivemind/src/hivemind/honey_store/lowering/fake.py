"""Provide FakeClearanceJudge: a scripted ClearanceJudge for tests, demos and model-free Hives.

The clearance judge decides whether a Honey Store deposit's text may carry a lower label (ADR-0034:
a Real Cell's floor holds everything it gathered at C2, and an independent judge or the human may
lower what holds nothing personal). `ModelClearanceJudge` asks the JUDGE model slot;
`FakeClearanceJudge` answers from a script instead, the way `hivemind.llm.FakeLLMProvider` answers
from scripted responses, and records every request it was shown so a test can prove what the judge
saw (and what it never did: an id, the Ripener's reason). Like every fake in the Hive it ships in
`src/`, beside the protocol it implements, and is held to the same standard (codingrules 14.4).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.lowering`.
    Used by tests and demo paths wherever a `ClearanceJudge` is injected (the lowering service,
    `HoneyAccess.judge`). Calls into this package's `models` and `hivemind.honey_store.errors`
    only.

Key invariants:
    - Answers come in script order, one per call; an empty script answers `default`, or raises
      `ClearanceJudgeAnswerError` when there is none, exactly as a judge that cannot answer does.
    - A verdict is always stamped with the requested rubric id, like the shipped judge's own.
    - Not safe for concurrent scripting: set the script up before the code under test runs.

See Also:
    - hivemind.honey_store.lowering.judge for ClearanceJudge and ModelClearanceJudge.
    - hivemind.llm.fake for FakeLLMProvider, the scripted fake this mirrors.
"""

from __future__ import annotations

from collections import deque

from hivemind.honey_store.errors import ClearanceJudgeAnswerError
from hivemind.honey_store.lowering.models import (
    ClearanceJudgeRequest,
    ClearanceOutcome,
    ClearanceVerdict,
)

SCRIPTED_REASON = "Scripted by FakeClearanceJudge."  # Every fake verdict's one reason.

# One scripted answer: an outcome to return, or an answer failure to raise.
type ScriptedAnswer = ClearanceOutcome | ClearanceJudgeAnswerError

__all__ = ["SCRIPTED_REASON", "FakeClearanceJudge", "ScriptedAnswer"]


class FakeClearanceJudge:
    """A ClearanceJudge that answers from a script and records every request it is shown.

    Attributes:
        requests: Every request `judge` was called with, in call order.
    """

    def __init__(self, *answers: ScriptedAnswer, default: ClearanceOutcome | None = None) -> None:
        """Queue `answers`, and choose what an empty script answers.

        Args:
            *answers: Answers to give in order, one per call; an error is raised when its turn
                comes, standing in for a judge that could not answer.
            default: What to answer once the script is empty; None raises
                `ClearanceJudgeAnswerError` instead, as a judge that cannot answer would.
        """
        self._script: deque[ScriptedAnswer] = deque(answers)
        self._default = default
        self.requests: list[ClearanceJudgeRequest] = []

    def script(self, *answers: ScriptedAnswer) -> None:
        """Append answers to the script.

        Args:
            *answers: Outcomes to return, or errors to raise, in order, one per call.
        """
        self._script.extend(answers)

    async def judge(self, request: ClearanceJudgeRequest) -> ClearanceVerdict:
        """Record `request` and answer with the next scripted outcome; see `ClearanceJudge.judge`.

        Args:
            request: What the judge is shown.

        Returns:
            The scripted (or default) outcome with `SCRIPTED_REASON`, stamped with the requested
            rubric id.

        Raises:
            ClearanceJudgeAnswerError: The next scripted answer is one, or the script is empty and
                there is no default.
        """
        self.requests.append(request)
        answer = self._next_answer()
        # A scripted failure stands for a judge that could not answer at all.
        if isinstance(answer, ClearanceJudgeAnswerError):
            raise answer
        return ClearanceVerdict(
            outcome=answer, reasons=(SCRIPTED_REASON,), rubric_id=request.rubric_id
        )

    def _next_answer(self) -> ScriptedAnswer:
        """Pop the next scripted answer, else the default, else an answer failure."""
        if self._script:
            return self._script.popleft()
        if self._default is not None:
            return self._default
        return ClearanceJudgeAnswerError("FakeClearanceJudge", "the scripted answers ran out")
