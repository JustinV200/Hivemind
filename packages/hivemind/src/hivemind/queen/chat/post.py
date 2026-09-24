"""Define the functions that write the chat: the human's messages in, the Queen's lines out.

Docs/adr/0032: the chat is one log both directions append to. `post_message` is the human's half
(`Queen.post_human_message`'s body): a MESSAGE line from a device, committed with its
`queen.human_message_received` event, which the Queen's tick then drains into her inbox. The rest
are the Queen's half, each appended as Monarch and each followed by the matching `HumanChannel`
call so a device that is not looking is told: `post_reply` (an awake REPLY decision's words, with
its `queen.replied` event), `post_question` (a question routed to the human; its own
`task.blocked` event already records it), `post_alarm` (an Alarm that reached the human; its own
`alarm.escalated` event already records it) and `post_notice` (words no decision produced, such
as a spoken goal echoed back). `resolve_alarm` is the human's acknowledgement of an Alarm line:
it resolves the Alarm (`alarm.resolved`), drops it from the human inbox and tells every device.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's chat
    sub-package. Called by `hivemind.queen.chat.door` (the Queen's human-facing methods),
    `hivemind.queen.questions` (a question for the human), `hivemind.queen.ticks.alarms` and
    `.liveness` (an Alarm for the human), `hivemind.queen.ticks.chat` (a reply) and
    `hivemind.queen.ticks.intake` (a notice). Calls into `hivemind.brood_chamber` (Question),
    `hivemind.cell` (CellIdentity), `hivemind.queen.human_inbox`, `hivemind.queen.trail`,
    `hivemind.supervision` (Alarm, record_alarm_event), the chat package's own model and waggle
    only; `QueenDeps` only for its type.

Key invariants:
    - Every line is appended through `deps.chat`; a human message and a reply carry their trail
      event into the same transaction, and no event anywhere carries a line's words.
    - Every Queen line the human must see is followed by exactly one `HumanChannel` call.

See Also:
    - hivemind.queen.chat.protocol for ChatLog, the log these functions append to.
    - hivemind.queen.chat.channel for HumanChannel, the calls that follow each line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.brood_chamber import Question
from hivemind.cell import CellIdentity
from hivemind.queen.chat.model import (
    MAX_CHAT_TEXT_CHARS,
    ChatAuthor,
    ChatEntry,
    ChatKind,
    new_chat_entry_id,
)
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.trail import queen_event
from hivemind.supervision import Alarm, record_alarm_event
from waggle.ids import DeviceId, TaskId

if TYPE_CHECKING:
    # Only for the type hints: hivemind.queen.deps imports this package to name its log, so a
    # real import here would cycle back through queen/deps.py.
    from hivemind.queen.deps import QueenDeps

_MESSAGE_KIND = "queen.human_message_received"  # A human's message entered the Queen's inbox.
_REPLIED_KIND = "queen.replied"  # The Queen answered in the chat.

__all__ = [
    "post_alarm",
    "post_message",
    "post_notice",
    "post_question",
    "post_reply",
    "resolve_alarm",
]


async def post_message(
    deps: QueenDeps, text: str, device_id: DeviceId, task_id: TaskId | None
) -> ChatEntry:
    """Append the human's message, with its `queen.human_message_received` event.

    Args:
        deps: The Queen's collaborators; `chat` is the log written to.
        text: The human's own words; bounded like the wire HumanMessage.
        device_id: The enrolled device the human wrote from.
        task_id: The task the message concerns; None addresses the Queen at large.

    Returns:
        The stored line, its `seq` assigned.

    Raises:
        pydantic.ValidationError: `text` is empty or longer than the chat's own bound.
    """
    entry = ChatEntry(
        id=new_chat_entry_id(deps.clock),
        at=deps.clock.now(),
        author=ChatAuthor.HUMAN,
        kind=ChatKind.MESSAGE,
        text=text,
        task_id=task_id,
        device_id=device_id,
    )
    subject = task_id or deps.identity.hive_id
    event = queen_event(
        deps, _MESSAGE_KIND, subject, chat_entry_id=entry.id, device_id=device_id, task_id=task_id
    )
    return await deps.chat.append(entry, event)


async def post_reply(
    deps: QueenDeps, text: str, *, ref: str | None, task_id: TaskId | None
) -> ChatEntry:
    """Append the Queen's reply with its `queen.replied` event, and tell the human's devices.

    Args:
        deps: The Queen's collaborators.
        text: The words an awake REPLY decision chose.
        ref: The human message it answers, when a message woke the episode; None otherwise.
        task_id: The task the reply concerns, if any.

    Returns:
        The stored line.
    """
    entry = _queen_line(deps, ChatKind.REPLY, text, ref, task_id)
    subject = task_id or deps.identity.hive_id
    event = queen_event(deps, _REPLIED_KIND, subject, chat_entry_id=entry.id, answering=ref)
    stored = await deps.chat.append(entry, event)
    await deps.human_channel.replied(stored)
    return stored


async def post_notice(
    deps: QueenDeps, text: str, *, ref: str | None, task_id: TaskId | None
) -> ChatEntry:
    """Append words from the Queen that no decision produced; the caller tells the channel.

    Args:
        deps: The Queen's collaborators.
        text: What the human should read (a goal echoed back, why a request was refused).
        ref: The goal request or human message it is about, if any.
        task_id: The task it concerns, if any.

    Returns:
        The stored line.
    """
    return await deps.chat.append(_queen_line(deps, ChatKind.NOTICE, text, ref, task_id))


async def post_question(deps: QueenDeps, question: Question) -> ChatEntry:
    """Append a question the Queen routed to the human, and tell the human's devices.

    Args:
        deps: The Queen's collaborators.
        question: The Brood Chamber's own question (its id is what the human answers by).

    Returns:
        The stored line; its `ref` is the question id.
    """
    text = question.text
    if question.options:
        # A closed question lists its options, so the human can see what an answer may choose.
        text = "\n".join((question.text, *(f"- {option}" for option in question.options)))
    entry = _queen_line(deps, ChatKind.QUESTION, text, question.id, question.task_id)
    stored = await deps.chat.append(entry)
    await deps.human_channel.question_asked(stored)
    return stored


async def post_alarm(deps: QueenDeps, alarm: Alarm) -> ChatEntry:
    """Append an Alarm that reached the human, and tell the human's devices.

    Args:
        deps: The Queen's collaborators.
        alarm: The Alarm the chain escalated to its last hop.

    Returns:
        The stored line; its `ref` is the Alarm id the human acknowledges by.
    """
    text = f"Alarm {alarm.kind.value} ({alarm.severity.value}): {alarm.detail}"
    entry = _queen_line(deps, ChatKind.ALARM, text, alarm.id, alarm.context.task_id)
    stored = await deps.chat.append(entry)
    await deps.human_channel.alarm_raised(stored)
    return stored


async def resolve_alarm(deps: QueenDeps, human_inbox: HumanInbox, alarm_id: str) -> bool:
    """Resolve an Alarm the human acknowledged: `alarm.resolved`, out of the inbox, every device.

    Args:
        deps: The Queen's collaborators.
        human_inbox: The Queen's own inbox of Alarms waiting on the human.
        alarm_id: The Alarm the human acknowledged (an ALARM line's `ref`).

    Returns:
        True when the Alarm was waiting and is now resolved; False when it was not waiting (never
        escalated, or already acknowledged), in which case nothing changes.
    """
    alarm = next((waiting for waiting in human_inbox.alarms if waiting.id == alarm_id), None)
    if alarm is None:
        return False  # Nothing waiting under that id: acknowledging twice is not an error.
    identity = CellIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor=deps.identity.actor
    )
    # The chain's last hop settles it: the same Alarm id every level recorded (codingrules 8.8).
    await record_alarm_event(deps.trail, identity, deps.clock, alarm, "alarm.resolved", by="human")
    human_inbox.resolve(alarm.id)
    await deps.human_channel.alarm_acknowledged(alarm.id)
    return True


def _queen_line(
    deps: QueenDeps, kind: ChatKind, text: str, ref: str | None, task_id: TaskId | None
) -> ChatEntry:
    """Build one fresh Queen line, its words cut to the chat's own bound."""
    return ChatEntry(
        id=new_chat_entry_id(deps.clock),
        at=deps.clock.now(),
        author=ChatAuthor.QUEEN,
        kind=kind,
        text=text[:MAX_CHAT_TEXT_CHARS],
        ref=ref,
        task_id=task_id,
    )
