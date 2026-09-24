"""Define the chat's half of the Queen's tick: human messages into her inbox, her REPLY out.

Docs/adr/0032, "The chat is the human end of the Queen's inbox": a message the human posted
(`Queen.post_human_message`) waits in the chat log until her next tick, which her wake signal
starts at once. `human_items` drains every waiting message into an `InboxItem` of kind
`HUMAN_MESSAGE` from the `human` principal, the wire `HumanMessage` as its payload, so her
Attendant orders it among everything else and autopilot hands it to an awake episode (it has no
rule for free text). `reply` carries out a `REPLY` decision: the Queen's words appended as a
`queen`/`reply` line answering the message that woke her. `mark_handled` stamps the message once
the decision on it is written, so it is never decided twice. While her own model is clustered,
nothing is drained: the messages wait, durable, until she can think again.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen`'s own tick and `_act`. Calls into
    `hivemind.common.errors`, `hivemind.queen.chat` (the log, post_reply), `hivemind.queen.
    cluster` (awake_available), `hivemind.queen.deps`, `hivemind.queen.inbox`
    (human_inbox_item), `hivemind.supervision.attendant` (InboxItem) and waggle only.

Key invariants:
    - A human message becomes at most one InboxItem per tick, and is stamped handled only after
      the decision on it was written; a tick that fails before then leaves it waiting.
    - No log line or trail event here carries the message's words.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the chat.
    - hivemind.queen.ticks.awake for the episode a human message triggers.
    - hivemind.queen.chat.post for the lines this module appends.
"""

from __future__ import annotations

from hivemind.common.errors import InvariantViolationError
from hivemind.queen.chat import ChatEntry, post_reply
from hivemind.queen.cluster import awake_available
from hivemind.queen.deps import QueenDeps
from hivemind.queen.inbox import human_inbox_item
from hivemind.supervision.attendant import InboxItem
from waggle.messages.control import HumanMessage

MAX_MESSAGES_PER_TICK = 20  # Each may wake a model; the rest wait for the next tick, in order.

__all__ = ["MAX_MESSAGES_PER_TICK", "human_items", "mark_handled", "reply"]


async def human_items(deps: QueenDeps) -> list[InboxItem]:
    """Return every waiting human message as a HUMAN_MESSAGE InboxItem, oldest first.

    Args:
        deps: The Queen's collaborators; `chat` is read, `cluster_state` consulted.

    Returns:
        Up to `MAX_MESSAGES_PER_TICK` items; none at all while the Queen's own model is
        clustered, since nothing could decide them and each would only wait again.
    """
    if not awake_available(deps.cluster_state, deps):
        return []  # They stay in the log, unhandled, and are drained once her model returns.
    lines = await deps.chat.unhandled(MAX_MESSAGES_PER_TICK)
    return [human_inbox_item(_to_wire(line), line.id, line.at) for line in lines]


async def reply(deps: QueenDeps, item: InboxItem, message: str | None) -> None:
    """Carry out a REPLY: append the Queen's words, answering the message that woke her if any.

    Args:
        deps: The Queen's collaborators.
        item: The inbox item the deciding episode was about.
        message: The decision's words; `QueenDecision` requires them on a REPLY, so None only
            reaches here from a caller that decided REPLY without an episode, and says nothing.
    """
    if message is None:
        return  # No words to send: a REPLY without them is never appended as an empty line.
    answering = item.id if isinstance(item.payload, HumanMessage) else None
    await post_reply(deps, message, ref=answering, task_id=item.task_id)


async def mark_handled(deps: QueenDeps, item: InboxItem) -> None:
    """Stamp the human message `item` wraps handled, now that the decision on it is written.

    Args:
        deps: The Queen's collaborators.
        item: A HUMAN_MESSAGE item; its id is the chat line's own.
    """
    await deps.chat.mark_handled(item.id, deps.clock.now())


def _to_wire(line: ChatEntry) -> HumanMessage:
    """Rebuild the wire HumanMessage a waiting human line stands for."""
    # ChatEntry's own validator guarantees a human line names its device; a line without one
    # would be a store that broke that invariant, so it fails loudly rather than being guessed at.
    if line.device_id is None:
        raise InvariantViolationError(f"Chat line {line.id} is a human message with no device.")
    return HumanMessage(text=line.text, task_id=line.task_id, device_id=line.device_id)
