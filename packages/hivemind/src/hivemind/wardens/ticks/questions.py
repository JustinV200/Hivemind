"""Forward a sub-bee's Question to the Queen, and the Queen's Answer back to that sub-bee.

Roadmap step 3.19's own dispatch map: "Question from a sub-bee -> FORWARD_QUESTION to the Queen,
remembering question_id -> sub-bee; Answer from the Queen -> FORWARD_ANSWER to that sub-bee." Both
messages carry `question_id`, minted by the asking sub-bee and never any envelope's own id
(`waggle.messages.supervision.questions`'s own docstring), so `warden._questions` maps it straight
to the asking `WorkerId`; `warden._question_envelope_ids` remembers the original Question's own
envelope id too, because `waggle.messages.supervision.Answer` is a reply
(`waggle.envelope.MessageShape.REPLY`) and every reply's envelope must carry a `correlation_id` --
the id of *some* request envelope, even though the receiving sub-bee's own `hivemind.workers.
runtime.mailbox.Mailbox.resolve_answer` only ever matches on `answer.question_id` itself.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why). Calls into waggle only.

Key invariants:
    - `forward_answer` is a no-op, not an error, for a `question_id` this Warden never forwarded
      (already answered, or from a sub-bee that has since ended) -- a stray Answer is a peer's
      timing, not this module's contract to enforce, matching `Mailbox.resolve_answer`'s own rule.

See Also:
    - .claude/roadmap.md step 3.19's own dispatch map for the Question/Answer forwarding rule.
    - waggle.messages.supervision.questions for Question, Answer and AnswerSource.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from waggle.envelope import Hop, wrap
from waggle.ids import MessageId, WorkerId
from waggle.messages.supervision import Answer, Question

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["forward_answer", "forward_question"]


async def forward_question(warden: Warden, envelope_id: MessageId, question: Question) -> None:
    """Forward `question` to the Queen, remembering its asker and its own envelope id.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        envelope_id: The envelope id `question` arrived on, kept so `forward_answer`'s own reply
            envelope can correlate to it.
        question: The sub-bee's own Question.
    """
    # A Question's own asker is always a sub-bee (never the Queen itself, a HiveId), so this
    # narrowing is safe: the wire union exists only because the same shape could in principle
    # carry a Warden's own question one level up, which this Warden's own inbox never receives.
    warden._questions[question.question_id] = WorkerId(question.asked_by)
    warden._question_envelope_ids[question.question_id] = envelope_id
    await warden._deps.queen_link.send(wrap(question, warden._deps.hop, clock=warden._deps.clock))


async def forward_answer(warden: Warden, answer: Answer) -> None:
    """Forward the Queen's `answer` back to whichever sub-bee asked the matching question.

    Args:
        warden: The owning Warden.
        answer: The Queen's own Answer.
    """
    worker_id = warden._questions.pop(answer.question_id, None)
    correlation_id = warden._question_envelope_ids.pop(answer.question_id, None)
    if worker_id is None:
        return  # Already answered, or the asking sub-bee has since ended; not this module's fault.
    sub_bee = warden._sub_bees.get(worker_id)
    if sub_bee is None:
        return
    hop = Hop(sender=warden._warden_id, recipient=worker_id, node_id=warden._deps.identity.node_id)
    envelope = wrap(answer, hop, clock=warden._deps.clock, correlation_id=correlation_id)
    await sub_bee.link.send(envelope)
