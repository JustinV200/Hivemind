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

Roadmap step 10.3 (ADR-0039): forwarding is the `question_routing` enforcement point, the Worker
at the Warden -- a Question goes up the chain toward the human only when the asking sub-bee's own
set holds `question:human`, checked through the Guard's `Enforcer`. A refused Question is answered
straight back down the sub-bee's own link as a WARDEN Answer naming the reason (already a
`guard.denied` row), so the asker unblocks instead of waiting on a human who will never see it.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why). Calls into `hivemind.guard` (the question_routing
    point) and waggle only.

Key invariants:
    - Nothing reaches the Queen from a sub-bee whose set does not hold `question:human`, and a
      refused asker is always answered (when it is still attached) rather than left blocked.
    - `forward_answer` is a no-op, not an error, for a `question_id` this Warden never forwarded
      (already answered, or from a sub-bee that has since ended) -- a stray Answer is a peer's
      timing, not this module's contract to enforce, matching `Mailbox.resolve_answer`'s own rule.

See Also:
    - .claude/roadmap.md step 3.19's own dispatch map for the Question/Answer forwarding rule.
    - waggle.messages.supervision.questions for Question, Answer and AnswerSource.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    worker_principal,
)
from hivemind.wardens.links import send_guarded
from waggle.envelope import Hop, wrap
from waggle.ids import MessageId, WorkerId
from waggle.messages.supervision import Answer, AnswerSource, Question
from waggle.messages.supervision.questions import MAX_TEXT_CHARS
from waggle.messages.task import WorkerRole

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

_QUESTION_HUMAN = Capability(family=CapabilityFamily.QUESTION_HUMAN)  # Routing up to the human.

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
    asker = WorkerId(question.asked_by)
    refusal = await _routing_refusal(warden, asker)
    if refusal is not None:
        await _answer_refused(warden, asker, envelope_id, question, refusal)
        return
    warden._questions[question.question_id] = asker
    warden._question_envelope_ids[question.question_id] = envelope_id
    # A Queen this Warden cannot reach never sees the question at all; codingrules 8.8's own
    # outbox for a disconnected Warden is not wired for this yet (this dispatch's own report), so
    # the asking sub-bee simply waits out its own timeout rather than this tick ever crashing.
    await send_guarded(
        warden._deps.queen_link, wrap(question, warden._deps.hop, clock=warden._deps.clock)
    )


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
    # The sub-bee's own link is gone: it is ending (or already ended) and nobody is left to read
    # the answer, the same benign race hivemind.queen.ticks.honey._reply already documents.
    await send_guarded(sub_bee.link, envelope)


async def _routing_refusal(warden: Warden, asker: WorkerId) -> str | None:
    """Pass the `question_routing` point for `asker`; return the refusal's reason, or None.

    An asker this Warden no longer supervises holds nothing it can vouch for, so it is refused.
    """
    sub_bee = warden._sub_bees.get(asker)
    role = sub_bee.assignment.role if sub_bee is not None else WorkerRole.DRONE
    cell = warden._cell
    decision = await warden._deps.enforcer.check(
        PolicyRequest(
            principal=worker_principal(asker, role),
            point=EnforcementPoint.QUESTION_ROUTING,
            needed=_QUESTION_HUMAN,
            held=sub_bee.capabilities if sub_bee is not None else CapabilitySet.empty(),
            context=PolicyContext(
                comb_shield=cell.comb_shield if cell is not None else None,
                access_level=cell.access_level if cell is not None else None,
            ),
        )
    )
    return None if decision.allowed else decision.reason


async def _answer_refused(
    warden: Warden, asker: WorkerId, envelope_id: MessageId, question: Question, reason: str
) -> None:
    """Answer a refused Question straight back to its asker, as this Warden, naming the reason."""
    sub_bee = warden._sub_bees.get(asker)
    if sub_bee is None:
        return  # Nobody left to unblock; the refusal itself is already on the trail.
    answer = Answer(
        question_id=question.question_id,
        task_id=question.task_id,
        text=f"This question was not routed to the human: {reason}"[:MAX_TEXT_CHARS],
        chosen_option=None,
        source=AnswerSource.WARDEN,
        clearance=question.clearance,
    )
    hop = Hop(sender=warden._warden_id, recipient=asker, node_id=warden._deps.identity.node_id)
    await send_guarded(
        sub_bee.link, wrap(answer, hop, clock=warden._deps.clock, correlation_id=envelope_id)
    )
