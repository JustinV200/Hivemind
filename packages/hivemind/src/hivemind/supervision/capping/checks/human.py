"""Define HumanCheck: raise a Question for an ASK-verdict leaving and resolve it (roadmap 5.0d).

Roadmap step 5.0d: "supervision/capping/checks/human.py: HumanCheck raises a Question up the
existing chain (3.19 to 3.21) with closed options, *keep*, *keep for this whole goal*, *discard*,
and the task blocks until it is answered." `HumanCheck.ask` builds that `waggle.messages.
supervision.Question` -- its `options` are exactly the three closed choices, already representable
on the wire with no change (`Question.options: tuple[str, ...]`, roadmap step 5.0d's own note that
"closed options may already exist") -- and awaits an injected `Asker` (`hivemind.supervision.
capping.leave.Asker`, the same seam `hivemind.workers.tools.ask.ask` uses through `ctx.asker`, so
this rung travels the identical Worker -> Warden -> Queen -> human chain), racing it against a
`waggle.clock.Clock`-driven timeout: past it, "an unanswered question... means *discard*, never a
failed task" (roadmap step 5.0d, verbatim). `HumanCheck.resolve` is the other half: only called
when `hivemind.supervision.capping.leave.decide_persist` already produced an ASK verdict, it turns
the human's `LeaveHumanVerdict` into a `LeavePersistDecision` -- KEEP or KEEP_FOR_GOAL both persist
with `approved_by=HUMAN` (roadmap step 5.0d: "Only an Answer with source = HUMAN approves"); the
"remembered per goal and Cell so one goal asks once" half of KEEP_FOR_GOAL is the Queen's own job
(`hivemind.queen`, a higher layer this package never imports): from this rung's own point of view,
every ASK still raises a Question every time, and a Queen that already holds a "keep for this whole
goal" answer simply answers it herself, still tagged `AnswerSource.HUMAN` (roadmap step 5.0d: "that
remembered answer must still count as the human's"), so `HumanCheck` needs no special case for it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    checks`. Called by `hivemind.supervision.capping.apply._apply_diff`, never through the generic
    `checks_for`/`_run_checks` ladder (roadmap step 5.0d: "it only exists when the policy said ASK
    -- it is not a per-tier ladder entry that runs on every proposal"). Calls into `hivemind.
    common.tasks` (reap), `hivemind.supervision.capping.leave` (Asker, LeaveApplyContext,
    LeaveHumanVerdict, LeavePersistDecision, LeaveVerdict), `hivemind.supervision.capping.proposal`
    (Proposal) and waggle only.

Key invariants:
    - `ask` never raises for an unanswered or wrongly-sourced Answer: both resolve to
      `LeaveHumanVerdict.DISCARD`, the same "fail closed, never fail the task" shape roadmap step
      5.0d asks for.
    - Only `AnswerSource.HUMAN` ever approves (roadmap step 5.0d: "a Queen or Warden answer is
      refused"); `_verdict_for` checks this before reading `chosen_option` at all.
    - `resolve` is a no-op (returns `decision` unchanged) whenever `decision.record` is None or its
      verdict is not ASK: it never turns a DENY or ALLOW into something else, and never runs a
      HumanCheck for a proposal the leave policy already settled without asking.

See Also:
    - .claude/roadmap.md step 5.0d for this module's own deliverable, verbatim.
    - .claude/roadmap.md steps 3.19-3.21 for the existing Question/Answer chain this rung reuses.
    - hivemind.supervision.capping.leave for Asker, LeaveApplyContext, LeaveHumanVerdict,
      LeavePersistDecision and LeaveVerdict.
    - hivemind.workers.tools.ask for the sibling use of the same asker/Question/Answer chain.
    - hivemind.common.tasks for reap, the owned-task discard rule this module's timeout race uses.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Final

from hivemind.cell.leavings import ApprovedBy
from hivemind.common.tasks import reap
from hivemind.supervision.capping.leave import (
    Asker,
    LeaveApplyContext,
    LeaveHumanVerdict,
    LeavePersistDecision,
    LeaveVerdict,
)
from hivemind.supervision.capping.proposal import Proposal
from waggle.clock import Clock
from waggle.ids import new_message_id
from waggle.messages.supervision import Answer, AnswerSource, Question

KEEP_OPTION = "keep"  # Question.options[0]
KEEP_FOR_GOAL_OPTION = "keep for this whole goal"  # Question.options[1]
DISCARD_OPTION = "discard"  # Question.options[2]
# Index-matched to LEAVE_QUESTION_OPTIONS: Answer.chosen_option selects into this tuple.
LEAVE_QUESTION_OPTIONS: Final = (KEEP_OPTION, KEEP_FOR_GOAL_OPTION, DISCARD_OPTION)
_OPTION_VERDICTS: Final = (
    LeaveHumanVerdict.KEEP,
    LeaveHumanVerdict.KEEP_FOR_GOAL,
    LeaveHumanVerdict.DISCARD,
)

__all__ = [
    "DISCARD_OPTION",
    "KEEP_FOR_GOAL_OPTION",
    "KEEP_OPTION",
    "LEAVE_QUESTION_OPTIONS",
    "HumanCheck",
]


class HumanCheck:
    """Ask a human to keep or discard an ASK-verdict leaving, and resolve the answer."""

    def __init__(self, clock: Clock, timeout_s: float) -> None:
        """Build a HumanCheck bound to one gate's own Clock and timeout.

        Args:
            clock: Source of the Question's own id/timestamp and the timeout race; injected so a
                test drives the timeout deterministically (`waggle.clock.FakeClock.advance`).
            timeout_s: Seconds to wait for an Answer before treating this as discard.
        """
        self._clock = clock
        self._timeout_s = timeout_s

    async def ask(
        self, asker: Asker, proposal: Proposal, path: str, reason: str
    ) -> LeaveHumanVerdict:
        """Raise a closed-option Question for `path` and await its Answer, or time out to discard.

        Args:
            asker: Sends the Question up the Worker -> Warden -> Queen -> human chain.
            proposal: Supplies `proposer` (`Question.asked_by`), `task_id` and `clearance`
                (converted to wire form here, since `Proposal.clearance` is the hivemind mirror).
            path: The resolved path a human is being asked to keep or discard.
            reason: The task's own declared reason this path might stay (never file contents).

        Returns:
            KEEP, KEEP_FOR_GOAL or DISCARD; DISCARD for a timeout, an unanswered question, or an
            Answer whose `source` is not HUMAN (module docstring's own "Key invariants").
        """
        question = Question(
            question_id=new_message_id(self._clock),
            task_id=proposal.task_id,
            asked_by=proposal.proposer,
            text=f"Keep {path!r} once this task's lease is released? {reason}",
            options=LEAVE_QUESTION_OPTIONS,
            clearance=proposal.clearance.to_wire(),
            asked_at=self._clock.now(),
        )
        # External await: blocks on a human (or the Queen, answering from memory) reporting back
        # through the Warden; bounded by the timeout race below, so this can never hang forever.
        ask_task: asyncio.Task[Answer] = asyncio.ensure_future(asker.ask(question))
        timeout_task = asyncio.ensure_future(self._clock.sleep(self._timeout_s))
        done, _pending = await asyncio.wait(
            {ask_task, timeout_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if ask_task not in done:
            # Past its timeout: discard, never a failed task (roadmap step 5.0d, verbatim).
            await reap(ask_task)
            await reap(timeout_task)
            return LeaveHumanVerdict.DISCARD
        await reap(timeout_task)
        return _verdict_for(ask_task.result())

    async def resolve(
        self, leave: LeaveApplyContext, decision: LeavePersistDecision, proposal: Proposal
    ) -> LeavePersistDecision:
        """Turn an ASK-verdict LeavePersistDecision into a human-resolved one.

        A no-op for anything but an ASK verdict with an asker wired (module docstring's own "Key
        invariants"): `hivemind.supervision.capping.apply` calls this unconditionally after
        `decide_persist`, so this method itself is where "only ASK ever asks" is enforced.

        Args:
            leave: This call's own LeaveApplyContext; `.asker`/`.clock` must be set for this to do
                anything (`hivemind.supervision.capping.leave.with_asker`).
            decision: `decide_persist`'s own output for this path.
            proposal: The proposal this path belongs to (`ask`'s own second argument).

        Returns:
            `decision` unchanged when there is nothing to ask about; otherwise a new
            LeavePersistDecision reflecting the human's answer, with `record.human_answer` set.
        """
        if decision.record is None or decision.record.verdict is not LeaveVerdict.ASK:
            return decision
        if leave.asker is None:
            return decision  # No HUMAN rung reachable this call: stays persist=False (5.0c).
        verdict = await self.ask(
            leave.asker, proposal, decision.record.path, decision.record.reason
        )
        record = dataclasses.replace(
            decision.record,
            human_answer=verdict,
            persisted=verdict is not LeaveHumanVerdict.DISCARD,
        )
        if verdict is LeaveHumanVerdict.DISCARD:
            return LeavePersistDecision(persist=False, approved_by=None, reason=None, record=record)
        return LeavePersistDecision(
            persist=True, approved_by=ApprovedBy.HUMAN, reason=decision.record.reason, record=record
        )


def _verdict_for(answer: Answer) -> LeaveHumanVerdict:
    """Map one Answer onto LeaveHumanVerdict, refusing anything not sourced from a human."""
    if answer.source is not AnswerSource.HUMAN:
        # Roadmap step 5.0d: "a Queen or Warden answer is refused" -- only a HUMAN answer approves.
        return LeaveHumanVerdict.DISCARD
    if answer.chosen_option is None or answer.chosen_option >= len(_OPTION_VERDICTS):
        return LeaveHumanVerdict.DISCARD
    return _OPTION_VERDICTS[answer.chosen_option]
