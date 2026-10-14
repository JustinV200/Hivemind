"""Run the Pollen Packet: dial the Queen, announce, serve, and dial again, forever.

The Pollen Packet (the thin gateway program on a device the Hive borrows) has one loop. Each turn
folds any finished relocation window into the state, dials the Queen's persisted address (the
Hive's central orchestrator, and nobody else) with the certificate pin from the invite, and on
success sends the signed ``swarm.device_heartbeat`` as the very first frame, then a capacity report
with trigger RECONNECTED, then replays the outbox in order. From then on three tasks serve the
connection: the reader hands each verified frame to the dispatcher, the pulse beats, and a stop
watcher ends it all on shutdown. A connection that ends, or never opens, is retried with the
capped backoff of waggle's ``TickLoop`` (0.5 s doubling to 30 s); one that lived a minute resets
it. The only thing that stops the loop is a Queen that speaks another Waggle major, which no retry
can fix. Programs and background processes belong to their lease, not to the connection, so a
dropped link stops their streaming and never the program. At startup, before the first dial, and
again when the loop stops, the packet releases every lease in its table (cause ORPHAN_SWEEP): a
crash leaves leases behind, and none may outlive the packet. The reports wait in the outbox.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Assembled by the composition
    root (pollen.cli) from the parts below and run as the process's one long task; calls into
    every other pollen.agent module and waggle's TickLoop, errors and clock.

Key invariants:
    - The first frame of every connection is a signed heartbeat; the courier is attached, and live
      frames flow, only after the outbox has been replayed behind it.
    - A dial is made to the Queen's persisted address or its relocation plan, never to an address
      that arrived in any other message.
    - Every await on the network has a timeout from a named constant (in pollen.agent.link).
    - An unexpected bug inside a connection ends that connection and is retried; it never ends
      the packet.
    - No lease, program or scratch directory outlives the packet: the loop's exit releases them.
    - A stop is heard during a reconnection backoff, not after it.

See Also:
    - docs/waggle/gateway-contract.md sections 5, 8 and 14.
    - waggle.loop for the backoff this loop inherits.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import ClassVar

from pollen.agent.courier import Courier
from pollen.agent.dispatch import Dispatcher
from pollen.agent.heartbeat import Pulse, Sentinel, Timings
from pollen.agent.leases import LeaseService
from pollen.agent.link import Link, LiveVerifier
from pollen.agent.moved import dial_plan
from pollen.agent.status import PacketStatus
from pollen.bootstrap import WardenKeeper
from pollen.errors import LinkLostError, PollenError, ProtocolMismatchError
from pollen.executors.base import TaskScope
from pollen.lease import DeadManSwitch
from pollen.state import StateStore
from waggle.clock import Clock
from waggle.codec import Signer
from waggle.errors import (
    CodecError,
    ConnectFailedError,
    ConnectionLostError,
    InvalidPayloadError,
    SignatureError,
    TransportError,
    UnsupportedVersionError,
)
from waggle.loop import INITIAL_BACKOFF_S, TickLoop
from waggle.messages.cell import ReleaseCause

log = logging.getLogger(__name__)

# Failures a connection may end with that are already classified: the loop retries the first
# group, and PollenError covers the one it must not retry (a protocol mismatch).
_KNOWN_FAILURES: tuple[type[Exception], ...] = (
    TransportError,
    CodecError,
    SignatureError,
    PollenError,
    TimeoutError,
    OSError,
)

SHUTDOWN_RELEASE_TIMEOUT_S = 30.0  # The most a startup or shutdown sweep of the leases may take.
LINK_DRAIN_S = 5.0  # How long the reader gets to explain a link the pulse found dead.

__all__ = [
    "LINK_DRAIN_S",
    "SHUTDOWN_RELEASE_TIMEOUT_S",
    "PacketParts",
    "PollenPacket",
    "failure_code",
]


@dataclass(frozen=True, slots=True)
class PacketParts:
    """Everything the packet's loop drives, assembled by the composition root."""

    state: StateStore  # The persisted terms and the Queen's address.
    signer: Signer  # The node key every link signs with.
    verifier: LiveVerifier  # The keys every link verifies with.
    courier: Courier  # Sends frames; holds the attached link.
    dispatcher: Dispatcher  # Routes each verified frame.
    pulse: Pulse  # Heartbeats and capacity reports.
    sentinel: Sentinel  # Silence, releases and addresses, for the life of the packet.
    leases: LeaseService  # The startup sweep.
    status: PacketStatus  # Diagnostics.
    deadman: DeadManSwitch  # The silence clock, started from the persisted last-heard.
    scope: TaskScope  # Owns every task that outlives a request.
    warden: WardenKeeper  # Adopts the on-device Warden at start and watches it while running.


def failure_code(error: BaseException) -> str:
    """Return the stable code that names why a connection failed, for status and logs.

    Args:
        error: What ended the connection.

    Returns:
        The error's own code where it has one, else a short pollen code. Never a message, which
        could carry an address.
    """
    code = getattr(error, "code", None)
    if isinstance(code, str):
        return code
    return "pollen.link.timeout" if isinstance(error, TimeoutError) else "pollen.link.os_error"


class PollenPacket(TickLoop):
    """The packet's loop: one turn per connection, backing off between failures."""

    _recoverable_errors: ClassVar[tuple[type[Exception], ...]] = (
        TransportError,
        CodecError,
        SignatureError,
        LinkLostError,
        TimeoutError,
        OSError,
    )

    def __init__(self, parts: PacketParts, clock: Clock, timings: Timings) -> None:
        """Prepare the loop; nothing is dialled until ``run``.

        Args:
            parts: The collaborators the loop drives.
            clock: Paces the backoff and the settle checks.
            timings: How long a link must live to reset the backoff.
        """
        super().__init__(clock)
        self._parts = parts
        self._timings = timings

    async def run(self) -> None:
        """Sweep what a crash left, then dial and serve until stopped.

        Raises:
            ProtocolMismatchError: The Queen speaks a Waggle major this packet does not.
        """
        parts = self._parts
        async with parts.scope:
            await self._sweep()
            # A Warden that outlived the packet's last run is adopted (or forgotten, if it died)
            # before anything is dialled, so the first heartbeat and the dead-man switch already
            # know which level the device is on.
            with contextlib.suppress(PollenError, OSError):
                await parts.warden.reconcile()
            parts.scope.spawn(parts.sentinel.run(), "sentinel")
            parts.scope.spawn(parts.warden.watch(), "warden-watch")
            try:
                await super().run()
            finally:
                # Whatever stopped the loop, no lease outlives the packet: its programs are
                # ended and its changes undone now, and the reports wait in the outbox.
                await self._sweep()
                with contextlib.suppress(PollenError, OSError):
                    await parts.sentinel.persist()

    async def _sweep(self) -> None:
        """Release every lease in the table as an orphan: one a crash left, or the packet's own."""
        with contextlib.suppress(PollenError, OSError, TimeoutError):
            async with asyncio.timeout(SHUTDOWN_RELEASE_TIMEOUT_S):  # Local work, bounded.
                await self._parts.leases.release_all(
                    ReleaseCause.ORPHAN_SWEEP, include_releasing=True
                )

    async def _tick(self) -> None:
        """One turn: dial, announce, serve, and say how it went."""
        parts = self._parts
        link = await self._dial()
        began = self._clock.monotonic()
        try:
            await self._announce(link)
            await self._serve(link)
        except (UnsupportedVersionError, ConnectionLostError) as error:
            if isinstance(error, UnsupportedVersionError) or _is_major_mismatch(error):
                raise ProtocolMismatchError(
                    "The Queen speaks a Waggle major this packet does not."
                ) from error
            raise
        except _KNOWN_FAILURES:
            raise
        except Exception as error:  # SAFETY: loop top; a bug ends the connection, not the packet.
            log.error("pollen.connection_bug error=%s", type(error).__name__)
            raise LinkLostError("The connection ended on an internal fault.") from error
        finally:
            parts.courier.detach()
            parts.status.disconnected(None)
            with contextlib.suppress(TransportError, TimeoutError, OSError):
                await link.close()
        if self._stop.is_set():
            return
        if self._clock.monotonic() - began >= self._timings.stable_link_s:
            # A long-lived link resets the backoff (the base does, on a normal return), but the
            # first wait after a lost connection is still the initial one, and a stop ends it too.
            await self._wait_before_retry(INITIAL_BACKOFF_S)
            return
        raise LinkLostError("The connection ended soon after it began.")

    async def _wait_before_retry(self, seconds: float) -> None:
        """Wait out the reconnection backoff, but not past a request to stop.

        A packet that is down for a while waits up to half a minute between dials, and a service
        manager that asks it to stop gives it seconds: a stop that waited for the backoff would be
        killed before the shutdown sweep released the leases. So the wait ends when the stop
        event is set, whichever comes first.

        Args:
            seconds: The backoff, in seconds of the packet's clock.
        """
        sleeper = asyncio.ensure_future(self._clock.sleep(seconds))
        stopper = asyncio.ensure_future(self._stop.wait())
        try:
            await asyncio.wait({sleeper, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in (sleeper, stopper):
                waiter.cancel()
            await asyncio.gather(sleeper, stopper, return_exceptions=True)

    async def _on_tick_failed(self, error: Exception) -> None:
        """Record why the connection is down, for ``pollen status``."""
        self._parts.status.disconnected(failure_code(error))
        await self._parts.sentinel.publish()

    async def _dial(self) -> Link:
        """Dial the addresses of the relocation plan in order; the first that opens wins."""
        parts = self._parts
        state = parts.state.current
        failures: list[Exception] = []
        for url in dial_plan(state, self._clock.now()).urls:
            # The pin belongs to a wss:// listener; a ws:// loopback address has none.
            pin = state.tls_pin_sha256 if url.startswith("wss://") else None
            link = Link(url, pin, parts.signer, parts.verifier, self._clock)
            try:
                await link.connect()
            except (ConnectFailedError, TimeoutError) as error:
                failures.append(error)
                continue
            return link
        raise failures[-1]

    async def _announce(self, link: Link) -> None:
        """Send the first frame and the capacity report, replay the outbox, then go live."""
        parts = self._parts
        await parts.pulse.announce(link)
        await self._flush(link)
        parts.courier.attach(link)
        # Anything queued between the first flush and the attach is behind the replay's end.
        await self._flush(link)
        parts.status.connected()
        await parts.sentinel.publish()

    async def _flush(self, link: Link) -> None:
        """Replay the outbox through ``link``; a transport failure ends the connection."""
        report = await self._parts.courier.flush(link)
        if report.stopped_by is not None:
            raise report.stopped_by

    async def _serve(self, link: Link) -> None:
        """Run the reader, the pulse and the stop watcher until the connection ends."""
        reader = asyncio.create_task(self._read(link), name="reader")
        pulse = asyncio.create_task(self._parts.pulse.run(), name="pulse")
        stop = asyncio.create_task(self._stop.wait(), name="stop")
        tasks = (reader, pulse, stop)
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if pulse.done() and not reader.done():
                # The pulse gave up because a send failed: the link is going, and the reader is
                # about to say why (a protocol mismatch looks like any lost link until it does).
                await asyncio.wait(
                    {reader, stop}, timeout=LINK_DRAIN_S, return_when=asyncio.FIRST_COMPLETED
                )
        finally:
            for task in tasks:
                task.cancel()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:  # The reader's comes first: it knows the real reason.
            if isinstance(outcome, Exception):
                raise outcome

    async def _read(self, link: Link) -> None:
        """Hand each verified frame to the dispatcher; answer a frame with a bad payload."""
        dispatcher = self._parts.dispatcher
        # No "while connected" guard: a link that closed before the reader began must still say
        # how, and only ``receive`` knows the reason (a protocol mismatch among them).
        while True:
            try:
                async for envelope in link.receive():
                    await dispatcher.handle(envelope)
                return  # The Queen closed the connection normally.
            except InvalidPayloadError as error:
                # A frame with a readable id but a bad payload leaves the connection open.
                await dispatcher.refuse_invalid(error)


def _is_major_mismatch(error: ConnectionLostError) -> bool:
    """Say whether the Queen closed the link because it speaks another major."""
    return UnsupportedVersionError.code in str(error)
