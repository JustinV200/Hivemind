"""Provide the chat: the human end of the Queen's inbox, and her door for the Hive Entrance.

Docs/adr/0040, "The chat is the human end of the Queen's inbox, not a side channel": a message
from the human is appended to one chat log and enters the Queen's inbox as a `HumanMessage` her
Attendant scores; autopilot has no rule for free text, so an awake episode decides, and may REPLY
with words appended to the same log. Her questions and the Alarms that reached the human are
appended there too, so the human reads one conversation (README, "Chat": she speaks there as
Monarch). The package holds the log (`model`, `protocol`, `memory`, `sqlite`), the functions that
write it (`post`), the `HumanChannel` seam she tells the human's devices through (`channel`, the
Entrance implements it later; a no-op by default), `ChatDoor` (`door`), the human-facing
methods `Queen` inherits: request, confirm or decline a goal, post a message, acknowledge an
Alarm, and what a device's revocation withdraws (`withdraw`: its unplanned requests refused, its
goals stopped, placed work on its Warden first). Every line is C2, and the trail records only that
a message arrived or that she replied, never the words.

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
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md for the decision.
    - hivemind.queen.ticks.human.chat for the tick that drains human messages and acts on REPLY.
    - hivemind.queen.intake for the goal requests `ChatDoor.request_goal` commits.

Public API (roadmap step 10.5):
    - ChatEntry, ChatAuthor, ChatKind, ChatEntryId, new_chat_entry_id, CHAT_ENTRY_ID_PATTERN,
      CHAT_ENTRY_ID_PREFIX, MAX_CHAT_TEXT_CHARS, MAX_REF_CHARS: one line (model).
    - ChatLog, ChatQuery, ChatEntryExistsError, check_chat_append, DEFAULT_CHAT_PAGE,
      MAX_CHAT_PAGE: the log's seam (protocol).
    - InMemoryChatLog (memory), SqliteChatLog, apply_chat_migrations, SUBSYSTEM,
      MIGRATIONS_PACKAGE (sqlite): its two implementations.
    - HumanChannel, NullHumanChannel: the seam to the human's devices and its no-op (channel).
    - post_message, post_reply, post_notice, post_question, post_alarm, escalate_alarm,
      resolve_alarm, echo_goal, ECHO_PREFIX: every write (post).
    - ChatDoor: the Queen's human-facing methods (door).
    - refuse_unplanned, stop_goal, REVOKED_CODE, CANCEL_GRACE_S, CANCEL_SEND_TIMEOUT_S: what a
      revocation withdraws, and how a goal is stopped (withdraw).
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
    ECHO_PREFIX,
    echo_goal,
    escalate_alarm,
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
from hivemind.queen.chat.withdraw import (
    CANCEL_GRACE_S,
    CANCEL_SEND_TIMEOUT_S,
    REVOKED_CODE,
    refuse_unplanned,
    stop_goal,
)

__all__ = [
    "CANCEL_GRACE_S",
    "CANCEL_SEND_TIMEOUT_S",
    "CHAT_ENTRY_ID_PATTERN",
    "CHAT_ENTRY_ID_PREFIX",
    "DEFAULT_CHAT_PAGE",
    "ECHO_PREFIX",
    "MAX_CHAT_PAGE",
    "MAX_CHAT_TEXT_CHARS",
    "MAX_REF_CHARS",
    "MIGRATIONS_PACKAGE",
    "REVOKED_CODE",
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
    "echo_goal",
    "escalate_alarm",
    "new_chat_entry_id",
    "post_alarm",
    "post_message",
    "post_notice",
    "post_question",
    "post_reply",
    "refuse_unplanned",
    "resolve_alarm",
    "stop_goal",
]
