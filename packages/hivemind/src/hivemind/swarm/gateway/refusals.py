"""Provide TransportRefusals and EnrolmentAttempts: what the swarm listener refuses, and bounds it.

Waggle (the Hive's bee-to-bee wire protocol) protects the Hive Stand's listener at the transport:
a connection that sends frames faster than its ceiling is closed with code 4029, and an envelope
whose id was already seen inside the replay window is dropped before anything handles it. Waggle
writes no trail, so each refusal is reported to a callback, and this is the swarm listener's
callback (ADR-0055). A flood becomes `guard.frame_ceiling` and a repeated frame becomes
`guard.replay_refused`, each naming the node (the Guard Bee, the Hive's security watcher, counts
them as a pattern; it never sees a frame). One duplicate is expected and not recorded: a device
that reconnects replays the frames its outbox still held when the link dropped, and the first of
those after a reconnect are duplicates of frames the Hive already handled and acknowledged too
late. So a duplicate inside a short grace after the node's link attached is dropped silently
(ADR-0055: the reconnection grace), and any other is a refusal on the record.

`EnrolmentAttempts` bounds the one thing an unauthenticated peer can make the listener do: a
self-signed `swarm.enrol_request` is the only frame the verifier accepts from an unknown node (by
design, so a new device can present its invite), and each one costs a registry read and, when the
invite is unknown, a `swarm.enrol_refused` row on the append-only trail. A peer looping
connect, request, close would grow the trail without bound, so each remote address has a token
bucket of attempts a minute, the Entrance's per-address shape (ADR-0041); past it the router
closes the connection without reading the registry, answering or recording. The buckets are in
memory and bounded in keys for the same reasons as the Entrance's: they describe the last minute,
and a flood from more addresses than the capacity is the Guard Bee's to answer.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    `TransportRefusals` is built by the swarm listener and handed to waggle's `WebSocketServer`
    as `on_refusal`; it reads the gateway hub for when a node's link attached and records through
    the swarm recorder. `EnrolmentAttempts` is built and consulted by the connection router.

Key invariants:
    - The callback never raises for a recorder failure: it runs on a connection's receive path,
      and a trail that is down must not turn a flood into a crash. The failure is logged.
    - An event names the node and the envelope id, never a frame or anything inside one.
    - A flood is always recorded; only a duplicate inside the reconnection grace is silent.
    - An address gets at most `ENROL_ATTEMPTS_PER_MINUTE` enrolment attempts a minute, whatever
      port each came from; at most `MAX_TRACKED_ADDRESSES` addresses are remembered.

See Also:
    - docs/adr/0055-waggle-replay-window-and-frame-ceiling.md for the rules recorded here.
    - waggle.transport.limits for the ConnectionRefusal this receives.
    - hivemind.queen.cell_gate.refusals for the same shape of record for a Virtual Cell's link.
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from pydantic import ValidationError

from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway.hub import GatewayHub
from waggle.clock import Clock
from waggle.ids import HiveId
from waggle.transport.limits import ConnectionRefusal, RefusalKind

# How long after a node's link attaches a repeated envelope id counts as the outbox replay a
# reconnection produces. Two minutes covers a device that reconnects, resends its backlog and
# is slow to drain it; a replay attack later than that is on the record.
RECONNECT_GRACE_S = 120.0

FRAME_CEILING_KIND = "guard.frame_ceiling"
REPLAY_REFUSED_KIND = "guard.replay_refused"

# Enrolment attempts one address may make a minute. Enrolling is a once-per-device ceremony that
# a packet retries only after an operator mints a new invite, so ten covers a household behind one
# address while a loop of unknown invites is cut off after its first ten rows.
ENROL_ATTEMPTS_PER_MINUTE = 10
MAX_TRACKED_ADDRESSES = 1_024  # Buckets kept: far more addresses than devices any Hive enrols.
_SECONDS_PER_MINUTE = 60.0
# The bucket of a peer whose transport names no address (an in-process one): all such peers share
# it, which bounds them together rather than not at all.
UNNAMED_ADDRESS = "unnamed"

log = get_logger(__name__)

__all__ = [
    "ENROL_ATTEMPTS_PER_MINUTE",
    "FRAME_CEILING_KIND",
    "MAX_TRACKED_ADDRESSES",
    "RECONNECT_GRACE_S",
    "REPLAY_REFUSED_KIND",
    "UNNAMED_ADDRESS",
    "EnrolmentAttempts",
    "TransportRefusals",
]


@dataclass(slots=True)
class _Bucket:
    """One address's remaining attempts, and the monotonic moment they were last refilled."""

    tokens: float
    refilled_at: float


class EnrolmentAttempts:
    """A token bucket of enrolment attempts per remote address, bounded in addresses."""

    def __init__(
        self,
        clock: Clock,
        per_minute: int = ENROL_ATTEMPTS_PER_MINUTE,
        capacity: int = MAX_TRACKED_ADDRESSES,
    ) -> None:
        """Build the buckets, empty.

        Args:
            clock: Its `monotonic` reading refills the buckets.
            per_minute: Attempts one address may make a minute, and a bucket's full size; >= 1.
            capacity: Addresses remembered before the least recently seen is forgotten; >= 1.

        Raises:
            ValueError: The rate or the capacity is below 1.
        """
        if min(per_minute, capacity) < 1:
            raise ValueError("The enrolment attempt rate and the bucket capacity are at least 1.")
        self._clock = clock
        self._per_minute = float(per_minute)
        self._capacity = capacity
        # Insertion order is recency order: every take moves its address to the end.
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def allow(self, remote_address: str | None) -> bool:
        """Charge one attempt to the address, if its bucket has one left.

        Args:
            remote_address: The peer's address as the transport reports it (`host:port`), or None
                when the transport names none. The port is dropped: a loop opens a new one each
                time, and the bucket is the host's.

        Returns:
            True when the attempt may be judged; False when the address is over its rate, and
            nothing more is spent on it.
        """
        key = _host_of(remote_address)
        now = self._clock.monotonic()
        bucket = self._buckets.pop(key, None)
        # An address seen for the first time (or forgotten since) starts with a full minute.
        if bucket is None:
            bucket = _Bucket(tokens=self._per_minute, refilled_at=now)
        elapsed = max(0.0, now - bucket.refilled_at)
        bucket.tokens = min(
            self._per_minute, bucket.tokens + elapsed * self._per_minute / _SECONDS_PER_MINUTE
        )
        bucket.refilled_at = now
        allowed = bucket.tokens >= 1.0
        if allowed:
            bucket.tokens -= 1.0
        # Re-inserted at the end (most recent); the oldest go first once past capacity.
        self._buckets[key] = bucket
        while len(self._buckets) > self._capacity:
            self._buckets.popitem(last=False)
        return allowed


def _host_of(remote_address: str | None) -> str:
    """Return the host part of a `host:port` address, the bucket's key."""
    if not remote_address:
        return UNNAMED_ADDRESS
    # `host:port` with an IPv6 host as `[::1]:443` or bare `::1:443`: the port is after the
    # last colon either way, so what precedes it is the host.
    host, colon, _port = remote_address.rpartition(":")
    return host if colon else remote_address


class TransportRefusals:
    """Record each refusal the swarm listener's frame ceiling and replay window make."""

    def __init__(
        self,
        recorder: SwarmRecorder,
        hub: GatewayHub,
        hive_id: HiveId,
        grace_s: float = RECONNECT_GRACE_S,
    ) -> None:
        """Build the callback over the trail writer and the hub.

        Args:
            recorder: The trail, clock and identity the events are recorded with.
            hub: Says when a node's link last attached, which the reconnection grace reads.
            hive_id: The subject of an event about a connection that named no node yet.
            grace_s: Seconds after a link attaches in which a repeated id is expected.
        """
        self._recorder = recorder
        self._hub = hub
        self._hive_id = hive_id
        self._grace = timedelta(seconds=grace_s)
        # Dispatch by the refusal's kind through a table, so a new kind is a new entry and the
        # handler never branches on it.
        self._by_kind: dict[RefusalKind, Callable[[ConnectionRefusal], Awaitable[None]]] = {
            RefusalKind.REPLAY: self._replay,
            RefusalKind.FRAME_CEILING: self._ceiling,
        }

    async def __call__(self, refusal: ConnectionRefusal) -> None:
        """Handle one refusal: record it unless it is an expected duplicate.

        Args:
            refusal: What the transport refused, and for which node.
        """
        await self._by_kind[refusal.kind](refusal)

    async def _replay(self, refusal: ConnectionRefusal) -> None:
        """Record a repeated envelope id, unless it follows a reconnection closely."""
        if self._in_grace(refusal):
            return
        await self._record(REPLAY_REFUSED_KIND, refusal)

    async def _ceiling(self, refusal: ConnectionRefusal) -> None:
        """Record a connection closed for flooding."""
        await self._record(FRAME_CEILING_KIND, refusal)

    def _in_grace(self, refusal: ConnectionRefusal) -> bool:
        """Whether the node's link attached recently enough for a duplicate to be expected."""
        if refusal.node_id is None:
            return False
        attached = self._hub.attached_at_for_node(refusal.node_id)
        return attached is not None and refusal.at - attached <= self._grace

    async def _record(self, kind: str, refusal: ConnectionRefusal) -> None:
        """Write the guard event for a refusal; a failure is logged, never raised."""
        subject = refusal.node_id if refusal.node_id is not None else self._hive_id
        try:
            event = self._recorder.guard_event(
                kind,
                subject,
                {"node_id": refusal.node_id, "envelope_id": refusal.envelope_id},
            )
            await self._recorder.record(event)
        except (HiveMindError, sqlite3.Error, ValidationError) as exc:
            # The refusal itself is already enforced by the transport; only its record is lost.
            log.error("swarm.refusal_not_recorded", kind=kind, error=type(exc).__name__)
