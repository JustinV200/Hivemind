"""Define the chat resource's bodies: a chat line, a page of them, and a message to the Queen.

The chat is the human end of the Queen's inbox (ADR-0040): the human's messages, her replies, her
questions and the Alarms that reached the human, in one conversation ordered by the log's own
position (``seq``), which is the cursor a page is read by and ``/v1/chat/stream`` resumes from.
Every line is ``C2`` (the human's own words, or words about their work), so reading it needs
``honey:clearance:c2``; writing a message only enters the Queen's inbox.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by
    ``hivemind.entrance.routes.chat`` and the chat stream; published in the OpenAPI document.
    Calls into the Queen's chat model and pydantic.

Key invariants:
    - A line is shown exactly as the chat log holds it; nothing is summarised or rewritten.

See Also:
    - hivemind.queen.chat for the chat log.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.queen import ChatAuthor, ChatEntry, ChatKind
from hivemind.queen.chat import MAX_CHAT_TEXT_CHARS
from waggle.messages.base import TaskIdField

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

__all__ = ["ChatAccepted", "ChatLine", "ChatPage", "ChatPost", "chat_line"]


class ChatLine(BaseModel):
    """One line of the chat (C2)."""

    model_config = _CONFIG

    seq: int = Field(ge=1, description="Its position in the log: the cursor pages and streams use.")
    id: str = Field(description="The line's own id (chat_...).")
    at: datetime = Field(description="When it was written.")
    author: ChatAuthor = Field(description="human or queen.")
    kind: ChatKind = Field(description="message, reply, question, alarm or notice.")
    text: str = Field(description="The words.")
    ref: str | None = Field(
        description="What it refers to: the question or Alarm id to answer or acknowledge, the "
        "goal request, or the message a reply answers."
    )
    task_id: TaskIdField | None = Field(description="The task it concerns, if any.")


class ChatPage(BaseModel):
    """A page of the chat, oldest first."""

    model_config = _CONFIG

    entries: list[ChatLine] = Field(description="The lines, oldest first.")
    newest_seq: int | None = Field(
        description="The newest position on this page: pass it as after to read on, or to the "
        "stream to resume from; null for an empty page."
    )


class ChatPost(BaseModel):
    """A message from the human to the Queen (``entrance:submit``)."""

    model_config = _CONFIG

    text: str = Field(min_length=1, max_length=MAX_CHAT_TEXT_CHARS, description="The words.")
    task_id: TaskIdField | None = Field(
        default=None, description="The task it concerns; null addresses the Queen at large."
    )


class ChatAccepted(BaseModel):
    """A message appended to the chat; the Queen reads it on her next tick."""

    model_config = _CONFIG

    id: str = Field(description="The chat line's id.")


def chat_line(entry: ChatEntry) -> ChatLine:
    """Shape a stored chat line for the Landing Board.

    Args:
        entry: The line as the chat log holds it; appended, so it has a position.

    Returns:
        Its view.

    Raises:
        ValueError: The line was never appended (it has no position).
    """
    if entry.seq is None:
        raise ValueError(f"Chat line {entry.id} has no position: it was never appended.")
    return ChatLine(
        seq=entry.seq,
        id=entry.id,
        at=entry.at,
        author=entry.author,
        kind=entry.kind,
        text=entry.text,
        ref=entry.ref,
        task_id=entry.task_id,
    )
