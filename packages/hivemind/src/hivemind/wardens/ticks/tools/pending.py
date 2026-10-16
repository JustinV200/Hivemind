"""Remember what a Warden is waiting on for the Royal Jelly Lab: askers and replies in flight.

A tool request crosses several hops and the answers name their request only indirectly. The Queen
answers a relayed `tool.request` with a `tool.promoted` carrying the tool id, or a `control.error`
naming the forwarded envelope; a sandbox request or a promotion this Warden made for itself is
answered by a message correlated to the envelope it sent. `ToolRelay` keeps the tables that map each
back to what is waiting: a table of askers (the sub-bee and its own envelope id, by tool id and by
forwarded envelope id), a table of futures a background job awaits (by the envelope id it sent),
and a table of the CELL-scope promotions this Warden announced and took as accepted when no
refusal came in time (by the announcement's envelope id), so a refusal that arrives late can still
retract what was installed. The tick resolves a future when the answer arrives; nothing in a tick
ever waits on one.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.tools sub-package. Owned by `hivemind.wardens.warden.Warden` (as `_tool_relay`); written
    by `relay` and read by `cell_scope`, `ports` and `remote_registry`. Calls into waggle only.

Key invariants:
    - Owns its mutable state in place (codingrules section 8.5); one event loop, no lock.
    - It never holds more than `MAX_PENDING_TOOL_REQUESTS` askers, nor more announcements: the
      oldest is dropped first, because its Worker's own wait ended long before.
    - A future is resolved at most once and removed when it is.

See Also:
    - hivemind.wardens.ticks.honey.HoneyRelay, which this is shaped like.
    - hivemind.workers.runtime.tools for the Worker's own end of the same conversation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from hivemind.workers.tools.comb import CombToolEntry
from waggle.ids import MessageId, ToolId, WorkerId
from waggle.messages.control import ErrorMessage
from waggle.messages.tool import ToolPromoted, ToolQuarantineReport

MAX_PENDING_TOOL_REQUESTS = 64  # A Worker has one request out at a time; more means lost answers.

type Reply = ToolQuarantineReport | ErrorMessage

__all__ = ["MAX_PENDING_TOOL_REQUESTS", "Announced", "Asker", "Reply", "ToolRelay"]


@dataclass(frozen=True, slots=True)
class Asker:
    """The sub-bee that asked for a tool, and the envelope it asked on."""

    worker_id: WorkerId  # The sub-bee whose request it was.
    original_id: MessageId  # Its own envelope id: what an error answering it must correlate to.


@dataclass(frozen=True, slots=True)
class Announced:
    """A promotion this Warden announced and installed, and what it displaced in its table."""

    promoted: ToolPromoted  # The announcement, as sent to the Queen.
    displaced: CombToolEntry | None  # The entry of that name before it; None if there was none.


class ToolRelay:
    """Remember each relayed request's asker, and each reply a background job is waiting for."""

    def __init__(self) -> None:
        """Start with nothing relayed, nothing awaited and nothing announced."""
        self._by_tool: dict[ToolId, Asker] = {}
        self._by_envelope: dict[MessageId, ToolId] = {}
        self._waits: dict[MessageId, asyncio.Future[Reply]] = {}
        self._announced: dict[MessageId, Announced] = {}

    def remember(self, tool_id: ToolId, forwarded_id: MessageId | None, asker: Asker) -> None:
        """Record that `asker` wants `tool_id`, forwarded up as envelope `forwarded_id`.

        Args:
            tool_id: The tool asked for; the Queen's `tool.promoted` carries it.
            forwarded_id: The envelope this Warden sent the Queen, which her error correlates to;
                None for a request this Warden answers itself.
            asker: Who asked and on which envelope.
        """
        self._by_tool[tool_id] = asker
        if forwarded_id is not None:
            self._by_envelope[forwarded_id] = tool_id
        # Oldest first: its Worker gave up long ago (its own channel times out).
        while len(self._by_tool) > MAX_PENDING_TOOL_REQUESTS:
            self._drop(next(iter(self._by_tool)))

    def take_by_tool(self, tool_id: ToolId) -> Asker | None:
        """Pop and return who asked for `tool_id`, or None if nobody is waiting on it here."""
        asker = self._by_tool.get(tool_id)
        if asker is not None:
            self._drop(tool_id)
        return asker

    def take_by_envelope(self, forwarded_id: MessageId | None) -> Asker | None:
        """Pop and return who asked the request forwarded as `forwarded_id`, if still known."""
        tool_id = self._by_envelope.get(forwarded_id) if forwarded_id is not None else None
        return self.take_by_tool(tool_id) if tool_id is not None else None

    def expect(self, envelope_id: MessageId) -> asyncio.Future[Reply]:
        """Register a future for the reply to the envelope this Warden is about to send.

        Args:
            envelope_id: The id of the envelope sent; the reply's `correlation_id`.

        Returns:
            A future a tick resolves with the reply. Register before sending, so a reply can
            never arrive ahead of its waiter.
        """
        future: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()
        self._waits[envelope_id] = future
        return future

    def resolve(self, envelope_id: MessageId | None, reply: Reply) -> bool:
        """Hand `reply` to the future waiting for it; False for a stray or late one."""
        future = self._waits.pop(envelope_id, None) if envelope_id is not None else None
        if future is None or future.done():
            return False
        future.set_result(reply)
        return True

    def discard(self, envelope_id: MessageId) -> None:
        """Forget a future whose waiter gave up (a timeout, a send that failed)."""
        self._waits.pop(envelope_id, None)

    def announced(
        self, envelope_id: MessageId, promoted: ToolPromoted, displaced: CombToolEntry | None
    ) -> None:
        """Record that the promotion sent as `envelope_id` was taken as accepted.

        Args:
            envelope_id: The announcement's envelope id, which a late refusal correlates to.
            promoted: The announcement itself; what `take_announced` hands back.
            displaced: The entry of that name in the Warden's table before this promotion is
                installed over it; None when there was none.
        """
        self._announced[envelope_id] = Announced(promoted, displaced)
        # Bounded like the askers: a refusal of something announced that long ago is not coming.
        while len(self._announced) > MAX_PENDING_TOOL_REQUESTS:
            del self._announced[next(iter(self._announced))]

    def take_announced(self, envelope_id: MessageId | None) -> Announced | None:
        """Pop and return the announcement sent as `envelope_id`, or None if none is remembered."""
        return self._announced.pop(envelope_id, None) if envelope_id is not None else None

    def __len__(self) -> int:
        """Return how many relayed requests still await their answer."""
        return len(self._by_tool)

    def _drop(self, tool_id: ToolId) -> None:
        """Remove an asker and every forwarded-envelope mapping that points at its tool."""
        self._by_tool.pop(tool_id, None)
        for envelope_id in [e for e, tool in self._by_envelope.items() if tool == tool_id]:
            del self._by_envelope[envelope_id]
