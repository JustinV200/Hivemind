"""Define make_quiesce: ask a Virtual Cell's own Warden to flush its trail before it is torn down.

THE DEFECT this closes (found on a real Docker run, named in this dispatch's own brief): once a
task on a Virtual Cell finishes, `hivemind.queen.cell_gate.release.make_on_task_finished` used to
call `hivemind.hive.lifecycle.CellLifecycle.teardown` straight away, which calls `backend.destroy`
-- for Docker, killing the container outright. The in-Cell Warden's own final trail sync
(`hivemind.wardens.warden.Warden.stop`'s own last step, `_sync_trail`) never got a chance to run,
so every `worker.spawned`/`capping.*`/`worker.done` event that Warden ever recorded, which lives
only in that container's own local `MemoryPheromoneTrail` (ADR-0027: "a store that dies with the
Cell"), was lost -- the Queen's own trail held only her own node id for that Cell's whole task.

`make_quiesce` is the fix: it gives the Warden a chance to leave cleanly first. It looks the Cell's
own `hivemind.queen.deps.WardenLink` up in the live Queen's `wardens` (the same late-bound view
`hivemind.queen.cell_gate.provider.LifecycleVirtualCellProvider` already reads, reused here rather
than adding a second `bind_queen` call site to `hivemind.cli.compose.hive._assemble_hive`, which is
not in this dispatch's allowed-to-fix list), sends a graceful `waggle.messages.cell.
CellTeardownRequest` over it, and waits up to `grace_s` for the link to detach -- which only happens
once `hivemind.wardens.ticks.control.handle_stop` has run the Warden's own `stop()` to completion,
`_sync_trail` included, and the closed connection has made `hivemind.queen.cell_gate.listener.
CellListener._handle`'s own `finally` call `hivemind.queen.attach.detach_warden`. A timeout is not
an error: `hivemind.hive.lifecycle.CellLifecycle.teardown`'s own `backend.destroy` is always the
backstop, so a Warden that never answers still gets torn down, just without its last few events.

`CellTeardownRequest` over `waggle.messages.control.protocol.Shutdown`: both map to
`WardenAction.STOP` in `hivemind.wardens.autopilot.table.decide` and both reach the same
`handle_stop` (`hivemind.wardens.ticks.control`'s own module docstring names both), but
`CellTeardownRequest` is the one the cell family itself defines for exactly this ask ("retire a
Virtual Cell, gracefully or immediately" -- `waggle.messages.cell.leases`'s own module docstring),
carries a `cause` this call fills as `COMPLETED` (the only requestable cause that fits a task that
just finished) and an explicit `lease_id=None` ("asks for the whole Virtual Cell to be retired").
Neither message gets a reply from `handle_stop` (it only calls `stop()`, never answers), so nothing
here waits on a correlated reply; detecting the Warden actually leaving is done by polling
`queen.wardens` instead (`_wait_until_detached`), the only signal this module has without touching
`hivemind.queen.attach`/`hivemind.queen.queen` to add an event neither is in this dispatch's
allowed-to-fix list to add.

**Overwinter is deliberately not handled here.** `hivemind.queen.cell_gate.release.
make_on_task_finished` only calls the callable this factory builds on a path that is already
heading to `lifecycle.teardown` (module docstring there): a Cell about to be paused, not destroyed,
must keep its Warden running so the pause can later resume it, and a `CellTeardownRequest`/
`Shutdown` would end that Warden for good, which `overwinter()` never wants. That Cell's own
locally-recorded events are still at risk of the same loss this module closes for teardown --
named honestly as a follow-up, not solved here: the fix is a new waggle message ("flush your trail
now, but keep running"), which does not exist yet and is out of this dispatch's scope.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`. Built by
    the composition root (`hivemind.cli.compose.virtual_cells.build_virtual_cells`) and set on
    `hivemind.queen.cell_gate.release.make_on_task_finished`'s own `quiesce` parameter. Calls into
    `hivemind.queen.deps` (WardenLink) and waggle (clock, envelope, ids, messages) only.

Key invariants:
    - The built callable never raises: a Cell not currently attached (already gone, or the Queen
      is not yet bound) is a no-op, and a timed-out wait is not an error either (module docstring).
    - `_wait_until_detached` never blocks past `grace_s`, measured on the injected `Clock`, never a
      real timer (so a test drives it with a `FakeClock`).

See Also:
    - .claude/roadmap.md step 5.9 for the release-then-decide edge this module makes graceful.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the in-Cell
      Warden's own final trail sync this module gives a chance to run.
    - hivemind.queen.cell_gate.release for make_on_task_finished, this module's one caller.
    - hivemind.wardens.ticks.control for handle_stop, what a CellTeardownRequest triggers Cell-side.
    - hivemind.queen.cell_gate.listener for CellListener, whose closed-connection detach this
      module polls `queen.wardens` for.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from hivemind.queen.deps import WardenLink
from waggle.clock import Clock
from waggle.envelope import wrap
from waggle.ids import CellId, WardenId
from waggle.messages.cell import CellTeardownRequest, ReleaseCause
from waggle.messages.labels import Urgency

__all__ = ["Quiesce", "make_quiesce"]

# How long to give a Cell's own Warden to stop and ship its final trail segment before this call
# gives up and lets the caller proceed to teardown/overwinter's own backend call regardless
# (module docstring: the backend destroy is always the backstop). Comfortably above
# hivemind.cli.in_cell.main.SHUTDOWN_GRACE_S (3.0s, the Warden's own bound on stopping sub-bees,
# releasing its lease and shipping its trail) plus one real network round trip.
_DEFAULT_GRACE_S = 5.0
# How often _wait_until_detached rechecks queen.wardens while waiting: short enough that a fast
# Warden's own detach is noticed quickly, coarse enough not to busy-loop the event loop.
_POLL_INTERVAL_S = 0.05

Quiesce = Callable[[CellId], Awaitable[None]]


class _WardensView(Protocol):
    """Structural stand-in for `hivemind.queen.queen.Queen`'s own `.wardens` property.

    A private copy of the identically-shaped Protocol `hivemind.queen.cell_gate.provider` already
    defines, not imported from there: codingrules section 5.4 makes a leading-underscore name
    private to its own module, and a real `Queen` (or that module's own `_WardensView`) already
    satisfies this one structurally, with no shared base needed.
    """

    @property
    def wardens(self) -> tuple[WardenLink, ...]:
        """Every Warden currently attached, in attachment order."""
        ...


def make_quiesce(
    queen_getter: Callable[[], _WardensView | None],
    clock: Clock,
    *,
    grace_s: float = _DEFAULT_GRACE_S,
) -> Quiesce:
    """Build the `quiesce` callable `make_on_task_finished` calls before tearing a Cell down.

    Args:
        queen_getter: Returns the live Queen (anything with a `.wardens` property), or `None`
            before one is bound yet. A zero-argument callable, not a bare reference, because the
            Queen this reads is late-bound the same way `LifecycleVirtualCellProvider.bind_queen`
            is (module docstring): the composition root builds this closure before a `Queen`
            exists, over `provider.queen` (a property reading that same provider's own late-bound
            reference), so no second `bind_queen` call site is needed.
        clock: Bounds `grace_s` and stamps the `CellTeardownRequest` envelope; injected so a test
            drives the wait with a `FakeClock` instead of a real timer.
        grace_s: The most this call waits for the Warden to detach after asking it to stop.
            Defaults to `_DEFAULT_GRACE_S` (~5s, module docstring's own reasoning).

    Returns:
        An async callable taking one `CellId`, for `OnTaskFinished`'s own teardown branches.
    """

    async def _quiesce(cell_id: CellId) -> None:
        """Ask `cell_id`'s own Warden to stop and flush, then wait up to `grace_s` for it to."""
        queen = queen_getter()
        if queen is None:
            return  # No Queen bound yet: acquire() never ran, so no Cell exists to quiesce.
        link = _find_link(queen, cell_id)
        if link is None:
            return  # Not currently attached (already gone, or never was): nothing to signal.
        await _send_teardown_request(link, clock)
        await _wait_until_detached(queen_getter, link.warden_id, clock, grace_s)

    return _quiesce


def _find_link(queen: _WardensView, cell_id: CellId) -> WardenLink | None:
    """Return `cell_id`'s currently attached WardenLink, or None."""
    return next((link for link in queen.wardens if link.cell.id == cell_id), None)


async def _send_teardown_request(link: WardenLink, clock: Clock) -> None:
    """Send a graceful CellTeardownRequest over `link`, asking its Warden to stop and flush.

    `handle_stop` (`hivemind.wardens.ticks.control`) answers this the same way it answers a
    `Shutdown`: it calls `Warden.stop()`, whose own last step ships this Cell's final trail
    segment before the tick loop actually ends (module docstring).
    """
    message = CellTeardownRequest(
        cell_id=link.cell.id,
        lease_id=None,  # None asks for the whole Virtual Cell to be retired (its own docstring).
        urgency=Urgency.GRACEFUL,  # Let the Warden checkpoint and ship its trail before it stops.
        cause=ReleaseCause.COMPLETED,  # The only path this is ever called from: a finished task.
        reason="Queen is releasing this Cell; flush the trail before teardown.",
    )
    await link.transport.send(wrap(message, link.hop, clock=clock))


async def _wait_until_detached(
    queen_getter: Callable[[], _WardensView | None],
    warden_id: WardenId,
    clock: Clock,
    grace_s: float,
) -> None:
    """Poll `queen.wardens` until `warden_id` is gone, or `grace_s` elapses (never an error).

    Polls rather than awaiting an event: nothing published by `hivemind.queen.attach.detach_warden`
    is reachable from here without touching `queen.attach`/`queen.queen` (module docstring: neither
    is in this dispatch's allowed-to-fix list to add one to).
    """
    deadline = clock.monotonic() + grace_s
    while _still_attached(queen_getter(), warden_id):
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            return  # Timed out: the caller's own backend destroy/pause is the backstop.
        await clock.sleep(min(_POLL_INTERVAL_S, remaining))


def _still_attached(queen: _WardensView | None, warden_id: WardenId) -> bool:
    """Return whether `warden_id` is still attached to `queen` (False once `queen` is gone too)."""
    if queen is None:
        return False
    return any(link.warden_id == warden_id for link in queen.wardens)
