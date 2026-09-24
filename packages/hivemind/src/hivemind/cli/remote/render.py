"""Render what a remote Hive says to this laptop: the Queen's lines, a goal's end, the inbox.

Everything printed here was written by someone else (the Queen relays questions a bee wrote, and
Alarms quote failures), so every string goes through ``hivemind.cli.landing.shown`` before it
reaches the terminal, one line at a time (a question's options arrive as its later lines). A
question is printed with the command that answers it, an Alarm with the one that acknowledges it,
because ``hive run --remote`` only follows: answering is ``hive inbox --remote``'s job, from any
terminal, while the goal waits.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.remote``. Used by
    ``hivemind.cli.remote.commands``. Calls into ``hivemind.cli.landing`` and the Landing Board's
    view models only.

Key invariants:
    - No foreign string is printed unescaped.

See Also:
    - hivemind.cli.landing.text for the escaping.
"""

from __future__ import annotations

from hivemind.cli.landing import shown
from hivemind.cli.remote.goals import FollowOutcome
from hivemind.entrance.models import ChatLine, InboxView
from hivemind.queen import ChatAuthor, ChatKind

MAX_LINE_CHARS = 400  # One printed line of a chat entry; a longer one is cut and marked.
# How each kind of the Queen's lines is introduced.
_LABELS = {
    ChatKind.REPLY: "queen",
    ChatKind.NOTICE: "queen (notice)",
    ChatKind.QUESTION: "queen asks",
    ChatKind.ALARM: "alarm",
}

__all__ = ["MAX_LINE_CHARS", "chat_lines", "inbox_lines", "outcome_line"]


def chat_lines(entry: ChatLine) -> list[str]:
    """One chat entry as printed while a goal is followed; the human's own lines are skipped.

    Args:
        entry: A line of the chat.

    Returns:
        Its lines, escaped; a question ends with how to answer it, an Alarm with how to
        acknowledge it; empty for a human's message.
    """
    if entry.author is ChatAuthor.HUMAN:
        return []
    label = _LABELS.get(entry.kind, "queen")
    tag = (
        f" [{entry.ref}]" if entry.ref and entry.kind in (ChatKind.QUESTION, ChatKind.ALARM) else ""
    )
    parts = entry.text.splitlines() or [""]
    lines = [f"{label}{tag}: {shown(parts[0], MAX_LINE_CHARS)}"]
    lines.extend(f"  {shown(part, MAX_LINE_CHARS)}" for part in parts[1:])
    if entry.kind is ChatKind.QUESTION and entry.ref:
        lines.append(f'  answer it: hive inbox --remote answer {entry.ref} "..."')
    if entry.kind is ChatKind.ALARM and entry.ref:
        lines.append(f"  acknowledge it: hive inbox --remote acknowledge {entry.ref}")
    return lines


def outcome_line(outcome: FollowOutcome) -> str:
    """Say how the follow ended: finished, refused, or still running at the timeout.

    Args:
        outcome: Where the goal request stood.

    Returns:
        One line.
    """
    view = outcome.view
    if view.refused:
        return f"goal request {view.id} was refused; the Queen's reason is in the chat."
    if view.finished_at is not None:
        return (
            f"goal request {view.id} finished at {view.finished_at.isoformat(timespec='seconds')}"
            f" (goal {view.goal_id})."
        )
    return (
        f"goal request {view.id} is still {view.state.value} (goal {view.goal_id or 'not planned'})"
        "; the Hive goes on with it."
    )


def inbox_lines(inbox: InboxView) -> list[str]:
    """What waits on the human, as ``hive inbox --remote`` prints it.

    Args:
        inbox: The questions and Alarms waiting.

    Returns:
        One block per question (id, task, text, options) and per Alarm; a line when empty.
    """
    lines: list[str] = []
    for question in inbox.questions:
        lines.append(
            f"question {question.id} (task {question.task_id}): {shown(question.text, 300)}"
        )
        lines.extend(
            f"  [{index}] {shown(option, 200)}" for index, option in enumerate(question.options)
        )
    for alarm in inbox.alarms:
        lines.append(
            f"alarm {alarm.id} {alarm.kind} ({alarm.severity}): {shown(alarm.detail, 300)}"
        )
    return lines or ["Nothing waits on the human."]
