"""Run the reconnection sequence: dial with backoff, announce, replay, record, sync, go online.

A Warden (the always-on supervisor of one Cell, a unit of compute) that lost its link to the Queen
(the Hive's central orchestrator) gets back to work in a fixed order (ADR-0053), and this module is
that order, with every collaborator injected so one sequence serves a colonized device's Warden and
a test alike. `redial` is the first half: dial through the injected `QueenDialer`, waiting
0.5 s, then twice as long each time, to 30 s between failed attempts (the schedule of ADR-0004),
and announce the Cell again through the injected `Announcer` (`cell.ready`,
`forage.capacity_report`, `cell.heartbeat`: the Queen's copy of the Cell may be stale, and she
treats a known Warden id re-attaching as a reconnection, not a new Warden). `rejoin` is the
second: replay the durable
queue in order, tell `AfterReconnect` the link is back (it records `warden.reconnected` and ships
the trail segment, signed), then, under the queue's lock, send what was queued meanwhile and the
questions held for the Queen, and only then declare the link online. The queue is drained in two
phases so the Warden is never kept waiting on a long backlog: the first drains it one entry per
lock hold, letting the Warden's own sends queue in between, and the second, short, runs under the
lock that also flips the link, so nothing can be queued after the last replay and left behind.
`TrailReconnect` is the usual `AfterReconnect`: it records `warden.reconnected` on the Warden's own
trail and then ships the segment, so the Queen's copy of the trail has the reconnection in it.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline sub-package. Called by `hivemind.wardens.offline.link.ReconnectingQueenLink` each time
    its link is lost; calls into `hivemind.wardens.offline.outbox`, `hivemind.common.logging`,
    `hivemind.pheromone` and `hivemind.memory` (for `TrailReconnect`'s `warden.reconnected`),
    `hivemind.wardens.trail_sync` and waggle (the clock, the transport protocol and its errors)
    only.

Key invariants:
    - A dial is one attempt: the backoff between attempts belongs to `redial`, so a dialer that
      retried inside itself would stretch the schedule.
    - Every await on the network has a timeout constant; a failure the network can cause
      (`TransportError`, `OSError`, `TimeoutError`) is retried, and nothing else is swallowed.
    - The link is declared online only after the queue and the held questions have been sent,
      with the queue's lock held, so a Warden never sees "online" with its backlog still on disk.
    - A link that dies while the sequence is part-way leaves every unsent message queued.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the sequence and the reason
      for each step.
    - hivemind.wardens.offline.link for the Transport that runs this on a lost link.
    - waggle.transport.websocket_client for the schedule constants this module reuses.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from hivemind.common.logging import get_logger
from hivemind.memory import MemoryIdentity
from hivemind.pheromone import PheromoneTrail, WardenEvent
from hivemind.wardens.offline.outbox import OfflineOutbox
from hivemind.wardens.trail_sync import TrailSync
from waggle.clock import Clock
from waggle.errors import TransportError
from waggle.ids import WardenId, new_event_id
from waggle.transport.base import Transport
from waggle.transport.websocket import PING_INTERVAL_S, PING_TIMEOUT_S
from waggle.transport.websocket_client import (
    RECONNECT_FACTOR,
    RECONNECT_INITIAL_S,
    RECONNECT_MAX_S,
)

DIAL_TIMEOUT_S = 30.0  # One dial: TCP, TLS pin and handshake; the client's own bound is shorter.
ANNOUNCE_TIMEOUT_S = 30.0  # Three small frames to a link that has just opened.
RETRYABLE = (TransportError, OSError, TimeoutError)  # What a network can do to the sequence.
# How far back from its newest shipped event a Warden sends its trail again once the link is back.
# A link whose far end vanished is declared dead by the websocket's keepalive PING_INTERVAL_S +
# PING_TIMEOUT_S after the last traffic, and the Warden notices on its next beat after that, so
# what it shipped in twice that long before it noticed may have gone into a connection already dead.
UNNOTICED = timedelta(seconds=2 * (PING_INTERVAL_S + PING_TIMEOUT_S))

_LOG = get_logger(__name__)

__all__ = [
    "ANNOUNCE_TIMEOUT_S",
    "DIAL_TIMEOUT_S",
    "RETRYABLE",
    "AfterReconnect",
    "Announcer",
    "QueenDialer",
    "Reconnection",
    "TrailReconnect",
    "WardenRecord",
    "close_quietly",
    "redial",
    "rejoin",
]


class QueenDialer(Protocol):
    """Open one connection to the Queen, signed and pinned as the Warden's configuration says."""

    async def dial(self) -> Transport:
        """Make one attempt to connect.

        Returns:
            A connected transport.

        Raises:
            waggle.errors.TransportError: The attempt failed (refused, unreachable, a wrong
                certificate pin); `redial` waits and tries again.
        """
        ...


class Announcer(Protocol):
    """Say who this Cell is, again, on a fresh connection."""

    async def announce(self, transport: Transport) -> None:
        """Send `cell.ready`, `forage.capacity_report` and `cell.heartbeat` on `transport`.

        Args:
            transport: The connection `QueenDialer.dial` just returned.

        Raises:
            waggle.errors.TransportError: The connection failed mid-announcement.
        """
        ...


class AfterReconnect(Protocol):
    """What the Warden does once the link is back and the queue has been replayed."""

    async def reconnected(self, outage_s: float) -> None:
        """Record `warden.reconnected` and ship the trail segment over the new link.

        Args:
            outage_s: How long the link was down, in seconds of the Warden's clock.
        """
        ...


@dataclass(frozen=True, slots=True)
class Reconnection:
    """The collaborators of one reconnection, bundled so the link takes few parameters."""

    dialer: QueenDialer  # One dial attempt.
    announcer: Announcer  # The frames a fresh connection must start with.
    after: AfterReconnect  # The record and the trail sync.
    clock: Clock  # Paces the backoff and measures the outage.


@dataclass(frozen=True, slots=True)
class WardenRecord:
    """Where a Warden writes its own `warden.*` events, for the step that has no Warden yet.

    The reconnecting link is built before the Warden it belongs to (the Warden is handed the
    link), so what records `warden.reconnected` holds these four instead of the Warden.
    """

    trail: PheromoneTrail  # The Warden's own local trail.
    identity: MemoryIdentity  # The Hive, node and actor stamped on the event.
    warden_id: WardenId  # The subject of the event.
    clock: Clock  # Mints the event's id and time.


class TrailReconnect:
    """The `AfterReconnect` step of a Warden with a trail of its own: record, then ship it."""

    def __init__(self, record: WardenRecord, trail_sync: TrailSync | None) -> None:
        """Build the step over the Warden's trail and, if it has one, its trail sync.

        Args:
            record: Where `warden.reconnected` is written.
            trail_sync: Ships the trail segment, `warden.reconnected` included, to the Queen;
                None for a Warden that records straight into the Queen's own store.
        """
        self._record = record
        self._trail_sync = trail_sync

    async def reconnected(self, outage_s: float) -> None:
        """Record `warden.reconnected`, then ship the trail segment over the new link.

        Args:
            outage_s: How long the link was down, in seconds of the Warden's clock.
        """
        record = self._record
        event = WardenEvent(
            id=new_event_id(record.clock),
            hive_id=record.identity.hive_id,
            node_id=record.identity.node_id,
            at=record.clock.now(),
            actor=record.identity.actor,
            kind="warden.reconnected",
            subject_id=record.warden_id,
            payload={"outage_s": round(outage_s, 3)},
        )
        await record.trail.record(event)
        # A segment that does not make it is shipped again on the heartbeat cadence (idempotent by
        # event id), so the result is not this step's to act on. What went out while the link was
        # dead but not yet known to be is not that kind of failure: the send returned, and the
        # Queen never had it. So the stretch before the loss was noticed goes out again.
        if self._trail_sync is not None:
            self._trail_sync.rewind(UNNOTICED)
            await self._trail_sync.sync()


async def redial(parts: Reconnection) -> Transport:
    """Dial and announce until both succeed, waiting longer after each failure.

    Args:
        parts: The dialer, announcer and clock.

    Returns:
        A connected transport that has announced the Cell.

    Raises:
        asyncio.CancelledError: The caller was cancelled (the Warden is stopping); the connection
            being attempted is closed first.
    """
    delay = RECONNECT_INITIAL_S
    while True:
        transport: Transport | None = None
        try:
            async with asyncio.timeout(DIAL_TIMEOUT_S):  # A silent network ends the attempt.
                transport = await parts.dialer.dial()
            async with asyncio.timeout(ANNOUNCE_TIMEOUT_S):  # Three frames; a dead link ends it.
                await parts.announcer.announce(transport)
        except RETRYABLE as exc:
            if transport is not None:
                await close_quietly(transport)
            _LOG.info("wardens.offline.redial_failed", error=type(exc).__name__, retry_in_s=delay)
            # Through the injected clock, so a test drives the whole schedule without a timer.
            await parts.clock.sleep(delay)
            delay = min(delay * RECONNECT_FACTOR, RECONNECT_MAX_S)
            continue
        except asyncio.CancelledError:
            if transport is not None:
                await close_quietly(transport)
            raise
        return transport


async def rejoin(
    parts: Reconnection,
    outbox: OfflineOutbox,
    transport: Transport,
    go_online: Callable[[], None],
    outage_s: float,
) -> None:
    """Catch the Queen up over a link that has just announced, then declare it online.

    Args:
        parts: Supplies the after-reconnect hook.
        outbox: The durable queue and the held questions.
        transport: The link `redial` returned.
        go_online: Flips the link online; called exactly once, with the queue's lock held and the
            queue empty.
        outage_s: How long the link was down.

    Raises:
        waggle.errors.TransportError: The link failed part-way; every unsent message is still
            queued, and the caller dials again.
        TimeoutError: The link took no frame for the send timeout.
    """
    await outbox.expire()
    # Phase one: drain the backlog one entry per lock hold, so a send the Warden makes meanwhile
    # is queued behind it instead of waiting for the whole replay.
    while True:
        async with outbox.lock:
            if not await outbox.replay_one(transport):
                break
    await parts.after.reconnected(outage_s)
    # Phase two, under the lock the link's flip needs: what arrived meanwhile, then the questions
    # the Queen was to hear after everything else, then the flip, all before anything else can
    # decide between "queue" and "send live".
    async with outbox.lock:
        while await outbox.replay_one(transport):
            pass
        await outbox.release_held(transport)
        go_online()


async def close_quietly(transport: Transport) -> None:
    """Close a connection that is being abandoned, whatever state it is in.

    Args:
        transport: The connection to close.
    """
    try:
        async with asyncio.timeout(DIAL_TIMEOUT_S):  # A close handshake with a dead peer.
            await transport.close()
    except RETRYABLE as exc:
        # Already gone, or the peer will not answer: either way nothing is left to close.
        _LOG.debug("wardens.offline.close_failed", error=type(exc).__name__)
