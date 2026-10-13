"""Keep the packet's clocks: heartbeats and capacity reports out, silence and housekeeping in.

Two things on the Pollen Packet (the thin gateway program on a device the Hive borrows) happen on
a timer and not because a frame arrived. ``Pulse`` is what the packet *says*: the first frame of
every connection is a signed ``swarm.device_heartbeat`` followed by a ``forage.capacity_report``
with trigger RECONNECTED, then a heartbeat every ``heartbeat_interval_s`` for as long as the
connection is open, whatever commands are running, and a fresh capacity report when a figure moved
materially (CHANGED) or on a slow timer (PERIODIC). ``Sentinel`` is what the packet *watches*: it
treats a link that has stayed open a full WebSocket ping interval as proof the Queen (the Hive's
central orchestrator) answered, trips the dead-man switch when she has been silent longer than the
enrolled limit and releases every open lease with cause DEAD_MAN (gateway-contract section 13),
retries releases that failed a step, folds a finished relocation window into the state, makes the
packet redial when the Queen's address moved, and persists the last moment she was heard from and
the status document at a coarse interval, since flash storage wears.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built by the composition root
    and driven by pollen.agent.packet; sends through pollen.agent.courier, releases through
    pollen.agent.leases and reads the lease table; calls into pollen.agent.moved, pollen.lease and
    waggle.

Key invariants:
    - Sending a heartbeat never waits on a command: ``Pulse`` is its own task and a slow executor
      cannot delay it.
    - The dead-man switch applies only at runtime level GATEWAY, only to open leases, and its clock
      is monotonic, so changing the wall clock neither trips it nor delays it.
    - A link counts as "heard" only after it has been open a full ping interval; a link that the
      Queen closes at once (a revoked node) never resets the switch.
    - ``last_heard_at`` is rewritten only when it moved by half the persist interval or more.

See Also:
    - docs/waggle/gateway-contract.md sections 5, 8 and 13.
    - pollen.lease.deadman for the monotonic silence clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime

from pollen.agent.courier import Courier
from pollen.agent.leases import LeaseService
from pollen.agent.link import Link, LiveVerifier
from pollen.agent.moved import dial_plan, settle, trusted_keys
from pollen.agent.status import PacketStatus, write_status
from pollen.executors.info import build_capacity_report, capacity_changed, host_capacity
from pollen.lease import DeadManSwitch, LeaseState, LeaseTable
from pollen.state import DataLayout, PacketState, StateStore
from waggle.clock import Clock
from waggle.messages.cell import ReleaseCause
from waggle.messages.forage import CapacityTrigger
from waggle.messages.reports import HostCapacityReport
from waggle.messages.swarm import DeviceHeartbeat, RuntimeLevel
from waggle.messages.swarm.enrolment import MAX_HEARTBEAT_LEASES
from waggle.transport.websocket import PING_INTERVAL_S

log = logging.getLogger(__name__)

__all__ = ["Pulse", "PulseParts", "Sentinel", "SentinelParts", "Timings"]


@dataclass(frozen=True, slots=True)
class Timings:
    """The packet's intervals, injectable so tests need not wait for the production ones."""

    capacity_period_s: float = 300.0  # A PERIODIC capacity report when nothing else was sent.
    watchdog_s: float = 1.0  # How often the sentinel looks at silence, releases and addresses.
    persist_s: float = 60.0  # How often last-heard and the status document are written.
    retry_s: float = 30.0  # How often a release that failed a step is tried again.
    stable_link_s: float = 60.0  # A link open this long resets the reconnection backoff.
    link_proof_s: float = PING_INTERVAL_S  # Open this long means a ping was answered.


@dataclass(frozen=True, slots=True)
class PulseParts:
    """What the pulse needs, bundled so it takes few parameters."""

    state: StateStore  # Identity, the Queen's address and the heartbeat interval.
    leases: LeaseTable  # The open leases and the processes they track.
    courier: Courier  # Builds frames and sends them on the attached link.
    clock: Clock  # Paces the heartbeats and measures uptime.
    layout: DataLayout  # Whose filesystem the capacity report measures.


class Pulse:
    """Sends the heartbeat and the capacity reports.

    Owns mutable state (codingrules 8.5, documented): the last capacity snapshot reported and when,
    replaced by whichever task sends a report (one at a time: announce, then the periodic loop).
    """

    def __init__(self, parts: PulseParts, timings: Timings) -> None:
        """Start the uptime clock.

        Args:
            parts: State, leases, courier, clock and layout.
            timings: The capacity period.
        """
        self._parts = parts
        self._timings = timings
        self._started = parts.clock.monotonic()
        self._last_host: HostCapacityReport | None = None
        self._last_report_at = self._started

    async def announce(self, link: Link) -> None:
        """Send the first frame of a connection and the RECONNECTED capacity report.

        Args:
            link: The connection that has just opened; sent on directly, since the courier is not
                attached until the outbox has been replayed behind these two frames.

        Raises:
            TransportError: The link failed.
            TimeoutError: The peer did not take a frame in time.
        """
        parts = self._parts
        hive = parts.state.current.hive_id
        await link.send(parts.courier.envelope(self._heartbeat(), to=hive))
        host = await asyncio.to_thread(host_capacity, parts.layout.root)
        report = build_capacity_report(
            parts.state.current.cell_id, CapacityTrigger.RECONNECTED, host
        )
        await link.send(parts.courier.envelope(report, to=hive))
        self._remember(host)

    async def run(self) -> None:
        """Beat for as long as the attached link accepts frames; return when it does not."""
        parts = self._parts
        while True:
            await parts.clock.sleep(parts.state.current.heartbeat_interval_s)
            hive = parts.state.current.hive_id
            if not await parts.courier.send(self._heartbeat(), to=hive):
                return  # The link is gone; the connection's other tasks end with this one.
            await self._report_if_due()

    def _heartbeat(self) -> DeviceHeartbeat:
        """Build the liveness report as of now (no capacity figures ride on it)."""
        parts = self._parts
        return DeviceHeartbeat(
            device_id=parts.state.current.device_id,
            level=parts.state.current.level,
            lease_ids=parts.leases.open_ids()[:MAX_HEARTBEAT_LEASES],
            hive_started_processes=parts.leases.process_count(),
            outbox_pending=parts.courier.pending,
            uptime_s=max(0.0, parts.clock.monotonic() - self._started),
        )

    async def _report_if_due(self) -> None:
        """Send CHANGED when a figure moved materially, else PERIODIC when the period is up."""
        parts = self._parts
        host = await asyncio.to_thread(host_capacity, parts.layout.root)
        waited = parts.clock.monotonic() - self._last_report_at
        trigger = None
        if self._last_host is None or capacity_changed(self._last_host, host):
            trigger = CapacityTrigger.CHANGED
        elif waited >= self._timings.capacity_period_s:
            trigger = CapacityTrigger.PERIODIC
        if trigger is None:
            return
        report = build_capacity_report(parts.state.current.cell_id, trigger, host)
        if await parts.courier.send(report, to=parts.state.current.hive_id):
            self._remember(host)

    def _remember(self, host: HostCapacityReport) -> None:
        """Note the snapshot the Queen now holds, and when she got it."""
        self._last_host = host
        self._last_report_at = self._parts.clock.monotonic()


@dataclass(frozen=True, slots=True)
class SentinelParts:
    """What the sentinel needs, bundled so it takes few parameters."""

    state: StateStore  # The dead-man limit, the level and the Queen's address plan.
    leases: LeaseService  # Releases leases and retries failed releases.
    table: LeaseTable  # Which leases are open.
    courier: Courier  # Where the current link is.
    deadman: DeadManSwitch  # The monotonic silence clock.
    verifier: LiveVerifier  # Re-keyed when a relocation window ends.
    status: PacketStatus  # Diagnostics for ``pollen status``.
    layout: DataLayout  # Where the status document goes.
    clock: Clock  # Paces the checks.


class Sentinel:
    """Watches silence, releases and the Queen's address; runs for the life of the packet.

    Owns mutable state (codingrules 8.5, documented): the sequence of the last relocation it made
    the link redial for, so a fallback to the old address is not recycled again and again.
    """

    def __init__(self, parts: SentinelParts, timings: Timings) -> None:
        """Bind the sentinel to its collaborators.

        Args:
            parts: State, lease service, courier, dead-man switch, verifier, status and clock.
            timings: The watchdog, retry and persist intervals.
        """
        self._parts = parts
        self._timings = timings
        self._recycled_sequence = 0

    async def run(self) -> None:
        """Look every ``watchdog_s``; retry and persist on their own, slower, schedules."""
        parts, timings = self._parts, self._timings
        retried = persisted = parts.clock.monotonic()
        while True:
            await parts.clock.sleep(timings.watchdog_s)
            await self.check()
            now = parts.clock.monotonic()
            if now - retried >= timings.retry_s:
                retried = now
                await parts.leases.retry_releasing()
            if now - persisted >= timings.persist_s:
                persisted = now
                await self.persist()

    async def check(self) -> None:
        """Do one round: count a proven link as heard, trip the switch, settle, maybe redial."""
        parts = self._parts
        link = parts.courier.link
        if link is not None and link.open_for_s >= self._timings.link_proof_s:
            parts.deadman.heard()
        state = parts.state.current
        has_open = any(lease.state is LeaseState.OPEN for lease in parts.table.all())
        if (
            state.level is RuntimeLevel.GATEWAY
            and has_open
            and parts.deadman.is_tripped(state.offline_limit_s)
        ):
            log.warning("pollen.dead_man_tripped silence_ms=%d", parts.deadman.silence_s() * 1000)
            await parts.leases.release_all(ReleaseCause.DEAD_MAN, include_releasing=False)
        await self._follow_queen(link)

    async def persist(self) -> None:
        """Write last-heard (if it moved enough) and the status document."""
        parts = self._parts
        heard = parts.deadman.heard_at()
        stored = parts.state.current.last_heard_at
        if stored is None or (heard - stored).total_seconds() >= self._timings.persist_s / 2:
            await parts.state.update(last_heard_at=heard)
        await self.publish()

    async def publish(self) -> None:
        """Write the status document now; a failure is logged and never fatal."""
        parts = self._parts
        document = parts.status.snapshot(
            open_leases=len(parts.table.open_ids()),
            outbox_pending=parts.courier.pending,
            silence_s=parts.deadman.silence_s(),
        )
        try:
            await write_status(parts.layout.status, document)
        except OSError:
            log.warning("pollen.status_not_written")

    async def _follow_queen(self, link: Link | None) -> None:
        """Fold a finished relocation window in, and close a link that dials the wrong address."""
        parts = self._parts
        now = parts.clock.now()
        settled = await parts.state.transform(lambda state: _settled(state, now))
        if settled is not None:
            parts.verifier.replace(trusted_keys(settled))
        if link is not None and link.is_connected and self._should_recycle(link, now):
            with contextlib.suppress(
                Exception
            ):  # SAFETY: closing a dying link must not stop the sentinel.
                await link.close()

    def _should_recycle(self, link: Link, now: datetime) -> bool:
        """Say whether the link is to an address the packet should no longer be dialling."""
        state = self._parts.state.current
        plan = dial_plan(state, now)
        if link.uri not in plan.urls:
            return True
        move = state.queen_move
        # Once per relocation: if the new address is unreachable the packet falls back to the old
        # one, and must not be torn off it every second while the window lasts.
        if (
            move is not None
            and now >= move.effective_at
            and link.uri != plan.urls[0]
            and self._recycled_sequence != move.sequence
        ):
            self._recycled_sequence = move.sequence
            return True
        return False


def _settled(state: PacketState, now: datetime) -> PacketState | None:
    """Return the state with a finished relocation folded in, or None if nothing finished."""
    settled = settle(state, now)
    return None if settled is state else settled
