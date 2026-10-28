"""Define make_room: pick the oldest dormant Cells to evict so one fresher Cell fits the pool.

Roadmap step 13.5 (ADR-0066). Before this, a full Overwintering pool simply refused the next
released Cell (`policy.decide_release`'s room and disk rules), so at scale the pool filled once
with whatever went dormant first and kept it until each Cell's own `dormant_until`. A Cell just
released is the likeliest to be wanted again, so the pool now makes room for it instead: the
oldest dormant Cells go first, and only as many as the bounds need. `make_room` is that choice
and only that choice; it is pure, and the caller tears the chosen Cells down.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.overwinter`. Called by
    `hivemind.hive.overwinter.pool.OverwinterPool.room_for`, whose answer
    `hivemind.hive.lifecycle` weighs before `decide_release`. Calls into
    `hivemind.hive.overwinter.policy` (OverwinterConfig) and waggle only.

Key invariants:
    - `make_room` performs no I/O and reads no clock.
    - It returns either a set of evictions after which the incoming Cell fits every bound, or
      nothing at all: never a partial eviction that still leaves no room.
    - Victims are taken oldest first, and only a Cell of the incoming image is taken while the
      per-image bound is what blocks, since no other eviction would help that bound.

See Also:
    - docs/adr/0066-overwintered-cells-are-evicted-oldest-first-under-pressure.md.
    - hivemind.hive.overwinter.policy for the bounds this reads and the rule that then decides.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from hivemind.hive.overwinter.policy import OverwinterConfig
from waggle.ids import CellId

__all__ = ["Occupant", "make_room"]


@dataclass(frozen=True, slots=True)
class Occupant:
    """One dormant Cell, as `make_room` weighs it.

    Attributes:
        cell_id: The dormant Cell.
        image: The image it was provisioned from.
        disk_mb: The disk it holds, in megabytes, as the pool accounted it at admission.
        since: When it went dormant; the oldest goes first.
    """

    cell_id: CellId
    image: str
    disk_mb: int
    since: datetime


def make_room(
    occupants: Sequence[Occupant], image: str, disk_mb: int, config: OverwinterConfig
) -> tuple[CellId, ...]:
    """Return the oldest dormant Cells to evict so one more Cell of `image` fits every bound.

    Args:
        occupants: Every dormant Cell the pool holds.
        image: The incoming Cell's image.
        disk_mb: The incoming Cell's disk, in megabytes.
        config: The pool's bounds.

    Returns:
        The Cells to evict, oldest first; empty when it already fits, or when no eviction could
        make it fit (a Cell bigger than the whole disk budget, say).
    """
    kept = sorted(occupants, key=lambda occupant: occupant.since)
    victims: list[CellId] = []
    # Evict one at a time, oldest first, until every bound holds for the incoming Cell.
    while not _fits(kept, image, disk_mb, config):
        victim = _next_victim(kept, image, config)
        if victim is None:
            return ()  # Nothing left that would help: evict nothing rather than evict in vain.
        kept.remove(victim)
        victims.append(victim.cell_id)
    return tuple(victims)


def _fits(kept: Sequence[Occupant], image: str, disk_mb: int, config: OverwinterConfig) -> bool:
    """Whether one more Cell of `image` and `disk_mb` fits beside `kept`."""
    same_image = sum(1 for occupant in kept if occupant.image == image)
    disk_used = sum(occupant.disk_mb for occupant in kept)
    return (
        same_image < config.max_per_image
        and len(kept) < config.max_cells
        and disk_used + disk_mb <= config.disk_budget_mb
    )


def _next_victim(kept: Sequence[Occupant], image: str, config: OverwinterConfig) -> Occupant | None:
    """The oldest Cell whose eviction helps, or None when the pool is empty of helpful ones."""
    same_image = [occupant for occupant in kept if occupant.image == image]
    # A full image slot is freed only by a Cell of that image; any other bound by any Cell.
    candidates = same_image if len(same_image) >= config.max_per_image else kept
    return candidates[0] if candidates else None
