"""Define make_retire_all: tear down every Virtual Cell the lifecycle still tracks at Hive shutdown.

A Virtual Cell cannot outlive the Queen process that provisioned it today: its Warden holds a
link to this process's own listener port and verifies this process's own signing key (both
minted per process, `hivemind.cli.compose.virtual_cells`), so a dormant Cell left paused on the
backend after `hive run` exits can never be resumed by the next Queen -- which finds it through
`CellLifecycle.reconcile`, fails to resume it, and destroys it anyway (a real Docker run,
2026-09-23, left one paused container behind every run). Until the Queen's key and listener port
are stable across processes (`.claude/phase-5-virtual-cells-handoff.md`, open item 5), the honest
shutdown retires every live Cell here, so `docker ps` after `hive run` shows nothing this Hive
left behind and `hive cells abscond` is a backstop rather than a chore.

Per Cell, by its status:

- DESTROYING, DESTROYED, FAILED: nothing to do.
- DORMANT: `lifecycle.teardown` straight away, no quiesce. A paused Warden cannot answer a
  `CellTeardownRequest`, and its task's own trail rows already shipped before its `TaskResult`
  (`hivemind.wardens.ticks.trail_ship`), so nothing a quiesce could still save is inside it.
- Anything else (PROVISIONING, READY, GRANTED, RELEASED): `quiesce` first, exactly as
  `hivemind.queen.cell_gate.release.make_on_task_finished` does before a teardown, so the Warden
  inside stops its sub-bees and ships its final segment; then `lifecycle.teardown`.

Best-effort per Cell: one Cell's `hivemind.hive.errors.HiveError` never stops the others being
retired. The next Queen's reconcile and `hive cells abscond` remain the backstops for whatever
a backend refused to destroy.

Fits into the Hive:
    Layer 6 (the kernel). Built by `hivemind.cli.compose.virtual_cells.build_virtual_cells` and
    awaited by `hivemind.cli.compose.hive.run_hive` after the Queen has stopped (so no dispatch
    can race a teardown, or re-place a task whose Warden just detached) and before the listener
    stops (a quiesce still needs the Cells' own links). Calls into `hivemind.hive.cell_state`,
    `hivemind.hive.errors`, `hivemind.hive.lifecycle` and `hivemind.queen.cell_gate.quiesce`.

Key invariants:
    - Never resumes a dormant Cell in order to retire it, and never calls `quiesce` for one.
    - Every `lifecycle.teardown` of a non-dormant Cell is preceded by `quiesce` for that Cell.
    - Returns only after every tracked Cell has been attempted, whatever any one of them raised.

See Also:
    - hivemind.queen.cell_gate.release for the per-task release chain this mirrors at shutdown.
    - hivemind.queen.cell_gate.quiesce for what a quiesce asks of the Warden inside a Cell.
    - hivemind.cli.readback.virtual_abscond for the offline backstop over backend labels.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import HiveError
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.queen.cell_gate.quiesce import Quiesce
from waggle.ids import CellId

__all__ = ["RetireAll", "make_retire_all"]

RetireAll = Callable[[], Awaitable[tuple[CellId, ...]]]
"""Retire every Virtual Cell the lifecycle tracks; returns the ids that reached DESTROYED."""

_ALREADY_GONE = frozenset(
    {VirtualCellStatus.DESTROYING, VirtualCellStatus.DESTROYED, VirtualCellStatus.FAILED}
)


def make_retire_all(lifecycle: CellLifecycle, quiesce: Quiesce) -> RetireAll:
    """Build the callable `run_hive` awaits at shutdown, closed over `lifecycle` and `quiesce`.

    Args:
        lifecycle: Whose `live_cells()` are retired, each through its own `teardown`.
        quiesce: Awaited before the teardown of every Cell that is not DORMANT (module
            docstring); `hivemind.queen.cell_gate.quiesce.make_quiesce` builds the real one.

    Returns:
        An async callable that retires every tracked Cell and returns the ids it destroyed.
    """

    async def _retire_all() -> tuple[CellId, ...]:
        """Retire every tracked Cell, best-effort each; see the module docstring for the order."""
        retired: list[CellId] = []
        for live in lifecycle.live_cells():
            if live.status in _ALREADY_GONE:
                continue
            try:
                if live.status is not VirtualCellStatus.DORMANT:
                    await quiesce(live.cell_id)
                await lifecycle.teardown(live.cell_id)
            except HiveError:
                continue  # Best-effort: reconcile and `hive cells abscond` are the backstops.
            retired.append(live.cell_id)
        return tuple(retired)

    return _retire_all
