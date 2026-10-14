"""Run the Warden of a colonized Swarm device: connect to the Queen, lease the machine, serve.

This is the borrowed counterpart of `hivemind.cli.in_cell.main.run_in_cell_warden`'s own body, and
the whole of Level 1 of the runtime ladder from the device's side (roadmap step 11.6a). The Pollen
Packet starts it as a long-lived background process with the environment `config` reads. It opens
the durable stores, builds the Warden's link to the Queen (unconnected) and its deps, connects
(which announces the Cell: `cell.ready` with the level and tier the Queen granted the device, a
capacity report that lists the model servers the device manifest declares, a heartbeat), and then
starts and runs a real Warden. That Warden leases the machine itself, through the same machinery
the Hive Stand uses, so a task's commands run on the device with nothing between. It runs until it
is told to stop: a Queen-sent `Shutdown` stops it from inside its own tick, and a signal (the
packet's WITHDRAW, `SIGTERM`) is turned into the same stop by `stop`, so the lease is given back,
the device left as it was found, and the trail shipped, before the process exits.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.borrowed`. Called by
    `hivemind.cli.in_cell.main.run_in_cell_warden` when `HIVEMIND_CELL_LEASE` is `borrowed`. Calls
    into `hivemind.cell` (HiveStandSource, CellIdentity), `hivemind.cli.in_cell` (the hive-stand
    names, the borrowed modules), `hivemind.common`, `hivemind.wardens` (Warden) and waggle only.

Key invariants:
    - `stop` is the only way this function is asked to end other than a Queen-sent Shutdown or an
      error: it is set by the process's signal handlers (`stop_on_signals`) and by a test.
    - `Warden.stop()` is called by one caller at a time: by this function when `stop` fires, or by
      the Warden itself when the Queen says so, and again here only when the loop ended some other
      way and the Warden is not yet STOPPED; calling it twice at once would release the lease twice.
    - A stop while the Warden is still dialling the Queen for the first time ends the dial; no
      Warden has been started, so there is nothing to give back.
    - Everything on the way out is bounded by `BORROWED_SHUTDOWN_GRACE_S`: the packet that sent
      the signal kills the process when its own, longer, grace runs out. The stores are left open
      for the process to end with (`hivemind.cli.in_cell.borrowed.stores` says why).

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the move this completes.
    - hivemind.cli.in_cell.main for the owned counterpart and the process's entry point.
    - pollen.bootstrap.warden for the packet's side: the environment, the signal and the grace.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import signal
from collections.abc import Callable, Iterator

from hivemind.cell import CellIdentity
from hivemind.cell.local import HiveStandSource
from hivemind.cli.in_cell.borrowed.config import BorrowedConfig, build_borrowed_config
from hivemind.cli.in_cell.borrowed.deps import (
    BorrowedParts,
    build_borrowed_warden_deps,
    build_device_link,
)
from hivemind.cli.in_cell.borrowed.dial import LateTrailSync
from hivemind.cli.in_cell.borrowed.stores import DeviceStores, open_device_stores
from hivemind.cli.in_cell.hive_stand import hive_stand_addresses
from hivemind.common.logging import get_logger
from hivemind.common.tasks import reap, reaping
from hivemind.manifest import InCellEnv
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.offline import ReconnectingQueenLink
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden
from waggle.clock import Clock

# How long a stopping Warden may spend ending its sub-bees, releasing its lease (killing what the
# lease started, putting back what it changed, removing its scratch), shipping its last trail
# segment and closing the link. Longer than a Virtual Cell's, whose container is destroyed
# regardless: this one is a device that must be left as it was found. The packet's own grace before
# it forces the process (pollen.bootstrap) is longer still.
BORROWED_SHUTDOWN_GRACE_S = 20.0
# The signals that ask a Warden to stop: the service manager's and the terminal's, and Windows' own.
_STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGBREAK")

_LOG = get_logger(__name__)

__all__ = ["BORROWED_SHUTDOWN_GRACE_S", "run_borrowed_warden", "stop_on_signals"]


async def run_borrowed_warden(
    env: InCellEnv,
    clock: Clock,
    *,
    on_deps_built: Callable[[WardenDeps], None] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Connect a colonized device's Warden to the Queen and run it until it stops.

    Args:
        env: The process's environment, already read (codingrules section 13).
        clock: Injected time source for every id, sleep and timestamp.
        on_deps_built: Called once with the built WardenDeps, after the Warden has connected and
            announced and before it starts; for a test to script the provider.
        stop: Set to ask the Warden to stop and this function to return. None never asks.

    Raises:
        ConfigurationError: The environment or the device manifest is not what a device needs.
        OSError: The state directory or its stores cannot be opened.
    """
    config = build_borrowed_config(env, clock)
    # Once, at start: the Hive Stand as this device reaches it, for the Warden's floors.
    addresses = await hive_stand_addresses(config.runtime)
    config = dataclasses.replace(
        config, runtime=dataclasses.replace(config.runtime, hive_stand_addresses=addresses)
    )
    stores = await open_device_stores(config.state_dir, clock)
    stopping = stop if stop is not None else asyncio.Event()
    await _serve(config, stores, clock, (on_deps_built, stopping))


async def _serve(
    config: BorrowedConfig,
    stores: DeviceStores,
    clock: Clock,
    hooks: tuple[Callable[[WardenDeps], None] | None, asyncio.Event],
) -> None:
    """Build the link and the Warden, connect, and run until `stop` or the Queen ends it."""
    on_deps_built, stop = hooks
    runtime = config.runtime
    identity = CellIdentity(
        hive_id=runtime.hive_id, node_id=runtime.node_id, actor=str(runtime.warden_id)
    )
    source = HiveStandSource(config.cell, identity, stores.trail, clock, stores.leavings)
    trail_sync = LateTrailSync()
    link = build_device_link(config, source, stores, trail_sync, clock)
    deps = build_borrowed_warden_deps(config, BorrowedParts(source, link, stores), clock)
    # The link ships the trail after a reconnection through the Warden's own sync (dial.py).
    trail_sync.bind(deps.trail_sync)
    try:
        if not await _connected(link, stop):
            return
        if on_deps_built is not None:
            on_deps_built(deps)
        warden = Warden(runtime.warden_id, deps)
        await warden.start()
        _LOG.info("cell.warden.started", warden_id=runtime.warden_id, borrowed=True)
        await _run_until_stopped(warden, stop)
        _LOG.info("cell.warden.stopped", warden_id=runtime.warden_id, borrowed=True)
    finally:
        await _close(link)


async def _connected(link: ReconnectingQueenLink, stop: asyncio.Event) -> bool:
    """Connect the link (dialling until it holds), or give up when `stop` is set first.

    Returns:
        True once connected and announced; False if `stop` was set before that.
    """
    connecting = asyncio.ensure_future(link.connect())
    async with reaping(connecting), reaping(asyncio.ensure_future(stop.wait())) as stopping:
        # External await: the link dials with backoff for as long as the Queen is unreachable,
        # which is why the stop event is raced against it; nothing here has a deadline of its own.
        await asyncio.wait({connecting, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if not connecting.done():
            return False
        connecting.result()  # A connect that failed outright raises here.
        return True


async def _run_until_stopped(warden: Warden, stop: asyncio.Event) -> None:
    """Run the started Warden's loop until it ends, or `stop` ends it (key invariants above)."""
    run = asyncio.ensure_future(warden.run())
    async with reaping(asyncio.ensure_future(stop.wait())) as stopping:
        try:
            await asyncio.wait({run, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if not run.done():
                # Asked to stop from outside: stop() ends the loop first and cleans up after,
                # and nobody else calls it, so the lease is released once.
                await _stopped_within_grace(warden)
        finally:
            await _settle(warden, run)


async def _stopped_within_grace(warden: Warden) -> None:
    """Stop `warden` within the grace; a Warden that overruns it is left to the packet's force."""
    try:
        async with asyncio.timeout(BORROWED_SHUTDOWN_GRACE_S):
            await warden.stop()
    except TimeoutError:
        _LOG.warning("cell.warden.shutdown_timed_out", grace_s=BORROWED_SHUTDOWN_GRACE_S)


async def _settle(warden: Warden, run: asyncio.Future[None]) -> None:
    """Bring `warden` to STOPPED whatever ended its loop, then raise what the loop raised."""
    try:
        async with asyncio.timeout(BORROWED_SHUTDOWN_GRACE_S):
            await asyncio.wait({run})
            if warden.state is not WardenState.STOPPED:
                # The loop ended some other way (an error), not through a stop that already
                # released the lease: stop it now, once.
                await warden.stop()
    except TimeoutError:
        _LOG.warning("cell.warden.shutdown_timed_out", grace_s=BORROWED_SHUTDOWN_GRACE_S)
    finally:
        await reap(run)  # Cancels a loop that overran, and re-raises an error that ended it.


async def _close(link: ReconnectingQueenLink) -> None:
    """Close the link within the grace; a peer that is already gone is not worth a hang."""
    try:
        async with asyncio.timeout(BORROWED_SHUTDOWN_GRACE_S):
            await link.close()
    except TimeoutError:
        _LOG.warning("cell.link.close_timed_out", grace_s=BORROWED_SHUTDOWN_GRACE_S)


@contextlib.contextmanager
def stop_on_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> Iterator[None]:
    """Set `stop` when the process is asked to end by a signal; put the old handlers back after.

    Args:
        loop: The running event loop, on which `stop` is set.
        stop: The event `run_borrowed_warden` was given.

    Yields:
        Nothing; the handlers are in place for the block.
    """
    previous: dict[int, object] = {}
    for name in _STOP_SIGNALS:
        number = getattr(signal, name, None)
        if number is not None:
            # A plain handler that hands the set to the loop, not loop.add_signal_handler, which
            # Windows lacks; the packet's own CLI does the same for the same reason.
            previous[number] = signal.signal(number, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        yield
    finally:
        for number, handler in previous.items():
            if handler is not None:  # None: a handler not installed from Python; leave ours.
                signal.signal(number, handler)  # type: ignore[arg-type]
