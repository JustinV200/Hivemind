"""Define run_hive: lease the Hive Stand's Cell, run the Queen and Warden, and tear both down.

`run_hive` is an `asynccontextmanager`: it attaches the Hive Stand's Warden (the Queen's
`warden_spawn` point, roadmap step 10.3), opens the Virtual side when one is configured, opens the
Swarm when `[swarm] enabled` (roadmap step 11.6), leases the Hive Stand's one Cell, runs the Queen
and the Warden -- and the House Bee's ripening loop, when the Hive has a Honey Store (roadmap
phase 7), and the Swarm gate, which runs a Warden for each device that is up -- as background
tasks for as long as the `async with` block is open, and tears everything down -- releasing every
lease, "left as found" -- on exit.
Last of all, a task the stop caught on one of those Cells (an interrupted or timed-out `hive run`,
a `hive serve` shut down) is given back to PENDING, its question withdrawn, so the next start
places it afresh (`hivemind.queen.requeue_unfinished`): no task outlives the stop bound to a Cell
that is gone. A process that died without that teardown is recovered by the next start instead:
every start is a Requeening (ADR-0062). Once the Virtual side is reconciled, the readiness gate is
re-armed with the key of every Virtual Cell that outlived the last Queen (`rearm_gate`), and the
Queen's first half of Requeening runs (`begin_requeening`) before the Hive Stand's Warden takes its
lease and before her loop starts; her tick settles the rest.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.hive`. Called by
    `hivemind.cli.run`, `hivemind.cli.compose.entrance` and every end-to-end test. Calls into
    `hivemind.cli.compose.guard` (close_guard_bee), `.hive.build` (Hive), `hivemind.queen`
    (requeue_unfinished), `hivemind.queen.attach` (detach_warden, to unwind a failed start),
    `hivemind.queen.cell_gate` (rearm_gate), `hivemind.queen.requeening` (begin_requeening) and the
    Queen, Warden, House Bee and Swarm gate a Hive holds only.

Key invariants:
    - The House Bee's ripening loop starts after the Queen and stops before her, so she never
      runs a tick without it and it never ripens for a Queen already gone; a Hive with no Honey
      Store (a hand-built HiveStores) has no loop and runs exactly as before phase 7.
    - The Swarm gate starts after the Hive Stand's Warden is attached and the Virtual side is open,
      and stops before the Queen: its Wardens release their leases over the devices' links while
      she still hears them, and the listener closes those links only after. A Hive with
      `[swarm] enabled = false` has no gate and runs exactly as before phase 11.
    - A start that fails part-way leaves nothing running: the gate, the Virtual side's listener,
      the links and the Guard Bee are stopped before the failure reaches the caller.
    - `run_hive` always stops the Queen, stops the Warden (releasing its lease), awaits both of
      their `run()` tasks, closes the Guard Bee, the Virtual side, the Queen<->Warden link and
      then every model provider's connections, in that order, whether its `async with` block
      exits cleanly, raises or is cancelled (the teardown runs as a task of its own, so a
      cancelled caller cannot cut it short). Awaiting both tasks before its own
      `asyncio.TaskGroup` block ends is a shutdown-hygiene fix: without it, an exception
      propagating out of the caller's `async with run_hive(hive):` body would reach the TaskGroup
      while a tick might still be in flight, and the TaskGroup would cancel it itself rather than
      let the cooperative `stop()` finish.
    - The unfinished tasks are given back only after every Cell is gone (the lease released,
      every Virtual Cell retired): a Night Veil Cell's own purge has ended its tasks by then, and
      no Warden of this Hive is left to report on one.

See Also:
    - .claude/codingrules.md section 11 for the `asyncio.TaskGroup`/structured-concurrency rule
      `run_hive` follows.
    - hivemind.cli.compose.hive.build for build_hive, which builds what this runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from hivemind.cli.compose.guard import close_guard_bee
from hivemind.cli.compose.hive.build import Hive
from hivemind.common.tasks import run_to_end
from hivemind.hive import VirtualCellRecord
from hivemind.queen import requeue_unfinished
from hivemind.queen.attach import detach_warden
from hivemind.queen.cell_gate import rearm_gate
from hivemind.queen.requeening import RequeeningStart, begin_requeening
from waggle.ids import CellId

__all__ = ["run_hive"]


@asynccontextmanager
async def run_hive(hive: Hive) -> AsyncIterator[None]:
    """Lease the Hive Stand's Cell, run the Queen and Warden, and tear both down cleanly on exit.

    Args:
        hive: A Hive from `build_hive`, not yet started.

    Yields:
        Control to the caller, with `hive.queen` and `hive.warden` both ticking as background
        tasks; call `hive.queen.submit_goal`/`run_goal` inside the `async with` block.

    Raises:
        hivemind.queen.WardenSpawnRefusedError: The Guard refused the Queen `warden:spawn`; nothing
            was started (roadmap step 10.3).
    """
    await _start(hive)
    # Structured concurrency (codingrules section 11): both loops are owned by this one
    # asyncio.TaskGroup, which awaits them to completion when the block below exits, whether
    # cleanly or through an exception raised inside the caller's own `async with` body.
    async with asyncio.TaskGroup() as group:
        queen_task = group.create_task(hive.queen.run())
        warden_task = group.create_task(hive.warden.run())
        # After the Queen (module docstring): ripening runs beside her, never inside her tick.
        ripening_task = (
            group.create_task(hive.house_bee.run()) if hive.house_bee is not None else None
        )
        # Roadmap step 11.6: the gate runs every device's Warden as a task of its own group.
        swarm_task = group.create_task(hive.swarm.gate.run()) if hive.swarm is not None else None
        try:
            yield
        finally:
            # A task of its own (`run_to_end`): a caller cancelled (asyncio.run cancels its main
            # task on Ctrl+C), or cancelled again while this runs, still gets the whole teardown,
            # so no Virtual Cell is left running and no lease unreleased.
            await run_to_end(_tear_down(hive, (queen_task, warden_task), ripening_task, swarm_task))


async def _tear_down(
    hive: Hive,
    loops: tuple[asyncio.Task[None], asyncio.Task[None]],
    ripening_task: asyncio.Task[None] | None,
    swarm_task: asyncio.Task[None] | None,
) -> None:
    """Stop the ripening loop, the swarm, the Queen and the Warden, close up, give back tasks."""
    # Before the Queen (module docstring's "Key invariants").
    await _stop_ripening(hive, ripening_task)
    await _stop_swarm(hive, swarm_task)
    # Stop the Queen first (codingrules section 8.8: she holds no session, nothing to release),
    # then the Warden, which releases its lease -- "left as found" -- before its own run() loop is
    # allowed to end; both are cooperative signals (their own stop()), never a cancel, and awaited
    # to completion here -- before run_hive's TaskGroup's own __aexit__ runs -- so a caller's
    # exception propagating through its block can never have the TaskGroup itself cancel a tick
    # still in flight (this dispatch's own rule 4).
    await hive.queen.stop()
    await hive.warden.stop()
    await asyncio.gather(*loops)
    await _close_links(hive)
    # Last, with every Cell gone: a task the stop caught on one can only run again from PENDING
    # (a stop is no decision about its goal, so nothing is cancelled for it), and its question is
    # withdrawn for its fresh bee to ask again. Local Brood Chamber writes only.
    await requeue_unfinished(hive.queen_deps)


async def _close_links(hive: Hive) -> None:
    """Close what the stopped Queen and Warden leave open: Guard Bee, Cells, links, providers.

    Split out of `run_hive` for its own line budget (codingrules 5.1); called only once the Queen
    and the Warden have both fully stopped, so nothing still reads from what this closes.
    """
    await close_guard_bee(hive.queen_deps)  # Roadmap step 10.6: no episode left behind.
    if hive.virtual_cells is not None:
        # Every Virtual Cell, dormant ones included: hivemind.queen.cell_gate.shutdown.
        await hive.virtual_cells.retire_all()
        # Stop accepting and close every Virtual Cell connection last: nothing above this still
        # reads from a WardenLink once the Queen and Warden are both fully stopped.
        await hive.virtual_cells.listener.stop()
    # Closing the Queen's own end wakes the Warden's queen_link.receive() with a clean sentinel
    # (waggle.transport.memory.MemoryTransport.close's own contract), so nothing is left awaiting
    # a link neither side will ever write to again.
    await hive.warden_link.transport.close()
    # Last, once nothing can call a model any more: release every provider's pooled connections
    # (hivemind.llm.ProviderRegistry.aclose, bounded per provider), which a long-running `hive
    # serve` would otherwise leak for good.
    await hive.registry.aclose()


async def _start(hive: Hive) -> None:
    """Attach the Hive Stand's Warden, open the Virtual side and the Swarm, then lease the Cell.

    Split out of `run_hive` for codingrules 5.1's function length only; everything here happens
    before the Queen's and the Warden's own loops start. If a step after the first fails, what
    the earlier ones started is stopped before the failure propagates: a port that is already
    taken must not leave a listener, a Warden or a link behind in a process that carries on.
    """
    # Roadmap step 10.3: admitting the Hive Stand's Warden is the Queen's warden_spawn point, an
    # awaited Guard check (and a `warden.spawned` row), so it happens here, before anything runs.
    await hive.queen.attach_warden(hive.warden_link)
    try:
        await _open(hive)
    except BaseException:
        await _unwind(hive)
        raise


async def _open(hive: Hive) -> None:
    """Open the Virtual side and the Swarm, requeen, then lease the Hive Stand's one Cell."""
    listed: tuple[VirtualCellRecord, ...] = ()
    if hive.virtual_cells is not None:
        # Roadmap step 5.6: start accepting Virtual Cells' own control connections, THEN reconcile
        # the live table from every registered backend's own list_cells (hivemind.hive.lifecycle.
        # CellLifecycle.reconcile's own contract: called once, before any other method). The
        # listener goes first because reconcile constructs every backend, and a Docker or QEMU
        # backend's QueenEndpoint carries the listener's bound port, which only exists after
        # start() (the first real Docker run failed on exactly this). Both happen before
        # hive.warden.start()/the TaskGroup in run_hive, so a Cell dialling back in while the Queen
        # is still coming up is never dropped for connecting "too early". Before either, the
        # Docker control network the listener binds the gateway of (roadmap step 10.6a).
        await hive.virtual_cells.prepare()
        await hive.virtual_cells.listener.start(hive.queen)
        await hive.virtual_cells.lifecycle.reconcile(hive.manifest.hive.id)
        # ADR-0062: a Cell that outlived the last Queen proves itself with the key its labels
        # carry; without this, its redialing Warden would be refused at the handshake.
        gate, registry = hive.virtual_cells.gate, hive.virtual_cells.registry
        listed = await rearm_gate(gate, registry, hive.manifest.hive.id)
    if hive.swarm is not None:
        # Roadmap step 11.6: after the Hive Stand's Warden is attached and the Virtual side is open,
        # so the Queen has her own Cell to place on before a device's Warden joins her; devices
        # that dial in from here on wait for `gate.run()` to serve them.
        await hive.swarm.gate.start(hive.queen, hive.queen_deps)
    # Before the Hive Stand's Warden leases: any lease still open on its Cell is the dead
    # process's, and recovery gives it back (ADR-0062).
    await _requeen(hive, listed)
    await hive.warden.start()


async def _requeen(hive: Hive, listed: tuple[VirtualCellRecord, ...]) -> None:
    """Run the first half of Requeening with what only the composition root knows."""
    devices: frozenset[CellId] = frozenset()
    if hive.swarm is not None:
        # A device's tasks are the swarm gate's to hold (ADR-0053); local SQLite read.
        devices = frozenset(node.cell_id for node in await hive.swarm.registry.nodes())
    start = RequeeningStart(virtual_cells=listed, device_cells=devices, hive_stand=hive.source)
    await begin_requeening(hive.queen, start)


async def _unwind(hive: Hive) -> None:
    """Stop what `_start` had started before a later step failed: the gate, links and Guard Bee.

    The gate's `stop` is safe at any point of its own start (a listener that never bound does
    nothing). The Hive Stand's Warden, attached but never run, is detached, and the Queen, who
    never ran either, is stopped, since attaching a Warden started readers and jobs of hers (the
    tools she announces) that would otherwise outlive the failure.
    """
    if hive.swarm is not None:
        await hive.swarm.gate.stop()
    await detach_warden(hive.queen, hive.warden_link.warden_id)
    await hive.queen.stop()
    await _close_links(hive)


async def _stop_swarm(hive: Hive, swarm_task: asyncio.Task[None] | None) -> None:
    """Stop the Swarm gate (every device Warden releasing its lease, then the listener); wait.

    A no-op for a Hive with `[swarm] enabled = false`. `asyncio.wait`, not a bare await, for the
    reason `_stop_ripening` gives: should the gate itself have failed, `run_hive`'s TaskGroup
    reports that failure, and the Queen and Warden still stop cleanly after this returns.
    """
    if hive.swarm is None or swarm_task is None:
        return  # The Swarm is off: no gate was ever started.
    await hive.swarm.gate.stop()
    await asyncio.wait({swarm_task})


async def _stop_ripening(hive: Hive, ripening_task: asyncio.Task[None] | None) -> None:
    """Stop the House Bee's ripening loop and wait for it; a no-op for a Hive with no Honey Store.

    `stop()` cancels a pass in flight (each store write is its own transaction) and ends the
    pause at once, so shutdown never waits out a pass. `asyncio.wait`, not a bare await: should
    the loop itself have failed, `run_hive`'s TaskGroup reports that failure, and the Queen and
    Warden still stop cleanly after this returns.
    """
    if hive.house_bee is None or ripening_task is None:
        return  # No Honey Store wired: no loop was ever started.
    hive.house_bee.stop()
    await asyncio.wait({ripening_task})
