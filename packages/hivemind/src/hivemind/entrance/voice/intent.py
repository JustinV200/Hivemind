"""Define VoiceIntent: what a person means by a clip, a goal, an answer to a question, or chat.

A clip at the Landing Board (the Hive Entrance's versioned API) arrives with an intent (ADR-0040):
``goal`` (the words are a goal, echoed back for confirmation before anything is spent),
``answer:<question id>`` (the words answer a question waiting on the human, straight through, as a
typed answer does) or ``chat`` (the words are a message to the Queen, entering her inbox as typed
chat does). The intent is a query parameter on ``POST /v1/chat/audio`` and a member of the end
frame of a push-to-talk hold on the chat socket; both parse it here, once, so both refuse the same
things. The capability an intent needs travels with it: an answer needs ``entrance:answer``, as a
typed answer does, beside the ``entrance:submit`` every voice call needs.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Parsed by the
    voice route and the push-to-talk reader; read by the voice door. Calls into waggle's id and
    ULID rules only.

Key invariants:
    - An answer intent always names a well-formed question id; the others never name one.
    - ``INTENT_PATTERN`` accepts no text that ``VoiceIntent.parse`` would not.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md, the audio intents.
    - hivemind.entrance.voice.door for what each intent leads to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hivemind.entrance.voice.errors import InvalidIntentError
from waggle.errors import InvalidIdError
from waggle.ids import IdKind, MessageId, parse_id
from waggle.ulid import CROCKFORD_ALPHABET, ULID_LENGTH

SUBMIT = "entrance:submit"  # What every voice call needs: speaking gives the Hive work.
ANSWER = "entrance:answer"  # What a spoken answer needs besides, exactly as a typed answer does.
_ANSWER_PREFIX = "answer:"  # An answer intent's own prefix; the question id follows it.
# The documented shape: goal, chat, or answer: and a message id (msg_ and a Crockford ULID).
INTENT_PATTERN = (
    rf"^(goal|chat|{_ANSWER_PREFIX}{IdKind.MESSAGE.value}_[{CROCKFORD_ALPHABET}]{{{ULID_LENGTH}}})$"
)
MAX_INTENT_CHARS = 64  # The longest intent: answer:, msg_ and a 26-character ULID, with room.

__all__ = [
    "ANSWER",
    "INTENT_PATTERN",
    "MAX_INTENT_CHARS",
    "SUBMIT",
    "VoiceIntent",
    "VoiceIntentKind",
]


class VoiceIntentKind(Enum):
    """What a clip's words are for."""

    GOAL = "goal"  # A goal for the Hive: echoed back and held for the human's yes.
    ANSWER = "answer"  # The answer to a question waiting on the human: straight through.
    CHAT = "chat"  # A message to the Queen, into her inbox as typed chat.


@dataclass(frozen=True, slots=True)
class VoiceIntent:
    """One clip's intent: its kind, and for an answer the question it answers.

    Attributes:
        kind: What the words are for.
        question_id: The question an answer answers; None for a goal or chat.
    """

    kind: VoiceIntentKind
    question_id: MessageId | None = None

    def __post_init__(self) -> None:
        """Refuse an answer without its question, or a question on anything but an answer."""
        if (self.kind is VoiceIntentKind.ANSWER) != (self.question_id is not None):
            raise ValueError("An answer intent names its question; no other intent names one.")

    @classmethod
    def parse(cls, raw: str) -> VoiceIntent:
        """Read an intent as a device sends it: ``goal``, ``chat`` or ``answer:<question id>``.

        Args:
            raw: The intent, exactly as sent.

        Returns:
            The intent.

        Raises:
            InvalidIntentError: The text is none of the three, or its question id is malformed.
        """
        if raw.startswith(_ANSWER_PREFIX):
            try:
                question = parse_id(raw.removeprefix(_ANSWER_PREFIX), IdKind.MESSAGE)
            except InvalidIdError as exc:
                raise InvalidIntentError("an answer intent names a malformed question id") from exc
            return cls(VoiceIntentKind.ANSWER, MessageId(question))
        # Only the two bare kinds remain; an answer needs its question, so it is not one of them.
        for kind in (VoiceIntentKind.GOAL, VoiceIntentKind.CHAT):
            if raw == kind.value:
                return cls(kind)
        raise InvalidIntentError("the intent is none of goal, chat or answer:<question id>")

    @property
    def capability(self) -> str:
        """The capability this intent needs besides ``entrance:submit``, or that one itself."""
        return ANSWER if self.kind is VoiceIntentKind.ANSWER else SUBMIT

    def __str__(self) -> str:
        """Render the intent as a device sends it."""
        if self.question_id is not None:
            return f"{_ANSWER_PREFIX}{self.question_id}"
        return self.kind.value
