"""Provide the Entrance Reducer: drop the Entrance to loopback only; reopen it only from loopback.

The Hive Entrance (the Hive's one HTTP door) can be narrowed at once (ADR-0041). ``EntranceMode`` is
its state machine (codingrules Appendix C, "Entrance mode"): ``OPEN`` and ``REDUCED``, one table
with both edges, each recorded as its ``guard.*`` event in the same step as the persisted change,
so a restart resumes the mode it left. ``EntranceReducer.reduce`` persists REDUCED, ends every
session opened on the remote listener, stops the remote listener and the tunnel child
(``RemoteListenerControl``) and closes every remote socket (``StreamCloser``; the app does it
within one second); it is called by ``hive entrance reduce``, by the Entrance when it follows a
Guard Bee's ``guard.reduce_ordered`` (narrowing access is always safe without judgement), and when
the remote listener fails. Reducing a reduced Entrance records nothing and changes no state, but
re-asserts the narrowing, since every step after the persist is idempotent: a reduction interrupted
after it was persisted is finished by the next one (and by ``start_mode`` at a restart).
``reopen`` needs a stepped-up session on the loopback listener, persists OPEN and starts the remote
listener again. The two seams are implemented by the Entrance app (``hivemind.entrance.runtime``);
each change that happened is also broadcast to every approved device through the seams' security
notifier (a reduction concerns the Hive, not one device).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Built by the
    Entrance's composition root; called by the reduce and reopen routes and CLI commands, the
    Guard order follower and the listener supervisor (later steps). Calls into the Entrance
    tables (``EntranceStore.entrance_mode``), the session book and the two seams.

Key invariants:
    - ``MODE_TRANSITIONS`` has exactly one entry per mode; each edge names its trail kind.
    - A mode change and its event are written together, or neither is.
    - Only a stepped-up session on the loopback listener reopens; nothing reopens by itself.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "The Entrance
      Reducer".
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md for the two listeners.
    - .claude/codingrules.md Appendix C, "Entrance mode".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.session.models import Listener
from hivemind.entrance.enrol.deps.notifier import (
    NullSecurityNotifier,
    SecurityNotice,
    SecurityNotifier,
)
from hivemind.entrance.errors import (
    EntranceModeConflictError,
    InvalidModeTransitionError,
    ReopenRefusedError,
)
from waggle.ids import EventId

if TYPE_CHECKING:
    # Type-only: the store imports EntranceMode from here, so a runtime import would be a cycle.
    from hivemind.entrance.auth.session.book import SessionBook
    from hivemind.entrance.auth.session.models import AuthenticatedSession
    from hivemind.entrance.enrol.deps import EnrolmentRecords
    from hivemind.pheromone import GuardEvent

REDUCED_KIND = "guard.reduced"  # OPEN to REDUCED.
REOPENED_KIND = "guard.reopened"  # REDUCED to OPEN.

log = get_logger(__name__)

__all__ = [
    "MODE_TRANSITIONS",
    "REDUCED_KIND",
    "REOPENED_KIND",
    "EntranceMode",
    "EntranceReducer",
    "ReduceReason",
    "ReducerSeams",
    "RemoteListenerControl",
    "StreamCloser",
    "mode_trail_kind",
]


class EntranceMode(Enum):
    """Whether the Entrance serves its remote listener at all."""

    OPEN = "OPEN"  # Loopback always, and the remote listener while exposed.
    REDUCED = "REDUCED"  # Loopback only; every remote session ended; only loopback reopens.


# The single transition table (codingrules section 9): from-mode -> {to-mode: trail kind}.
MODE_TRANSITIONS: Mapping[EntranceMode, Mapping[EntranceMode, str]] = {
    # hive entrance reduce, a Guard Bee's reduce order, or a remote listener that failed.
    EntranceMode.OPEN: {EntranceMode.REDUCED: REDUCED_KIND},
    # A stepped-up session on the loopback listener reopened it (hive entrance open).
    EntranceMode.REDUCED: {EntranceMode.OPEN: REOPENED_KIND},
}


class ReduceReason(Enum):
    """Why the Entrance was reduced; recorded on ``guard.reduced``."""

    OPERATOR = "operator"  # hive entrance reduce, or the Observation Hive's reduce button.
    GUARD_ORDER = (
        "guard_order"  # A Guard Bee rule's guard.reduce_ordered, which the Entrance follows.
    )
    LISTENER_FAILED = "listener_failed"  # The remote listener failed to bind or crashed.


class RemoteListenerControl(Protocol):
    """Stop and start the remote listener (and the tunnel child in tunnel mode); the app's seam."""

    async def stop(self) -> None:
        """Stop the remote listener and the tunnel child; idempotent. Bounded to one second."""
        ...

    async def start(self) -> None:
        """Start the remote listener (and the tunnel child) again, if exposure asks for one."""
        ...


class StreamCloser(Protocol):
    """Close every WebSocket that arrived on the remote listener; the app's seam."""

    async def close_remote(self) -> None:
        """Close every remote socket; the app does it within one second. Idempotent."""
        ...


@dataclass(frozen=True, slots=True)
class ReducerSeams:
    """What the Entrance app provides the reducer.

    Attributes:
        listener: Stops and starts the remote listener and the tunnel child.
        streams: Closes every remote socket.
        notifier: Tells every approved device that the door narrowed or reopened; the no-op
            until the push channels are wired.
    """

    listener: RemoteListenerControl
    streams: StreamCloser
    notifier: SecurityNotifier = field(default_factory=NullSecurityNotifier)


def mode_trail_kind(from_mode: EntranceMode, to_mode: EntranceMode) -> str:
    """Return the trail kind of the edge ``from_mode`` to ``to_mode``.

    Args:
        from_mode: The mode the Entrance is in.
        to_mode: The mode it moves to.

    Returns:
        ``"guard.reduced"`` or ``"guard.reopened"``.

    Raises:
        InvalidModeTransitionError: No such edge (OPEN to OPEN, REDUCED to REDUCED).
    """
    kind = MODE_TRANSITIONS[from_mode].get(to_mode)
    if kind is None:
        raise InvalidModeTransitionError(from_mode, to_mode)
    return kind


class EntranceReducer:
    """Narrow the Entrance to loopback, and reopen it from loopback after step-up."""

    def __init__(
        self, records: EnrolmentRecords, sessions: SessionBook, seams: ReducerSeams
    ) -> None:
        """Build the reducer.

        Args:
            records: The Entrance tables (the persisted mode), the clock and the identity its
                events are stamped with.
            sessions: The session book whose remote sessions a reduction ends.
            seams: The remote listener's control and the remote socket closer.
        """
        self._records = records
        self._sessions = sessions
        self._seams = seams

    async def start_mode(self) -> EntranceMode:
        """Return the persisted mode, so a restart comes back in the mode it left.

        A reduced Entrance also ends any remote session a reduction interrupted after its
        persist left open, so no restart revives one.

        Returns:
            The mode to start in: the remote listener is started only when it is OPEN.
        """
        # Latency: one local read of a one-row table.
        mode = await self._records.store.entrance_mode.get()
        if mode is EntranceMode.REDUCED:
            await self._sessions.end_remote()
        return mode

    async def reduce(self, reason: ReduceReason, actor: str) -> bool:
        """Drop the Entrance to loopback only; reducing a reduced Entrance changes nothing.

        Args:
            reason: Why.
            actor: Who: ``"human"`` at the Hive Stand, ``"system"`` for a Guard order or a
                failed listener, or the reducing device's id.

        Returns:
            True when this call moved the Entrance from OPEN to REDUCED.
        """
        payload: dict[str, JsonValue] = {"reason": reason.value}
        event = await self._change(EntranceMode.OPEN, EntranceMode.REDUCED, actor, payload)
        # Nothing remote authenticates from here on, even before the listener has stopped.
        ended = await self._sessions.end_remote()
        # Every remote socket is told why before the listener's own shutdown closes it.
        await self._seams.streams.close_remote()
        try:
            # Latency: the app bounds the listener's graceful shutdown to one second.
            await self._seams.listener.stop()
        finally:
            # A remote login that finished while the listener stopped is ended too.
            ended += await self._sessions.end_remote()
        changed = event is not None
        if event is not None:
            await self._broadcast(event)
        log.info("entrance.reduced", reason=reason.value, changed=changed, remote_sessions=ended)
        return changed

    async def reopen(self, session: AuthenticatedSession) -> bool:
        """Reopen a reduced Entrance: loopback only, with step-up (ADR-0041).

        Args:
            session: The authenticated session asking.

        Returns:
            True when this call moved the Entrance from REDUCED to OPEN; False when it was open.

        Raises:
            ReopenRefusedError: The session is not on the loopback listener, or not stepped up.
        """
        if session.session.listener is not Listener.LOOPBACK:
            raise ReopenRefusedError("Only the loopback listener reopens a reduced Entrance.")
        if not session.stepped_up:
            raise ReopenRefusedError("Reopening a reduced Entrance needs a step-up first.")
        payload: dict[str, JsonValue] = {"listener": Listener.LOOPBACK.value}
        event = await self._change(
            EntranceMode.REDUCED, EntranceMode.OPEN, session.device.id, payload
        )
        if event is None:
            return False
        # Latency: binding a socket and starting uvicorn on it, in this process.
        await self._seams.listener.start()
        await self._broadcast(event)
        log.info("entrance.reopened", device_id=session.device.id)
        return True

    async def _broadcast(self, event: GuardEvent) -> None:
        """Tell every approved device about a mode change: it concerns the Hive, not one device."""
        notice = SecurityNotice.broadcast(EventId(event.id), event.kind, event.at)
        # Latency: the notifier queues the notice and returns; delivery is never awaited here.
        await self._seams.notifier.notify(notice)

    async def _change(
        self,
        expected: EntranceMode,
        new: EntranceMode,
        actor: str,
        payload: Mapping[str, JsonValue],
    ) -> GuardEvent | None:
        """Persist ``expected`` to ``new`` with its event; None when the mode was another."""
        records = self._records
        # Latency: one local read of a one-row table.
        if await records.store.entrance_mode.get() is not expected:
            return None
        kind = mode_trail_kind(expected, new)
        event = records.identity.event(
            records.clock, kind, records.identity.hive_id, payload, actor
        )
        try:
            # Latency: one local transaction writing the mode and its event together.
            await records.store.entrance_mode.change(expected, new, event)
        except EntranceModeConflictError:
            # A concurrent change got there first; it already did what this one would have.
            return None
        return event
