"""Equip a sub-bee with the Exoskeleton its task needs, and take it off when the sub-bee retires.

A task whose `TaskAssign.exoskeleton` is set (protocol 1.6) needs a display, input, audio or a
browser on its Cell for as long as its sub-bee runs (roadmap step 6.4, ADR-0031). `equip` attaches
exactly that, through the Warden's own session on its Cell and with the sub-bee's own attenuated
capabilities, before the sub-bee's runtime starts; `unequip` detaches it when the sub-bee is
stopped, whichever way that happens (a finished task, a respawn, the Warden stopping), because
`hivemind.wardens.spawn.spawn.stop_sub_bee` is the one path every retirement takes. A terminal-only
task gets nothing: `equip` returns None without touching the Cell, so no display process ever
exists for it. `gui_surface` builds the Capping gate's GUI surface over what was attached, with a
flight recorder when the Hive keeps recordings (roadmap step 6.6), so every GUI proposal the
sub-bee makes is applied, verified, rolled back and recorded by its gate (ADR-0032).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's spawn
    sub-package. Called by `hivemind.wardens.spawn.spawn` (spawn_sub_bee and stop_sub_bee). Calls
    into `hivemind.cell`, `hivemind.common.logging`, `hivemind.exoskeleton` (attach, the surface
    and the flight recorder), `hivemind.guard` and `hivemind.memory` (the recording's Bee Bread
    reference) only.

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

from hivemind.cell import HoneyClearance
from hivemind.cell.source import CellIdentity
from hivemind.common.logging import get_logger
from hivemind.exoskeleton import AttachDeps, ExoskeletonHandle, attach
from hivemind.exoskeleton.recorder import FlightRecorder, RecordingInfo
from hivemind.exoskeleton.surface import ExoskeletonSurface
from hivemind.guard import CapabilitySet
from hivemind.memory import MemoryContext, deposit_recording_ref
from waggle.ids import new_event_id
from waggle.messages.task import TaskAssign

if TYPE_CHECKING:
    from hivemind.wardens.spawn.spawn import WardenCellContext

log = get_logger(__name__)

__all__ = ["equip", "gui_surface", "unequip"]


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


async def gui_surface(
    ctx: WardenCellContext, assignment: TaskAssign, handle: ExoskeletonHandle | None
) -> ExoskeletonSurface | None:
    """Build the Capping gate's GUI surface over `handle`, recording when the Hive keeps recordings.

    Args:
        ctx: This Warden and the Cell it owns.
        assignment: The task the handle was attached for.
        handle: What `equip` attached, or None for a terminal-only task.

    Returns:
        The surface the sub-bee's gate applies GUI proposals through, or None without a handle.
    """
    if handle is None:
        return None
    deps = ctx.deps
    recorder = None
    if deps.recording_store is not None:
        clearance = HoneyClearance.from_wire(assignment.clearance)
        info = RecordingInfo(
            recording_id=str(new_event_id(deps.clock)),
            cell_id=str(ctx.cell.id),
            task_id=str(assignment.task_id),
            clearance=clearance.value,
            started_at=deps.clock.now(),
        )
        recorder = FlightRecorder(deps.recording_store, info, deps.clock)
        await recorder.open()
        # The frames stay in the store; Bee Bread holds the reference an episode reaches it by.
        memory = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
        await deposit_recording_ref(info.recording_id, assignment.task_id, clearance, memory)
    return ExoskeletonSurface(handle.peripherals, deps.clock, recorder)


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
