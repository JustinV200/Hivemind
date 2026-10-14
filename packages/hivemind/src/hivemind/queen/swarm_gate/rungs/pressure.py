"""Define the two pressure figures the Queen's own rung decisions weigh, each in exactly one place.

The Queen decides to move a device up the ladder when the Hive Stand needs relief or the shared
model seats are under strain, and each of those is a figure between 0 and 1 compared with a
threshold the operator set in the manifest. A figure that two readers compute two ways is a
threshold nobody can reason about, so each is defined here, with what it measures said exactly.

**Relief pressure** (`[swarm.ladder] relief_pressure`) is the share of the Hive Stand that the
sub-bees of Level 0 Wardens take. At Level 0 a device's Warden runs on the Hive Stand, in the
Queen's own process, so each of its running sub-bees costs the Hive Stand what its role's footprint
says (`[forage.roles.<role>]`: cores and memory). The pressure is the larger of two shares: the
cores those sub-bees occupy over the Hive Stand's cores, and the memory they occupy over the Hive
Stand's memory, clamped to 1. It deliberately counts only what colonizing a device would take
off the Hive Stand: the Queen, the Hive Stand's own Cell and anything else running there are not
counted. When the Queen asks whether a device she colonized should still be colonized, she counts
that device's own sub-bees back in (`BeeLoad.plus`), because pressure measured with the device away
would fall below the threshold exactly because the device is away, and taking it back would raise
it again.

**Seat pressure** (`[swarm.nuc] seat_pressure`) is the share of the shared model pool's seats that
live grants hold. The pool's seats are the sum of the total seats every reported shared source
declares, less the seats the Royal Reserve keeps back for the Queen herself; the seats in use are
those the live grants have reserved, which the ledger already reports as the headroom left
(`Headroom.shared_seats`). The pressure is 1 minus that headroom over those usable seats, so it is
1 exactly when the pool has no headroom left, which is when the hosting plan already counts the
shared pool as under seat pressure (`queen.forage.hosting`). A pool with no usable seats at all has
no pressure: there is nothing to relieve.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.rungs`. Called by `.readings`, which gathers the figures' inputs
    from the Queen's ledger and Brood Chamber. Calls into `hivemind.forage` (the capacity types)
    and waggle only.

Key invariants:
    - Pure: no I/O, no clock; the same inputs give the same figure.
    - A figure is between 0 and 1 inclusive, whatever the inputs; a host or a pool of nothing has
      no pressure rather than an infinite one.
    - The relief figure counts a bee only once, at the machine it runs on; adding a device's bees
      back is the caller's explicit act.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the decisions these figures feed.
    - hivemind.swarm.level and hivemind.swarm.nuc for the thresholds' comparison.
    - hivemind.queen.forage.ledger.model for `Headroom`, the seat figure's source.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from hivemind.forage import HostCapacity, RoleFootprint
from waggle.messages.task import WorkerRole

__all__ = ["BeeLoad", "bees_load", "relief_pressure", "seat_pressure"]


@dataclass(frozen=True, slots=True)
class BeeLoad:
    """What some sub-bees ask of one machine while they run: cores and memory.

    Attributes:
        cpu_cores: The logical cores they occupy, summed over their roles' footprints.
        memory_bytes: The memory they occupy, summed the same way.
    """

    cpu_cores: float = 0.0
    memory_bytes: int = 0

    def plus(self, other: BeeLoad) -> BeeLoad:
        """Return the load of both sets of bees together."""
        return BeeLoad(self.cpu_cores + other.cpu_cores, self.memory_bytes + other.memory_bytes)


def bees_load(
    footprints: Mapping[WorkerRole, RoleFootprint], roles: Iterable[WorkerRole]
) -> BeeLoad:
    """Return what one running sub-bee of each of `roles` costs, summed.

    Args:
        footprints: What one bee of each role costs its Cell (`[forage.roles.<role>]`).
        roles: The role of each running sub-bee, one entry per bee. A role with no footprint
            costs nothing here, since nothing says what it costs.

    Returns:
        The summed load.
    """
    cores, memory = 0.0, 0
    for role in roles:
        footprint = footprints.get(role)
        if footprint is not None:
            cores += footprint.cpu_cores
            memory += footprint.memory_bytes
    return BeeLoad(cores, memory)


def relief_pressure(load: BeeLoad, host: HostCapacity) -> float:
    """Return the share of the Hive Stand's cores or memory that `load` takes, from 0 to 1.

    Args:
        load: The sub-bees of the Level 0 Wardens that run on the Hive Stand (plus, for a device
            the Queen asks whether to take back, that device's own).
        host: The Hive Stand's own capacity as its Warden last reported it.

    Returns:
        The larger of the cores share and the memory share, clamped to 1; 0 for a host that
        reports no cores or no memory, which has nothing to be pressed.
    """
    cores = load.cpu_cores / host.cores if host.cores > 0 else 0.0
    memory = load.memory_bytes / host.memory_bytes if host.memory_bytes > 0 else 0.0
    return min(1.0, max(cores, memory))


def seat_pressure(*, free_seats: int, total_seats: int, reserve_seats: int) -> float:
    """Return the share of the shared pool's usable seats that live grants hold, from 0 to 1.

    Args:
        free_seats: The seats the ledger still has free for new grants (`Headroom.shared_seats`).
        total_seats: The sum of the total seats every reported shared source declares.
        reserve_seats: The seats the Royal Reserve keeps back for the Queen herself.

    Returns:
        1 minus the free seats over the usable ones (the total less the reserve), clamped to the
        range; 0 when the pool has no usable seats.
    """
    usable = total_seats - reserve_seats
    if usable <= 0:
        return 0.0
    return min(1.0, max(0.0, 1.0 - free_seats / usable))
