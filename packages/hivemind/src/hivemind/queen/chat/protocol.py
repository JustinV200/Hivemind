"""Define ChatLog, the persistence seam for the chat: one append-only log, read by cursor or time.

The chat (`hivemind.queen.chat.model.ChatEntry`) is one append-only log in the Queen's own tables
(docs/adr/0040). This protocol is its whole surface: `append` writes a line (with, for a human
message or a reply, its trail event in the same transaction) and returns it with its position
(`seq`) assigned; `read` pages it by position or by time (`ChatQuery`), which is what the Hive
Entrance's `/v1/chat` serves and its stream follows; `unhandled` and `mark_handled` are the
Queen's own half, the human messages still waiting for her decision, oldest first. Lines are never
edited or deleted: `mark_handled` is the one change, and it only ever stamps a human message.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's chat
    sub-package. Implemented by `.memory` and `.sqlite`; written by `hivemind.queen.chat.post`
    and read by the Queen's tick (`hivemind.queen.ticks.chat`) and the Hive Entrance. Calls into
    `hivemind.common.errors`, `hivemind.pheromone` (QueenEvent), `hivemind.queen.errors` and the
    chat package's own model only.

Key invariants:
    - `seq` increases by one per appended line and is never reused, so a reader that remembers
      the last `seq` it saw never misses or repeats a line.
    - `read` returns lines oldest first whatever its anchor: from just after `after_seq` when
      set, otherwise the newest `limit` lines that match.
    - An event given to `append` commits with its line, or neither commits.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md for the chat.
    - hivemind.queen.chat.memory and hivemind.queen.chat.sqlite for the two implementations.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.common.errors import ConflictError, InvariantViolationError
from hivemind.pheromone import QueenEvent
from hivemind.queen.chat.model import ChatEntry
from hivemind.queen.errors import QueenError
from waggle.messages.base import UtcDatetime

DEFAULT_CHAT_PAGE = 50  # A screenful of conversation: what the chat view opens on.
MAX_CHAT_PAGE = 500  # Never unbounded: a client pages further back rather than asking for all.

__all__ = [
    "DEFAULT_CHAT_PAGE",
    "MAX_CHAT_PAGE",
    "ChatEntryExistsError",
    "ChatLog",
    "ChatQuery",
    "check_chat_append",
]


class ChatEntryExistsError(ConflictError, QueenError):
    """Raise when a chat line is appended under an id the log already holds."""

    code: ClassVar[str] = "hivemind.queen.chat_entry_exists"

    def __init__(self, entry_id: str) -> None:
        """Build the error for a duplicate line id; nothing was written.

        Args:
            entry_id: The id that is already taken.
        """
        super().__init__(f"Chat line {entry_id} already exists; nothing was written.")
        self.entry_id = entry_id


class ChatQuery(BaseModel):
    """A bounded read of the chat, anchored by position or at the newest end; oldest first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    after_seq: int | None = Field(
        default=None,
        ge=0,
        description="Only lines after this position, oldest first from there: a stream's resume "
        "point. None anchors the page at the newest end instead.",
    )
    before_seq: int | None = Field(
        default=None, ge=1, description="Only lines before this position: scrolling back."
    )
    since: UtcDatetime | None = Field(default=None, description="Only lines written at or after.")
    limit: int = Field(
        default=DEFAULT_CHAT_PAGE, ge=1, le=MAX_CHAT_PAGE, description="Most lines to return."
    )


def check_chat_append(entry: ChatEntry, event: QueenEvent | None) -> None:
    """Require a fresh line, and an event (when given) that names it, before any write.

    Args:
        entry: The line about to be appended.
        event: The event about to be recorded with it, or None.

    Raises:
        InvariantViolationError: `entry` already has a position, or `event` names another line.
    """
    # A line with a seq was appended already: appending it again would duplicate the conversation.
    if entry.seq is not None:
        raise InvariantViolationError(f"Chat line {entry.id} was already appended at {entry.seq}.")
    if event is not None and event.payload.get("chat_entry_id") != entry.id:
        raise InvariantViolationError(
            f"event {event.id} names chat line {event.payload.get('chat_entry_id')!r}, not "
            f"{entry.id!r}."
        )


class ChatLog(Protocol):
    """The chat's one append-only log; implementations must be safe to call concurrently."""

    async def append(self, entry: ChatEntry, event: QueenEvent | None = None) -> ChatEntry:
        """Append `entry` (and `event`, when given, in the same transaction).

        Args:
            entry: A fresh line: no `seq` yet.
            event: The trail event that records it (a human message arriving, a reply), or None
                for a line whose own action is recorded elsewhere (a question, an Alarm, a notice).

        Returns:
            The line as stored, with its `seq` assigned.

        Raises:
            ChatEntryExistsError: A line with `entry.id` exists; nothing was written.
            InvariantViolationError: `check_chat_append` refused the pair; nothing was written.
        """
        ...

    async def read(self, query: ChatQuery) -> tuple[ChatEntry, ...]:
        """Return a page of the chat, oldest first (see ChatQuery for the anchor rule).

        Args:
            query: The anchor, the filters and the limit.

        Returns:
            At most `query.limit` lines matching every set field of `query`.
        """
        ...

    async def unhandled(self, limit: int) -> tuple[ChatEntry, ...]:
        """Return the human messages the Queen has not yet decided on, oldest first.

        Args:
            limit: The most lines to return; the rest wait for the next call.

        Returns:
            Up to `limit` human MESSAGE lines with no `handled_at`.
        """
        ...

    async def mark_handled(self, entry_id: str, handled_at: datetime) -> None:
        """Stamp a human message handled, so `unhandled` stops returning it.

        Idempotent: an unknown id, a Queen line or an already-handled message is left as it is.

        Args:
            entry_id: The human message the Queen's decision was about.
            handled_at: When that decision was written.
        """
        ...
