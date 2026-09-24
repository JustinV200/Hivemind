"""Equip a sub-bee with the Exoskeleton its task needs, and take it off when the sub-bee retires.

A task whose `TaskAssign.exoskeleton` is set (protocol 1.6) needs a display, input, audio or a
browser on its Cell for as long as its sub-bee runs (roadmap step 6.4, ADR-0031). `equip` attaches
exactly that, through the Warden's own session on its Cell and with the sub-bee's own attenuated
capabilities, before the sub-bee's runtime starts; `unequip` detaches it when the sub-bee is
stopped, whichever way that happens (a finished task, a respawn, the Warden stopping), because
`hivemind.wardens.spawn.spawn.stop_sub_bee` is the one path every retirement takes. A terminal-only
task gets nothing: `equip` returns None without touching the Cell, so no display process ever
exists for it.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's spawn
    sub-package. Called by `hivemind.wardens.spawn.spawn` (spawn_sub_bee and stop_sub_bee). Calls
    into `hivemind.cell` (CellIdentity), `hivemind.common.logging`, `hivemind.exoskeleton` (attach,
    AttachDeps, ExoskeletonHandle) and `hivemind.guard` only.

Key invariants:
    - A terminal-only assignment never causes a single command or process on the Cell.
    - `unequip` never raises: a peripheral that will not stop is logged and left to the lease's
      release, the backstop that kills every process the lease recorded.

See Also:
    - hivemind.exoskeleton.attach for what attach starts and how detach stops it.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for why needs travel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell.source import CellIdentity
from hivemind.common.logging import get_logger
from hivemind.exoskeleton import AttachDeps, ExoskeletonHandle, attach
from hivemind.guard import CapabilitySet
from waggle.messages.task import TaskAssign

if TYPE_CHECKING:
    from hivemind.wardens.spawn.spawn import WardenCellContext

log = get_logger(__name__)

__all__ = ["equip", "unequip"]


async def equip(
    ctx: WardenCellContext, assignment: TaskAssign, capabilities: CapabilitySet
) -> ExoskeletonHandle | None:
    """Attach the Exoskeleton `assignment` needs, or nothing for a terminal-only task.

    Args:
        ctx: This Warden and the Cell it owns.
        assignment: The task the sub-bee will run.
        capabilities: The sub-bee's own attenuated capabilities; attach plans against these.

    Returns:
        The attached handle, or None when the task needs no Exoskeleton.

    Raises:
        hivemind.exoskeleton.AttachError: The Cell cannot provide what the task needs.
    """
    if assignment.exoskeleton is None:
        return None
    deps = ctx.deps
    identity = CellIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor=deps.identity.actor
    )
    attach_deps = AttachDeps(
        trail=deps.trail,
        identity=identity,
        clock=deps.clock,
        config=deps.exoskeleton_config,
        browser_launcher=deps.browser_launcher,
    )
    return await attach(ctx.cell, ctx.session, assignment.exoskeleton, capabilities, attach_deps)


async def unequip(handle: ExoskeletonHandle | None) -> None:
    """Detach `handle`, if there is one; log anything that would not stop.

    Args:
        handle: The sub-bee's Exoskeleton, or None when it had none.
    """
    if handle is None:
        return
    report = await handle.detach()
    if report.still_running:
        # The trail already has the count (cell.exoskeleton_detached); the lease's release kills
        # every process it recorded, so this is a warning, not a failure of the retirement.
        log.warning("exoskeleton.detach_left_processes", still_running=report.still_running)
