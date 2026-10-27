"""Remember which Alarms and questions the Queen handled, so a resend after a restart is dropped.

A Warden whose link died unnoticed sends again what may have been swallowed, under the same
envelope ids, and the transport's replay window drops whatever the Queen already had (phase 11,
wave 4G). That window lives in her process, so after a restart it starts empty and an Alarm or a
question sent again would be handled twice (results and Nectar are idempotent; these two are not).
This module keeps the durable half (ADR-0062). The tick notes each Alarm and question it handled
from a Warden (`note_handled`), at the moment it finished handling it, never on receipt: a frame
noted on receipt and lost in a crash before her tick reached it would be dropped as a replay and
never handled at all. A question the human must answer also keeps the wire ids its answer is
forwarded with (`note_question`), which the Queen otherwise holds only in memory.
`restore_handled` reads the rows still inside the replay window back at her start: their pairs
become the book's `replayed` set, which `is_replay` checks before an item is decided. It also puts
back the wiring of every question still open, however long ago it was asked (a human may answer
days later), so its answer reaches the Warden that asked. `prune_handled`, on each snapshot,
forgets the plain rows older than the window and the wiring of every question since closed.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    requeening sub-package. Called by `hivemind.queen.queen`'s tick (`is_replay`,
    `note_handled`), `hivemind.queen.questions.block_on_question` (`note_question`) and
    `.recover` (`restore_handled`) and `.snapshot` (`prune_handled`). Calls into
    `hivemind.brood_chamber` (through the deps), `hivemind.queen.requeening.models`,
    `hivemind.supervision.attendant` and waggle only; reaches into the Queen's own question
    bookkeeping, as `hivemind.queen.questions` does.

Key invariants:
    - Only an ALARM or QUESTION item whose principal is a Warden is ever noted or dropped.
    - A pair handled in this process is never in `replayed`: only `restore_handled` fills it.
    - Rows older than the transport's replay window are never read back as replays: nothing
      older is sent again (the Warden re-sends only what it sent less than nine minutes before
      its redial).
    - A question's wiring is kept, and restored, for as long as the question is still ASKED.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md, "The replay window survives".
    - waggle.transport.replay for the window this one outlives.
    - hivemind.queen.questions for the wiring restored here.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from hivemind.queen.requeening.models import HandledEnvelope, QuestionWiring
from hivemind.supervision.attendant import InboxItem, InboxKind
from waggle.errors import InvalidIdError
from waggle.ids import IdKind, MessageId, TaskId, WardenId, parse_id
from waggle.transport import REPLAY_WINDOW_S

if TYPE_CHECKING:
    # Types only: `hivemind.queen.deps` imports this sub-package (`QueenDeps.requeening`), and
    # the Queen imports it too, so neither may be a real import here.
    from hivemind.queen.deps import QueenDeps
    from hivemind.queen.queen import Queen

_NOTED_KINDS = (InboxKind.ALARM, InboxKind.QUESTION)  # The two kinds a resend would repeat.

__all__ = ["is_replay", "note_handled", "note_question", "prune_handled", "restore_handled"]


def is_replay(deps: QueenDeps, item: InboxItem) -> bool:
    """Return whether `item` is an Alarm or question she handled before her restart.

    Args:
        deps: The Queen's collaborators; `requeening.replayed` is read.
        item: One item drained off a Warden's link, about to be decided.

    Returns:
        True when its (Warden, envelope id) pair was handled in her previous life: the caller
        drops it unhandled, as the transport's window would have.
    """
    return item.kind in _NOTED_KINDS and (item.principal, item.id) in deps.requeening.replayed


async def note_handled(deps: QueenDeps, item: InboxItem) -> None:
    """Note that she has handled `item`, when it is a Warden's Alarm or question.

    Args:
        deps: The Queen's collaborators; `requeening.store` is written.
        item: The item just decided and acted on.
    """
    if item.kind not in _NOTED_KINDS or not _is_warden(item.principal):
        return  # A human's message, a Guard request, or anything a resend never repeats.
    row = HandledEnvelope(
        principal=item.principal, envelope_id=item.id, handled_at=deps.clock.now()
    )
    # Latency: one local SQLite write; a failure raises into the tick like any store error.
    await deps.requeening.store.note_handled(row)


async def note_question(
    deps: QueenDeps, warden_id: WardenId, envelope_id: MessageId, wiring: QuestionWiring
) -> None:
    """Note a question she put to the human, with the wire ids its answer needs.

    Args:
        deps: The Queen's collaborators; `requeening.store` is written.
        warden_id: The Warden whose link the question arrived on.
        envelope_id: The question's envelope id: the answer's `correlation_id`.
        wiring: The chamber's id for it, the asking Warden's, and its task.
    """
    row = HandledEnvelope(
        principal=warden_id,
        envelope_id=envelope_id,
        handled_at=deps.clock.now(),
        question=wiring,
    )
    await deps.requeening.store.note_handled(row)


async def restore_handled(queen: Queen) -> int:
    """Read back what she handled inside the replay window, and every open question's wiring.

    Args:
        queen: The Queen coming up; her book's `replayed` and her question bookkeeping are set.

    Returns:
        How many still open questions had their wiring put back.
    """
    deps = queen._deps
    since = deps.clock.now() - timedelta(seconds=REPLAY_WINDOW_S)
    rows = await deps.requeening.store.handled_since(since)
    deps.requeening.replayed = frozenset((row.principal, row.envelope_id) for row in rows)
    still_open, _closed = await _sort_wired(deps)
    for row, wiring in still_open:
        chamber_id = MessageId(wiring.chamber_question_id)
        queen._open_questions[MessageId(wiring.wire_question_id)] = TaskId(wiring.task_id)
        queen._question_wire_ids[chamber_id] = MessageId(wiring.wire_question_id)
        queen._question_envelope_ids[chamber_id] = MessageId(row.envelope_id)
    return len(still_open)


async def prune_handled(deps: QueenDeps) -> int:
    """Forget the plain rows older than the replay window and the wiring of closed questions.

    Args:
        deps: The Queen's collaborators; `requeening.store` is written.

    Returns:
        How many rows were forgotten.
    """
    store = deps.requeening.store
    pruned = await store.prune_handled(deps.clock.now() - timedelta(seconds=REPLAY_WINDOW_S))
    _open, closed = await _sort_wired(deps)
    return pruned + await store.forget_handled([(r.principal, r.envelope_id) for r in closed])


async def _sort_wired(
    deps: QueenDeps,
) -> tuple[list[tuple[HandledEnvelope, QuestionWiring]], list[HandledEnvelope]]:
    """Split the wired rows into the still ASKED (with their wiring) and the since closed.

    A closed question is one answered or withdrawn since it was noted.
    """
    # The wired rows first: a question noted after this read is simply not in it, while one
    # read after the open ids could look closed and lose its wiring.
    wired = await deps.requeening.store.wired_questions()
    open_ids = {question.id for question in await deps.chamber.pending_questions()}
    still_open: list[tuple[HandledEnvelope, QuestionWiring]] = []
    closed: list[HandledEnvelope] = []
    for row in wired:
        if row.question is not None and row.question.chamber_question_id in open_ids:
            still_open.append((row, row.question))
        else:
            closed.append(row)
    return still_open, closed


def _is_warden(principal: str) -> bool:
    """Whether `principal` is a Warden's id (every item drained off a link names its Warden)."""
    try:
        parse_id(principal, IdKind.WARDEN)
    except InvalidIdError:
        return False
    return True
