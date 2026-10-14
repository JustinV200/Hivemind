"""Define SwarmGate: the Queen's side of the Swarm, which keeps a Warden on each device that is up.

The swarm listener (Layer 3) is the door devices dial: it enrols, verifies and routes them, and
tells its hub who is connected. Nothing there supervises anything. The gate is what turns that
into Real Cells the Queen can place work on. It owns the listener and is the hub's listener, and it
keeps one Level 0 Warden per device (`hivemind.queen.swarm_gate.wardens`) in line with the registry
and the hub (`hivemind.queen.swarm_gate.reconcile`): a device that connects gets a Warden attached
to the Queen, a device whose link drops has its Warden told so, a device the operator revoked is
cut off, and a device whose terms changed gets a rebuilt Warden.

The gate does that work in a loop of its own, never inside the hub's callbacks. `on_online` and
`on_offline` run in the very task that reads a device's link (the monitor even calls `on_online`
from the link's reader when heartbeats resume), so they only note what happened and wake the loop;
the loop reads the registry and the hub afresh and does what is missing. The same pass runs every
`[swarm] heartbeat_interval_s`, so a revocation made by another process, or a notice that never
came, is found within one beat.

`run` owns a `TaskGroup` holding that loop and every device Warden's run loop. `stop` is the
shutdown order the Hive needs: the loop ends, every Warden stops (each releasing its lease while
its packet link is still up, so the device is left as found), and only then does the listener close
its connections.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen`. Built by
    `hivemind.cli.compose.swarm` when `[swarm] enabled`; `hivemind.cli.compose.hive.run_hive`
    starts it after the Hive Stand's Warden is attached, runs it beside the Queen, and stops it
    before her. Calls into `hivemind.queen.swarm_gate` (its own modules), `hivemind.swarm` (the
    listener, hub and registry), `hivemind.common.tasks` and waggle only.

Key invariants:
    - A hub callback never does I/O and never raises: it notes the change and sets an event.
    - Reconcile passes are serialised under one lock, and `stop` takes it, so no Warden is ever
      served after `stop` has begun stopping them.
    - `stop` never leaves a Warden running or a connection open, and is safe to call twice, or
      before `run`.
    - The listener is stopped after the Wardens, never before: a lease is released over the link.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for Level 0.
    - hivemind.queen.cell_gate for the equivalent door of Virtual Cells' Wardens.
    - hivemind.swarm.gateway.listener for the listener this gate owns.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, ClassVar

from hivemind.cell import CellError
from hivemind.common.logging import get_logger
from hivemind.common.tasks import reap_all
from hivemind.queen.swarm_gate.reconcile import DeviceReconciler, ReconcileDeps
from hivemind.queen.swarm_gate.unreachable import UnreachableDevices
from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.swarm import DeviceLink, SwarmError, SwarmListener, SwarmNode, SwarmRegistry
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.loop import TickLoop

if TYPE_CHECKING:
    from hivemind.queen.queen import Queen

log = get_logger(__name__)

__all__ = ["SwarmGate"]


class _GateLoop(TickLoop):
    """Run a reconcile pass every interval, and at once when the hub reports a change."""

    _recoverable_errors: ClassVar[tuple[type[Exception], ...]] = (
        SwarmError,
        CellError,
        sqlite3.Error,
    )

    def __init__(
        self,
        clock: Clock,
        interval_s: float,
        wake: asyncio.Event,
        sweep: Callable[[], Awaitable[None]],
    ) -> None:
        """Build the loop; `run()` starts it and `stop()` ends it.

        Args:
            clock: Drives the interval, so a test advances it instead of waiting.
            interval_s: Seconds between passes when the hub says nothing (`[swarm]
                heartbeat_interval_s`).
            wake: Set by anything that wants a pass now.
            sweep: One reconcile pass.
        """
        super().__init__(clock)
        self._interval_s = interval_s
        self._wake = wake
        self._sweep = sweep

    async def _tick(self) -> None:
        """Wait for the interval, a wake-up or the stop, then run one pass."""
        waits = [
            asyncio.ensure_future(self._wake.wait()),
            asyncio.ensure_future(self._clock.sleep(self._interval_s)),
            asyncio.ensure_future(self._stop.wait()),
        ]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await reap_all(waits)  # Whichever did not finish is cancelled, never left pending.
        if self._stop.is_set():
            return
        # Cleared before the pass, not after: a change that arrives during it asks for another.
        self._wake.clear()
        await self._sweep()

    async def _on_tick_failed(self, error: Exception) -> None:
        """Log a pass that failed for a reason the loop recovers from, by type only."""
        log.error("swarm.gate_tick_failed", error=type(error).__name__)


class SwarmGate:
    """Own the swarm listener and keep a Warden on every device that is up."""

    def __init__(
        self,
        listener: SwarmListener,
        registry: SwarmRegistry,
        wardens: DeviceWardens,
        interval_s: float,
        clock: Clock,
    ) -> None:
        """Build the gate; `start` binds the listener and `run` keeps the Wardens.

        Args:
            listener: The swarm listener this gate owns, starts and stops.
            registry: Who is enrolled; read at every pass.
            wardens: The table of device Wardens it keeps in line.
            interval_s: Seconds between passes (`[swarm] heartbeat_interval_s`).
            clock: Drives the interval.
        """
        self._listener = listener
        self._wardens = wardens
        self._unreachable = UnreachableDevices(wardens)
        deps = ReconcileDeps(registry, listener, wardens, self._unreachable)
        self._reconciler = DeviceReconciler(deps)
        # Set from the start, so the first pass serves any device that connected before `run`.
        self._wake = asyncio.Event()
        self._wake.set()
        self._lock = asyncio.Lock()
        self._loop = _GateLoop(clock, interval_s, self._wake, self._sweep)
        self._stopping = False
        self._finished = asyncio.Event()

    @property
    def listener(self) -> SwarmListener:
        """The swarm listener this gate owns."""
        return self._listener

    @property
    def wardens(self) -> DeviceWardens:
        """The table of device Wardens this gate serves."""
        return self._wardens

    async def start(self, queen: Queen) -> None:
        """Bind the Wardens to `queen`, listen to the hub, and open the listener.

        Args:
            queen: The Queen every device Warden is attached to.
        """
        self._wardens.bind(queen)
        self._listener.hub.subscribe(self)
        await self._listener.start()

    async def run(self) -> None:
        """Keep the Wardens in line until `stop`, running each as a task of this gate's group."""
        try:
            async with asyncio.TaskGroup() as group:
                self._wardens.open(group)
                group.create_task(self._loop.run())
                await self._finished.wait()
                # Leaving the block awaits the loop and every Warden's run task: `stop` has ended
                # them all by now, and a task that is somehow still going is cancelled with it.
        finally:
            self._wardens.close()

    async def stop(self) -> None:
        """Stop the loop, every device Warden (releasing its lease) and then the listener."""
        async with self._lock:
            if not self._stopping:
                self._stopping = True
                self._loop.stop()
                self._wake.set()  # The loop is waiting on this too, and now sees the stop.
                self._listener.hub.unsubscribe(self)
                # Before the listener closes the links: a lease is released over its link.
                await self._wardens.stop_all()
                await self._listener.stop()
        self._finished.set()

    async def on_online(self, node: SwarmNode, link: DeviceLink) -> None:
        """Wake the loop: a device connected, or its heartbeats resumed."""
        # Not reconciled here: this runs in the task that reads the device's link.
        self._wake.set()

    async def on_offline(self, node: SwarmNode, reason: str) -> None:
        """Note why a device went away, and wake the loop to tell its Warden."""
        self._unreachable.note(node.device_id, reason)
        self._wake.set()

    async def on_frame(self, node: SwarmNode, envelope: Envelope) -> None:
        """Ignore a frame nobody was waiting for: a device's own Warden hears those."""

    async def _sweep(self) -> None:
        """Run one reconcile pass, unless the gate is already stopping."""
        async with self._lock:
            if not self._stopping:
                await self._reconciler.sweep()
