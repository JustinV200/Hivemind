"""Decide what a Warden's Queen link does with each message while the link is down: pure data.

A Warden (the always-on supervisor of one Cell, a unit of compute) that dials the Queen (the Hive's
central orchestrator) loses its link now and then, and codingrules 8.8 says it keeps working within
what it owns: results and Alarms are not lost, a human-bound question waits for the Queen, and
nothing that would make the Warden grow is allowed. This module is that rule as one table, so it
can be tested row by row and read in one place (ADR-0053). `decide_offline` maps a message to one
of four actions: QUEUE it in the durable outbox and send it, in order, once the link is back; HOLD
it in memory and send it after the reconnection (a `supervision.question` is for a human, whom only
the Queen may ask, and its sub-bee stays blocked meanwhile); REFUSE it by raising a typed error the
sender handles (a `cell.request`: an offline Warden cannot get a new Cell); or DROP it, because the
news is stale by the time the link returns (a heartbeat, or the answer to an order the Queen has
given up on once her deadline passed). Anything the table does not name is
REFUSED, so a new kind of message behaves exactly as it does on a dead link until someone decides
otherwise.

The same table also says which frames a Warden sends again after a link it did not know was dead
(`must_arrive`). A cable pulled out from under a device leaves the connection looking alive for
about forty seconds, and everything sent in that time returns as sent and is lost; a frame the
Warden would have queued or held had it known the link was down is exactly a frame it must not
let a silent one swallow. So there is no second list: what the table would keep for the outage it
saw, the link sends again after the one it did not (ADR-0053).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline sub-package. Read by `hivemind.wardens.offline.link.ReconnectingQueenLink` for every
    send it cannot make live and for every frame it puts on a live connection (which of them it
    remembers to send again), and by `hivemind.wardens.offline.outbox` to decide which queued
    messages need a fresh envelope on replay. Calls into waggle only.

Key invariants:
    - Pure: no I/O, no clock, no state; the same message always gets the same action.
    - The table names payload types, never kind strings, and never a Cell's kind.
    - A `task.progress` is queued only at a checkpoint or a pause (the two stages that carry what
      the Queen needs to resume); every other stage is dropped, because it is only a status.
    - A frame is sent again after an unnoticed loss exactly when it would have been queued or held
      in a noticed one: heartbeats, pings, statuses and the answer to a lapsed order are never,
      and neither is a trail segment (the trail is recovered by rewinding its sync, not by frame).
    - `OfflineRefusedError` is a `ConnectionLostError`, so every send already guarded against a
      dead link (`hivemind.wardens.links.send_guarded`) treats a refusal as one, and a sender that
      cares can catch the narrower type.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the rules this table holds.
    - hivemind.wardens.offline.link for the link that applies it.
    - .claude/codingrules.md sections 8.8 and 15 for "a disconnected Warden keeps working" and
      "an offline Warden cannot grow".
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from waggle.errors import ConnectionLostError
from waggle.messages.base import MessageShape, WaggleMessage
from waggle.messages.cell import CellRequest, LeaseReleased
from waggle.messages.cell.status import CellHeartbeat
from waggle.messages.forage import ForageRequest
from waggle.messages.honey import NectarDeposit
from waggle.messages.registry import spec_for
from waggle.messages.supervision import AlarmRaised, Heartbeat, Question
from waggle.messages.swarm import NucPromoted
from waggle.messages.task import TaskProgress, TaskResult, TaskStage

__all__ = ["OfflineAction", "OfflineRefusedError", "decide_offline", "must_arrive", "owes_reply"]


class OfflineAction(Enum):
    """What a Warden's link does with one message while it cannot reach the Queen."""

    QUEUE = "QUEUE"  # Keep it durably; send it, in order, once the link is back.
    HOLD = "HOLD"  # Keep it in memory; send it once the link is back and the queue is drained.
    REFUSE = "REFUSE"  # Raise OfflineRefusedError: the sender decides what to do without it.
    DROP = "DROP"  # Forget it: it describes a moment that will be over when the link returns.


class OfflineRefusedError(ConnectionLostError):
    """Raise when a message may not be sent while the Warden is cut off from the Queen.

    A `ConnectionLostError` on purpose (module docstring): the sender sees what it sees on any
    dead link, and one that wants to tell a refusal from a loss catches this narrower type.
    """

    code: ClassVar[str] = "hive.wardens.offline_refused"


# The payload types the table names, each with its action. Results, Alarms, a request for more
# shared Forage, a lease's release and a chunk of Nectar (what a diagnostic Drone found about the
# lost link, ADR-0056, or a Patrol's summary) are all things the Queen must learn eventually; the
# question is the one message that waits for the link instead of the disk.
_BY_TYPE: dict[type[WaggleMessage], OfflineAction] = {
    TaskResult: OfflineAction.QUEUE,
    AlarmRaised: OfflineAction.QUEUE,
    ForageRequest: OfflineAction.QUEUE,
    LeaseReleased: OfflineAction.QUEUE,
    NectarDeposit: OfflineAction.QUEUE,
    Question: OfflineAction.HOLD,
    CellRequest: OfflineAction.REFUSE,
    Heartbeat: OfflineAction.DROP,
    CellHeartbeat: OfflineAction.DROP,
    # The answer to a promotion order: the Queen waits a bounded time for it, so one that could not
    # be sent in time describes a move she has already undone.
    NucPromoted: OfflineAction.DROP,
}

# The task.progress stages worth keeping: each carries (or follows) a Handoff the Queen resumes
# the task from. STARTED, WORKING and RESUMED only describe a moment that has passed.
_KEPT_STAGES = frozenset({TaskStage.CHECKPOINTED, TaskStage.PAUSED})


def decide_offline(payload: WaggleMessage) -> OfflineAction:
    """Return what a link that cannot reach the Queen does with `payload`.

    Args:
        payload: The message the Warden is sending.

    Returns:
        The table's action for its type; REFUSE for a type the table does not name.
    """
    # A progress report is the one type whose answer depends on its content: only the stages that
    # carry a Handoff are worth the disk.
    if isinstance(payload, TaskProgress):
        return OfflineAction.QUEUE if payload.stage in _KEPT_STAGES else OfflineAction.DROP
    return _BY_TYPE.get(type(payload), OfflineAction.REFUSE)


def must_arrive(payload: WaggleMessage) -> bool:
    """Return whether a frame carrying `payload` is worth sending again after an unnoticed loss.

    A link that died without the Warden knowing swallows whatever is sent in its last forty
    seconds, and each frame it swallowed is as lost as one the Warden would have queued had it
    known. The answer is therefore the offline table's own: a message it would queue or hold must
    arrive, one it would drop is stale by the time the link returns, and one it would refuse is not
    the Warden's to push through a link it could not have used.

    Args:
        payload: The message a frame was sent with.

    Returns:
        True for a message the table queues or holds (a result, a checkpoint, an Alarm, a Forage
        request, a lease's release, a chunk of Nectar, a question); False for every other.
    """
    return decide_offline(payload) in (OfflineAction.QUEUE, OfflineAction.HOLD)


def owes_reply(kind: str) -> bool:
    """Return whether a message of `kind` is a request, so its sender is owed an answer.

    A queued request goes out on a fresh envelope when it is replayed (the queue's docstring says
    why), and only a request: an event keeps the id it was queued with, so the Queen's replay
    window drops it if it had in fact arrived before the link broke.

    Args:
        kind: The envelope's kind string, e.g. `forage.request`.

    Returns:
        True when the registry gives the kind the request shape.

    Raises:
        waggle.errors.UnknownKindError: `kind` is not a registered kind.
    """
    return spec_for(kind).shape is MessageShape.REQUEST
