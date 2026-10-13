"""Play a Pollen Packet's side of the gateway contract, scripted, over any waggle Transport.

`ScriptedDevice` is the test double for the device end of a Swarm link. It reads the frames the
Hive sends on a transport and answers each the way the gateway contract says a conforming packet
does (leases, sessions, execs, files, background processes, observations), delegating to small
services that share one `DeviceState`; it also speaks unprompted when a test asks it to (a
heartbeat, a capacity report, a dead-man lease release). It owns no model, no real file and no
process: its disk is a dictionary and its programs are `ExecScript`s. It runs over a
`MemoryTransport` for link and session tests and over a real `WebSocketClientTransport` for the
listener tests, since it only ever calls `send` and `receive`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the Swarm unit tests, the
    SwarmSource entry of the RealCellSource contract suite and the real-socket listener tests.

Key invariants:
    - Requests are handled in the order they arrive; only an exec's output runs as a task of its
      own, so a program that hangs never blocks the next request.
    - A request type listed in `script.silent` is never answered and one listed in
      `script.refusals` draws that `control.error`, before any service sees it.
    - A send after the transport has gone is dropped quietly: a device cannot answer a link that
      is no longer there.

See Also:
    - docs/waggle/gateway-contract.md for the behaviour scripted here.
    - builders.swarm.device.state for the script and the state.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from builders.forage import make_host_capacity
from builders.swarm.device.context import DeviceContext
from builders.swarm.device.execs import ExecService
from builders.swarm.device.files import FileService
from builders.swarm.device.leases import LeaseService
from builders.swarm.device.procs import ProcessService
from builders.swarm.device.state import DeviceScript, DeviceState
from builders.swarm.parties import DeviceParty

from waggle.clock import Clock
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import CodecError, ConnectionLostError, SignatureError, TransportClosedError
from waggle.ids import HiveId, LeaseId, MessageId
from waggle.messages.base import WaggleMessage
from waggle.messages.cell.leases import LeaseReleased
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.forage.capacity import CapacityTrigger, LocalPoolUsage
from waggle.messages.forage.hosting import CapacityReport
from waggle.messages.swarm import DeviceHeartbeat, RuntimeLevel
from waggle.transport.base import Transport

__all__ = ["ScriptedDevice"]

Handler = Callable[[Envelope], Awaitable[None]]


class ScriptedDevice:
    """A scripted Pollen Packet: answers the Hive's requests from a script and a virtual disk."""

    def __init__(
        self,
        party: DeviceParty,
        hive_id: HiveId,
        clock: Clock,
        script: DeviceScript | None = None,
    ) -> None:
        """Build a device that is `party`, talking to the Queen at `hive_id`.

        Args:
            party: The device's ids and key.
            hive_id: The Queen's bee address, the recipient of everything the device sends.
            clock: Mints envelope ids and times, and the lease ids the device grants.
            script: What the device does when asked; the defaults when omitted.
        """
        self.state = DeviceState(
            party, hive_id, clock, script if script is not None else DeviceScript()
        )
        self._transport: Transport | None = None
        self._send_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        ctx = DeviceContext(self.state, self._send)
        self._ctx = ctx
        self._handlers: dict[type[WaggleMessage], Handler] = {}
        for service in (LeaseService(ctx), ExecService(ctx), FileService(ctx), ProcessService(ctx)):
            self._handlers.update(service.handlers())

    @property
    def script(self) -> DeviceScript:
        """The knobs a test turns; read again on every request."""
        return self.state.script

    def override(self, kind: type[WaggleMessage], handler: Handler) -> None:
        """Answer requests of `kind` with `handler` instead of the built-in service."""
        self._handlers[kind] = handler

    def start(self, transport: Transport) -> None:
        """Serve `transport` in a task of the device's own; `stop` ends it."""
        self._task = asyncio.ensure_future(self.serve(transport))

    async def stop(self) -> None:
        """Stop serving and wait for the serving task."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def serve(self, transport: Transport) -> None:
        """Answer every frame `transport` yields until it ends.

        Args:
            transport: The device's end of the link (a memory end or a dialled socket).
        """
        self._transport = transport
        try:
            async for envelope in transport.receive():
                await self._dispatch(envelope)
        except (ConnectionLostError, TransportClosedError, CodecError, SignatureError):
            return  # The link ended; a device has nobody left to answer.

    async def heartbeat(self, level: RuntimeLevel = RuntimeLevel.GATEWAY) -> None:
        """Send a `swarm.device_heartbeat`, the first frame of every connection."""
        state = self.state
        await self._send(
            DeviceHeartbeat(
                device_id=state.party.device_id,
                level=level,
                lease_ids=tuple(state.leases),
                hive_started_processes=sum(1 for p in state.processes.values() if p.running),
                outbox_pending=0,
                uptime_s=1.0,
            )
        )

    async def capacity_report(self, trigger: CapacityTrigger = CapacityTrigger.RECONNECTED) -> None:
        """Send a `forage.capacity_report` with a modest host, as after a reconnection."""
        await self._send(
            CapacityReport(
                cell_id=self.state.party.cell_id,
                trigger=trigger,
                host=make_host_capacity().to_wire(),
                model_servers=(),
                max_sub_bees=4,
                usage=LocalPoolUsage(
                    sub_bees_active=0, model_vram_bytes=0, model_disk_bytes=0, seats_exported=0
                ),
            )
        )

    async def release_unprompted(
        self, lease_id: LeaseId, cause: ReleaseCause = ReleaseCause.DEAD_MAN
    ) -> None:
        """Release a lease on the device's own initiative, as its dead-man switch does."""
        lease = self.state.leases.pop(lease_id)
        script = self.state.script.release
        released = LeaseReleased(
            lease_id=lease.lease_id,
            cell_id=self.state.party.cell_id,
            holder=self.state.party.warden_id,
            cause=cause,
            is_restored=script.is_restored,
            killed_processes=self.state.kill_processes(lease_id),
            residual_paths=script.residual_paths,
            reason="Released by the scripted device on its own.",
        )
        self.state.released.append(released)
        await self._send(released)

    async def send_raw(
        self, payload: WaggleMessage, correlation_id: MessageId | None = None
    ) -> None:
        """Send any message to the Hive, for the tests that need a frame the services never send."""
        await self._send(payload, correlation_id)

    async def _dispatch(self, envelope: Envelope) -> None:
        """Record a frame, apply the script's refusals and silences, and run its handler."""
        self.state.received.append(envelope)
        kind = type(envelope.payload)
        if kind in self.script.silent:
            return
        code = self.script.refusals.get(kind)
        if code is not None:
            await self._ctx.error(envelope, code)
            return
        handler = self._handlers.get(kind)
        if handler is not None:
            await handler(envelope)

    async def _send(self, payload: WaggleMessage, correlation_id: MessageId | None = None) -> None:
        """Wrap, sign (the transport's codec does) and send one frame to the Hive."""
        transport = self._transport
        if transport is None:
            return
        state = self.state
        hop = Hop(
            sender=state.party.device_id, recipient=state.hive_id, node_id=state.party.node_id
        )
        envelope = wrap(payload, hop, clock=state.clock, correlation_id=correlation_id)
        async with self._send_lock:
            with contextlib.suppress(ConnectionLostError, TransportClosedError):
                await transport.send(envelope)
