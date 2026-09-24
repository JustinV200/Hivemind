"""Provide the chat: the human end of the Queen's inbox, and her door for the Hive Entrance.

Docs/adr/0032, "The chat is the human end of the Queen's inbox, not a side channel": a message
from the human is appended to one chat log and enters the Queen's inbox as a `HumanMessage` her
Attendant scores; autopilot has no rule for free text, so an awake episode decides, and may REPLY
with words appended to the same log. Her questions and the Alarms that reached the human are
appended there too, so the human reads one conversation (README, "Chat": she speaks there as
Monarch). The package holds the log (`model`, `protocol`, `memory`, `sqlite`), the functions that
write it (`post`), the `HumanChannel` seam she tells the human's devices through (`channel`, the
Entrance implements it later; a no-op by default), and `ChatDoor` (`door`), the human-facing
methods `Queen` inherits: request, confirm or decline a goal, post a message, acknowledge an
Alarm. Every line is C2, and the trail records only that a message arrived or that she replied,
never the words.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Written
    by the Queen (her door, her tick, her questions and Alarms); read by her tick (unhandled human
    messages) and the Hive Entrance's `/v1/chat` and its stream. Calls into
    `hivemind.brood_chamber`, `hivemind.cell`, `hivemind.common`, `hivemind.pheromone`,
    `hivemind.queen.errors`, `.human_inbox`, `.intake`, `.trail`, `hivemind.supervision` and
    waggle only.

Key invariants:
    - The log is append-only: `mark_handled` on a human message is its one change.
    - The Queen never imports the Hive Entrance: she calls `HumanChannel`, which it implements.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the decision.
    - hivemind.queen.ticks.chat for the tick that drains human messages and acts on REPLY.
    - hivemind.queen.intake for the goal requests `ChatDoor.request_goal` commits.

Public API (roadmap step 10.5):
    - ChatEntry, ChatAuthor, ChatKind, ChatEntryId, new_chat_entry_id, CHAT_ENTRY_ID_PATTERN,
      CHAT_ENTRY_ID_PREFIX, MAX_CHAT_TEXT_CHARS, MAX_REF_CHARS: one line (model).
    - ChatLog, ChatQuery, ChatEntryExistsError, check_chat_append, DEFAULT_CHAT_PAGE,
      MAX_CHAT_PAGE: the log's seam (protocol).
    - InMemoryChatLog (memory), SqliteChatLog, apply_chat_migrations, SUBSYSTEM,
      MIGRATIONS_PACKAGE (sqlite): its two implementations.
    - HumanChannel, NullHumanChannel: the seam to the human's devices and its no-op (channel).
    - post_message, post_reply, post_notice, post_question, post_alarm, resolve_alarm: every
      write (post).
    - ChatDoor: the Queen's human-facing methods (door).
"""

from hivemind.queen.chat.channel import HumanChannel, NullHumanChannel
from hivemind.queen.chat.door import ChatDoor
from hivemind.queen.chat.memory import InMemoryChatLog
from hivemind.queen.chat.model import (
    CHAT_ENTRY_ID_PATTERN,
    CHAT_ENTRY_ID_PREFIX,
    MAX_CHAT_TEXT_CHARS,
    MAX_REF_CHARS,
    ChatAuthor,
    ChatEntry,
    ChatEntryId,
    ChatKind,
    new_chat_entry_id,
)
from hivemind.queen.chat.post import (
    post_alarm,
    post_message,
    post_notice,
    post_question,
    post_reply,
    resolve_alarm,
)
from hivemind.queen.chat.protocol import (
    DEFAULT_CHAT_PAGE,
    MAX_CHAT_PAGE,
    ChatEntryExistsError,
    ChatLog,
    ChatQuery,
    check_chat_append,
)
from hivemind.queen.chat.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteChatLog,
    apply_chat_migrations,
)

__all__ = [
    "CHAT_ENTRY_ID_PATTERN",
    "CHAT_ENTRY_ID_PREFIX",
    "DEFAULT_CHAT_PAGE",
    "MAX_CHAT_PAGE",
    "MAX_CHAT_TEXT_CHARS",
    "MAX_REF_CHARS",
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "ChatAuthor",
    "ChatDoor",
    "ChatEntry",
    "ChatEntryExistsError",
    "ChatEntryId",
    "ChatKind",
    "ChatLog",
    "ChatQuery",
    "HumanChannel",
    "InMemoryChatLog",
    "NullHumanChannel",
    "SqliteChatLog",
    "apply_chat_migrations",
    "check_chat_append",
    "new_chat_entry_id",
    "post_alarm",
    "post_message",
    "post_notice",
    "post_question",
    "post_reply",
    "resolve_alarm",
]
