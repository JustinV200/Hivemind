"""Define Readings and read_world: what the Queen reads off the Hive once per pass to judge devices.

The Queen's own rung decisions weigh facts about the Hive, not about one device: how loaded the Hive
Stand is by the Level 0 Wardens' sub-bees, how strained the shared seats are, which devices have a
task that must survive a lost link, and which have any work on them at all. Those are read here,
once per reconcile pass, from what the Queen already holds: the Brood Chamber's unfinished tasks,
the Forage ledger (the Hive Stand's own reported capacity, the seats granted and the pool's total)
and the footprint of each role. Reading them once and handing every device the same snapshot keeps
the pass to one read of the chamber however many devices there are, and keeps one device's verdict
from depending on the order the pass met it in. The figures are defined in `.pressure`; this module
only gathers their inputs.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.rungs`. Called by `.evaluate` at the start of a pass. Calls into
    `hivemind.cell` (the Hive Stand's Cell id), `hivemind.forage`, `hivemind.queen.swarm_gate`
    (the unfinished tasks, the terms and the context), `hivemind.swarm` and waggle only.

Key invariants:
    - Reading changes nothing: no task, grant, ledger row or trail event is written.
    - The Hive Stand's capacity is the one its own Warden last reported; with none the relief
      pressure is 0, so the Queen never colonizes a device to relieve a machine she cannot size.
    - A bee is counted where it runs: a RUNNING task of a Level 0 device loads the Hive Stand, and a
      RUNNING task of a colonized device loads that device and is added back only on request.
    - A device with no running bee bears neither pressure: a Warden, or a model server, put on a
      device that has no bee at work takes nothing off the Hive Stand and no call off the pool, so
      the Queen never installs anything on a device to relieve a strain it has no part in.

See Also:
    - hivemind.queen.swarm_gate.rungs.pressure for the figures and exactly what they measure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import hive_stand_cell_id
from hivemind.forage import HostCapacity
from hivemind.queen.swarm_gate.rungs.needs import survival_cells
from hivemind.queen.swarm_gate.rungs.pressure import (
    BeeLoad,
    bees_load,
    relief_pressure,
    seat_pressure,
)
from hivemind.queen.swarm_gate.takeback import open_tasks
from hivemind.queen.swarm_gate.terms import GateContext
from hivemind.swarm import RuntimeLevel, SwarmNode
from waggle.ids import CellId

__all__ = ["Readings", "read_world"]


@dataclass(frozen=True, slots=True)
class Readings:
    """The Hive as it stands for one pass of the Queen's own rung decisions.

    Attributes:
        survival: The Cells a task that must survive a lost link is placed on or pinned to.
        busy: The Cells that have any unfinished task placed on them.
        stand: The load of the sub-bees of the Level 0 Wardens, on the Hive Stand.
        by_cell: The load of the running sub-bees of each Cell, for counting a device's own back in.
        host: The Hive Stand's capacity as its Warden last reported it; None when it has not.
        seats: The shared pool's seat pressure, 0 to 1; whose it is, `seats_for` says.
    """

    survival: frozenset[CellId]
    busy: frozenset[CellId]
    stand: BeeLoad
    by_cell: Mapping[CellId, BeeLoad] = field(default_factory=dict)
    host: HostCapacity | None = None
    seats: float = 0.0

    def share_of(self, node: SwarmNode) -> float:
        """Return the share of the Hive Stand `node`'s own running sub-bees take; 0 when off it."""
        load = self.by_cell.get(node.cell_id)
        if self.host is None or load is None or node.level is not RuntimeLevel.GATEWAY:
            return 0.0
        return relief_pressure(load, self.host)

    def relief_for(self, node: SwarmNode) -> float:
        """Return the Hive Stand's relief pressure as it bears on `node`; 0 if `node` has no bee.

        A device with no sub-bee running takes nothing off the Hive Stand by being colonized and
        puts nothing back by being taken back, so the Hive Stand's pressure is not a reason for
        it, whatever it is. A Level 0 device's bees are already on the Hive Stand, so they are in
        the figure. A device whose Warden runs on it is off the Hive Stand, and its running bees
        are counted back in: the figure then answers whether the Hive Stand would need relief if
        the device were taken back, which is the question for a device colonized for relief.
        """
        own = self.by_cell.get(node.cell_id)
        if self.host is None or own is None:
            return 0.0
        load = self.stand if node.level is RuntimeLevel.GATEWAY else self.stand.plus(own)
        return relief_pressure(load, self.host)

    def seats_for(self, node: SwarmNode) -> float:
        """Return the shared pool's seat pressure as it bears on `node`; 0 if `node` has no bee.

        A model server on a device whose sub-bees are not running takes no call off the shared
        pool, so the pool's strain is not a reason to give that device one.
        """
        return self.seats if node.cell_id in self.by_cell else 0.0


async def read_world(context: GateContext, nodes: Iterable[SwarmNode]) -> Readings:
    """Read the Hive once: the unfinished tasks, the Hive Stand's capacity and the seats.

    Args:
        context: The Queen and her collaborators.
        nodes: Every enrolled device, as the registry holds them; those on Level 0 are the ones
            whose sub-bees run on the Hive Stand.

    Returns:
        The readings.
    """
    deps = context.deps
    tasks = await open_tasks(deps)
    level0 = {node.cell_id for node in nodes if node.level is RuntimeLevel.GATEWAY}
    by_cell: dict[CellId, BeeLoad] = {}
    for task in tasks:
        if task.status is TaskStatus.RUNNING and task.cell_id is not None:
            load = bees_load(deps.footprints, [task.spec.role])
            by_cell[task.cell_id] = by_cell.get(task.cell_id, BeeLoad()).plus(load)
    stand = BeeLoad()
    for cell_id in level0:
        stand = stand.plus(by_cell.get(cell_id, BeeLoad()))
    reported = deps.ledger.capacity_for(hive_stand_cell_id(deps.identity.node_id))
    seats = seat_pressure(
        free_seats=deps.ledger.headroom().shared_seats,
        total_seats=deps.ledger.seats.total_capacity(),
        reserve_seats=deps.ledger.reserve.seats,
    )
    return Readings(
        survival=survival_cells(tasks),
        busy=frozenset(task.cell_id for task in tasks if task.cell_id is not None),
        stand=stand,
        by_cell=by_cell,
        host=None if reported is None else reported.host,
        seats=seats,
    )
