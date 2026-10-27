"""Define ChatEntry: one line of the chat, the human end of the Queen's inbox.

Docs/adr/0040, "The chat is the human end of the Queen's inbox, not a side channel": a message
the human types (or speaks) is appended here and enters the Queen's inbox as a `HumanMessage`;
her replies, her questions and the Alarms that reached the human are appended to the same log,
so the human reads one conversation. `ChatEntry` is one line of it: who wrote it (`ChatAuthor`:
the human, or the Queen as Monarch), what kind of line it is (`ChatKind`), the words, what it
refers to (a question id, an Alarm id, a goal request id, or the human message a reply answers),
the task it concerns, the device a human line came from, and, for a human message, when the
Queen decided on it. `seq` is the log's own position, assigned when the line is appended: the
cursor `/v1/chat` pages by and its stream follows, since ids minted in the same millisecond do
not sort in the order they were written.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's chat
    sub-package. Built by `hivemind.queen.chat.post`, stored by a `ChatLog`, read by the Queen's
    own tick (unhandled human messages) and the Hive Entrance's chat routes. Calls into
    `hivemind.cell` (HoneyClearance) and waggle only.

Key invariants:
    - A human line is always a MESSAGE and always names its device; a Queen line is never a
      MESSAGE and never names one.
    - `handled_at` is set only on a human MESSAGE, once the Queen's decision on it is written.
    - Every line is C2: a human's words are personal by provenance, and the Queen's replies,
      questions and Alarms are read beside them (codingrules 8.9); nothing launders one lower.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md for the chat.
    - hivemind.queen.chat.protocol for ChatLog, the store these lines live in.
    - waggle.messages.control.hive for HumanMessage, the wire form a human line becomes.
"""

from __future__ import annotations

import secrets
from enum import Enum
from typing import NewType

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hivemind.cell import HoneyClearance
from waggle.clock import Clock
from waggle.messages.base import DeviceIdField, TaskIdField, UtcDatetime
from waggle.messages.control.hive import MAX_HUMAN_TEXT_CHARS
from waggle.ulid import RANDOMNESS_BYTES, encode_ulid

CHAT_ENTRY_ID_PREFIX = "chat_"  # No waggle IdKind names a chat line; it never crosses Waggle.
CHAT_ENTRY_ID_PATTERN = r"^chat_[0-9A-HJKMNP-TV-Z]{26}$"  # The prefix plus a Crockford ULID.
MAX_CHAT_TEXT_CHARS = MAX_HUMAN_TEXT_CHARS  # The wire HumanMessage's own bound: a few pages.
MAX_REF_CHARS = 64  # One id: a question, Alarm, goal request or chat line; generous for any.
# What a line about a Night Veil Cell is reduced to at its teardown (codingrules 12): no words.
VEILED_TEXT = "(Removed: this line was about a Night Veil Cell that has ended.)"

__all__ = [
    "CHAT_ENTRY_ID_PATTERN",
    "CHAT_ENTRY_ID_PREFIX",
    "MAX_CHAT_TEXT_CHARS",
    "MAX_REF_CHARS",
    "VEILED_TEXT",
    "ChatAuthor",
    "ChatEntry",
    "ChatEntryId",
    "ChatKind",
    "new_chat_entry_id",
    "veiled",
]

ChatEntryId = NewType("ChatEntryId", str)


class ChatAuthor(Enum):
    """Who wrote a chat line."""

    HUMAN = "human"  # The operator, from an enrolled device.
    QUEEN = "queen"  # The Queen, speaking to the human as Monarch.


class ChatKind(Enum):
    """What a chat line is."""

    MESSAGE = "message"  # The human's own words, bound for the Queen's inbox.
    REPLY = "reply"  # The Queen's answer: an awake REPLY decision's words.
    QUESTION = "question"  # A question the Queen routed to the human; ref is its id.
    ALARM = "alarm"  # An Alarm that reached the human, the chain's last hop; ref is its id.
    NOTICE = "notice"  # The Queen telling the human something no decision produced.


def new_chat_entry_id(clock: Clock) -> ChatEntryId:
    """Mint a fresh `chat_`-prefixed ULID, timestamped by `clock`.

    Args:
        clock: Injected clock so the id's timestamp is deterministic in tests.

    Returns:
        A `"chat_<26-char ULID>"` string matching `CHAT_ENTRY_ID_PATTERN`.
    """
    # Milliseconds, not seconds: waggle.ulid's encoding assumes millisecond resolution.
    timestamp_ms = int(clock.now().timestamp() * 1000)
    ulid = encode_ulid(timestamp_ms, secrets.token_bytes(RANDOMNESS_BYTES))
    return ChatEntryId(f"{CHAT_ENTRY_ID_PREFIX}{ulid}")


class ChatEntry(BaseModel):
    """One line of the chat, as the Queen's chat log stores it and the Hive Entrance serves it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ChatEntryId = Field(pattern=CHAT_ENTRY_ID_PATTERN, description="This line's own id.")
    seq: int | None = Field(
        default=None,
        ge=1,
        description="The log's own position, assigned on append (1, 2, 3, ...); the cursor the "
        "chat is paged and streamed by. None only before the line is appended.",
    )
    at: UtcDatetime = Field(description="When the line was written.")
    author: ChatAuthor = Field(description="The human, or the Queen as Monarch.")
    kind: ChatKind = Field(description="What kind of line this is.")
    text: str = Field(
        min_length=1,
        max_length=MAX_CHAT_TEXT_CHARS,
        description="The words; for a question or an Alarm, the text the human must read.",
    )
    ref: str | None = Field(
        default=None,
        max_length=MAX_REF_CHARS,
        description="What the line refers to: a question id, an Alarm id, a goal request id, or "
        "the human message a reply answers.",
    )
    task_id: TaskIdField | None = Field(default=None, description="The task it concerns, if any.")
    device_id: DeviceIdField | None = Field(
        default=None, description="The enrolled device a human line came from; None otherwise."
    )
    handled_at: UtcDatetime | None = Field(
        default=None,
        description="For a human message: when the Queen's decision on it was written.",
    )
    clearance: HoneyClearance = Field(
        default=HoneyClearance.C2, description="Always C2 (module docstring)."
    )

    @field_validator("clearance")
    @classmethod
    def _always_royal(cls, value: HoneyClearance) -> HoneyClearance:
        """Refuse any label but C2: the chat is personal by provenance."""
        if value is not HoneyClearance.C2:
            raise ValueError(f"A chat line is always C2, never {value.value}.")
        return value

    @model_validator(mode="after")
    def _author_matches_kind(self) -> ChatEntry:
        """Tie a line's author to its kind, its device and its handled mark."""
        human = self.author is ChatAuthor.HUMAN
        # A human only ever sends a message; the Queen never writes one into her own inbox.
        if human != (self.kind is ChatKind.MESSAGE):
            raise ValueError(
                f"Chat line {self.id}: a {self.author.value} line cannot be a {self.kind.value}."
            )
        # The device is how a push finds its way back, so a human line always names it.
        if human != (self.device_id is not None):
            raise ValueError(f"Chat line {self.id}: device_id is set exactly on a human line.")
        if self.handled_at is not None and not human:
            raise ValueError(f"Chat line {self.id}: only a human message is ever handled.")
        return self


def veiled(line: ChatEntry, ids: frozenset[str]) -> ChatEntry | None:
    """Return `line` reduced to its skeleton when it names any of `ids`; None when it names none.

    A Night Veil Cell's teardown leaves nothing that names it (codingrules 12), and a chat line
    can: an Alarm's words, a question's task. Its position, time, author, kind and device stay, so
    the log's cursor never moves and a human line still says which device sent it; its words, ref
    and task go.

    Args:
        line: A stored line.
        ids: The ended Cell's id and every id of its own (tasks, Wardens, Alarms, ...).

    Returns:
        The reduced line, or None when nothing in `line` names any of `ids`.
    """
    body = line.model_dump_json()
    # Ids are prefixed ULIDs, so a substring match never mistakes one id for another.
    if not any(member in body for member in ids):
        return None
    return line.model_copy(update={"text": VEILED_TEXT, "ref": None, "task_id": None})
