"""Define run_hive: lease the Hive Stand's Cell, run the Queen and Warden, and tear both down.

`run_hive` is an `asynccontextmanager`: it attaches the Hive Stand's Warden (the Queen's
`warden_spawn` point, roadmap step 10.3), opens the Virtual side when one is configured, leases
the Hive Stand's one Cell, runs the Queen and the Warden -- and the House Bee's ripening loop,
when the Hive has a Honey Store (roadmap phase 7) -- as background tasks for as long as the `async
with` block is open, and tears everything down -- releasing the lease, "left as found" -- on exit.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.hive`. Called by
    `hivemind.cli.run`, `hivemind.cli.compose.entrance` and every end-to-end test. Calls into
    `hivemind.cli.compose.guard` (close_guard_bee), `.hive.build` (Hive) and the Queen, Warden
    and House Bee a Hive holds only.

Key invariants:
    - The House Bee's ripening loop starts after the Queen and stops before her, so she never
      runs a tick without it and it never ripens for a Queen already gone; a Hive with no Honey
      Store (a hand-built HiveStores) has no loop and runs exactly as before phase 7.
    - `run_hive` always stops the Queen, stops the Warden (releasing its lease), awaits both of
      their `run()` tasks, closes the Guard Bee, the Virtual side, the Queen<->Warden link and
      then every model provider's connections, in that order, whether its `async with` block
      exits cleanly or raises. Awaiting both tasks before its own `asyncio.TaskGroup` block ends
      is a shutdown-hygiene fix: without it, an exception propagating out of the caller's `async
      with run_hive(hive):` body would reach the TaskGroup while a tick might still be in flight,
      and the TaskGroup would cancel it itself rather than let the cooperative `stop()` finish.

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
        try:
            yield
        finally:
            # Before the Queen (module docstring's "Key invariants").
            await _stop_ripening(hive, ripening_task)
            # Stop the Queen first (codingrules section 8.8: she holds no session, nothing to
            # release), then the Warden, which releases its lease -- "left as found" -- before its
            # own run() loop is allowed to end; both are cooperative signals (their own stop()),
            # never a cancel, and awaited to completion right here -- before this TaskGroup's own
            # __aexit__ runs -- so a caller's exception propagating through this block can never
            # have the TaskGroup itself cancel a tick still in flight (this dispatch's own rule 4).
            await hive.queen.stop()
            await hive.warden.stop()
            await asyncio.gather(queen_task, warden_task)
            await _close_links(hive)


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
    """Attach the Hive Stand's Warden, open the Virtual side, then lease the Hive Stand's Cell.

    Split out of `run_hive` for codingrules 5.1's function length only; everything here happens
    before the Queen's and the Warden's own loops start.
    """
    # Roadmap step 10.3: admitting the Hive Stand's Warden is the Queen's warden_spawn point, an
    # awaited Guard check (and a `warden.spawned` row), so it happens here, before anything runs.
    await hive.queen.attach_warden(hive.warden_link)
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
    await hive.warden.start()


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
