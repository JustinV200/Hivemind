"""Define the Queen's own "keep for this whole goal" memory for a leave Question (roadmap 5.0d).

Roadmap step 5.0d: "'Keep for this whole goal' is remembered by the Queen per goal and Cell so one
goal asks once, not once per file... The Queen answers a later leave Question for the same goal
and Cell herself from that memory, and that remembered answer must still count as the human's
(record approved_by = HUMAN, and make it auditable on the trail which human answer it derives
from). This is the one case where the Queen relays; a Queen or Warden deciding on her own is
refused." `is_leave_question` recognises one by its own closed `options` (`hivemind.supervision.
capping.checks.human.LEAVE_QUESTION_OPTIONS`) -- no wire change was needed for this dispatch,
since closed options already existed. `remember_if_keep_for_goal` is called once a HUMAN answer's
`chosen_option` is known to be "keep for this whole goal" (`hivemind.queen.questions`'s own two
forwarding paths); `answer_from_memory` is called from `hivemind.queen.queen._act`'s own
BLOCK_ON_QUESTION branch, before a leave Question is ever handed to `chamber.ask` -- so a
remembered goal answers instantly, over the wire, and never appears in `hive inbox` at all.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Calls
    into `hivemind.brood_chamber` (Task), `hivemind.queen.trail` (record_event), `hivemind.
    supervision.capping.checks.human` (LEAVE_QUESTION_OPTIONS, KEEP_FOR_GOAL_OPTION) and waggle
    only.

Key invariants:
    - Only a HUMAN-sourced answer choosing "keep for this whole goal" is ever remembered (roadmap
      step 5.0d: "a Queen or Warden deciding on her own is refused") -- enforced by the callers,
      which only call `remember_if_keep_for_goal` once they have checked both themselves; this
      module trusts that check rather than re-deriving it, to stay a plain dict write.
    - A remembered reuse is always sent back with `AnswerSource.HUMAN` and clearance C2, exactly
      like the original (roadmap step 5.0d: "that remembered answer must still count as the
      human's"); `queen.leave_remembered`'s own payload carries the original wire question id it
      derives from, for the trail (roadmap step 5.0d: "auditable... which human answer it derives
      from").
    - Keyed by `(goal_id, cell_id)`, never by task id: a goal's later subtasks, on the same Cell,
      reuse the same memory (roadmap step 5.0d's own "per goal and Cell").

See Also:
    - .claude/roadmap.md step 5.0d for this module's own deliverable, verbatim.
    - hivemind.queen.questions for answer_question and _forward_from_note, this module's two
      remember_if_keep_for_goal callers.
    - hivemind.queen.queen for _act's BLOCK_ON_QUESTION branch, answer_from_memory's one caller.
    - hivemind.supervision.capping.checks.human for HumanCheck, the rung that raises the Question
      this module may or may not let reach the human.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.brood_chamber import Task
from hivemind.queen.trail import record_event
from hivemind.supervision.capping.checks.human import KEEP_FOR_GOAL_OPTION, LEAVE_QUESTION_OPTIONS
from waggle.envelope import wrap
from waggle.ids import MessageId, WardenId
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Answer as WireAnswer
from waggle.messages.supervision import AnswerSource
from waggle.messages.supervision import Question as WireQuestion

if TYPE_CHECKING:
    # Only for the type hint below: hivemind.queen.questions imports this module at load time, so
    # a real-time import of hivemind.queen.queen here would cycle right back through it.
    from hivemind.queen.queen import Queen

# Question.options[1]: the closed "keep for this whole goal" choice HumanCheck offers (roadmap
# step 5.0d); index-matched, the same way hivemind.supervision.capping.checks.human's own
# _OPTION_VERDICTS is keyed to LEAVE_QUESTION_OPTIONS.
KEEP_FOR_GOAL_INDEX = LEAVE_QUESTION_OPTIONS.index(KEEP_FOR_GOAL_OPTION)
_KEEP_INDEX = 0  # Question.options[0]: what a remembered reuse always answers with.

__all__ = [
    "KEEP_FOR_GOAL_INDEX",
    "LeaveMemoryEntry",
    "answer_from_memory",
    "has_leave_options",
    "is_leave_question",
    "remember_if_keep_for_goal",
]


@dataclass(frozen=True, slots=True)
class LeaveMemoryEntry:
    """One goal+Cell's own remembered "keep for this whole goal" answer."""

    text: str  # The original human's own answer text, echoed on every reuse.
    source_question_id: MessageId  # The original wire Question.question_id this derives from.


def is_leave_question(question: WireQuestion) -> bool:
    """Return whether `question` is a leave-policy ASK Question (roadmap step 5.0d)."""
    return has_leave_options(question.options)


def has_leave_options(options: Sequence[str]) -> bool:
    """Return whether `options` are a leave Question's own closed options, in their own order.

    Shared by the wire Question (`is_leave_question`) and the Brood Chamber's stored one, which
    `hivemind.queen.questions`'s in-process answer path reads before it answers.
    """
    return tuple(options) == LEAVE_QUESTION_OPTIONS


def remember_if_keep_for_goal(queen: Queen, task: Task, question_id: MessageId, text: str) -> None:
    """Remember `text` for `(task.goal_id, task.cell_id)`, once a caller confirms it qualifies.

    Args:
        queen: The Queen whose own `_leave_memory` dict this writes to.
        task: The task the Question belonged to; supplies `goal_id` and `cell_id`.
        question_id: The original wire Question.question_id this answer derives from.
        text: The human's own answer text, echoed on every later reuse.
    """
    if task.cell_id is None:
        return  # Nothing to key memory by; module docstring's own (goal_id, cell_id) invariant.
    queen._leave_memory[(task.goal_id, task.cell_id)] = LeaveMemoryEntry(
        text=text, source_question_id=question_id
    )


async def answer_from_memory(
    queen: Queen, task: Task, warden_id: WardenId, question: WireQuestion, envelope_id: MessageId
) -> bool:
    """Answer `question` immediately from remembered goal+Cell memory, if any.

    Args:
        queen: The Queen whose own `_leave_memory` dict this reads, and whose `wardens` names
            the asking Warden's own link to send the Answer back over.
        task: The task `question` blocks; supplies `goal_id` and `cell_id`.
        warden_id: The asking Warden's own id (the Question's own `hop.sender`, `_act`'s own
            argument); looked up in `queen.wardens` for its link.
        question: The arriving Question; not a leave one (`is_leave_question`) means "no".
        envelope_id: The Question's own arrival envelope id, so the reply Answer (a
            `waggle.envelope.MessageShape.REPLY`) can carry the required `correlation_id`.

    Returns:
        True if answered from memory (the caller must not also hand this Question to the human
        inbox); False when it is not a leave Question, its Warden has since detached, or there is
        no memory yet for `(task.goal_id, task.cell_id)`.
    """
    if task.cell_id is None or not is_leave_question(question):
        return False
    entry = queen._leave_memory.get((task.goal_id, task.cell_id))
    link = next((link for link in queen.wardens if link.warden_id == warden_id), None)
    if entry is None or link is None:
        return False
    answer = WireAnswer(
        question_id=question.question_id,
        task_id=question.task_id,
        text=entry.text,
        chosen_option=_KEEP_INDEX,
        source=AnswerSource.HUMAN,
        clearance=WireHoneyClearance.C2,
    )
    envelope = wrap(answer, link.hop, clock=queen._deps.clock, correlation_id=envelope_id)
    # This path never calls chamber.ask (module docstring: "never appears in hive inbox at
    # all"), so there is no chamber state to reconcile; an unreachable Warden only misses the
    # wire push, the same as every other reply this Queen sends.
    await link.send(envelope)
    await record_event(
        queen._deps,
        "queen.leave_remembered",
        task.id,
        goal_id=task.goal_id,
        cell_id=task.cell_id,
        source_question_id=entry.source_question_id,
    )
    return True
