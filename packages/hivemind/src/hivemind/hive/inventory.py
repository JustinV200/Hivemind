"""Define the lifecycle's read views for placement: dormant Cells and backends with their room.

`hivemind.hive.lifecycle.CellLifecycle` reports two things the Queen's placement snapshot reads on
every pass: every dormant Cell it could resume, and every registered backend with the headroom it
has left. Both are hive-layer values, deliberately not the `hivemind.queen.placement.inventory`
types themselves (`hive` is Layer 3, `queen` Layer 6; the composition root converts). They moved
here from `lifecycle.py` in roadmap step 13.5 so that module could take Overwintering's eviction
under pressure within codingrules 5.1's file limit; `lifecycle` still re-exports both types.

Fits into the Hive:
    Layer 3 (sources of Cells). Called by `hivemind.hive.lifecycle.CellLifecycle`'s
    `dormant_candidates` and `virtual_backend_candidates`. Calls into `hivemind.cell`,
    `hivemind.hive.backends.base`, `hivemind.hive.cell_state`, `hivemind.hive.registry` and
    waggle only.

Key invariants:
    - Pure reads: nothing here changes a record, calls a backend or writes the trail.
    - A backend's `headroom` is narrowed to what is left after the Cells the lifecycle tracks on
      it, never widened.

See Also:
    - hivemind.hive.lifecycle for the table these views read.
    - hivemind.queen.dispatcher.snapshot for the conversion into placement's own types.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from hivemind.cell import CombShieldLevel
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.registry import BackendRegistry
from waggle.ids import CellId, WardenId

__all__ = [
    "LifecycleDormantCell",
    "LifecycleVirtualBackend",
    "backend_views",
    "dormant_views",
]


class TrackedCell(Protocol):
    """The fields of one `hivemind.hive.lifecycle.LiveVirtualCell` these views read."""

    @property
    def cell_id(self) -> CellId:
        """The Cell."""
        ...

    @property
    def status(self) -> VirtualCellStatus:
        """Its current status."""
        ...

    @property
    def backend(self) -> str:
        """The backend that holds it."""
        ...

    @property
    def image(self) -> str:
        """The image it was provisioned from."""
        ...

    @property
    def comb_shield(self) -> CombShieldLevel:
        """Its security tier."""
        ...

    @property
    def warden_id(self) -> WardenId | None:
        """Its Warden, once known."""
        ...


@dataclass(frozen=True, slots=True)
class LifecycleDormantCell:
    """One dormant Virtual Cell, as `CellLifecycle.dormant_candidates()` reports it.

    Attributes:
        cell_id: The dormant Cell itself.
        warden_id: Its own Warden, if still known (see `LiveVirtualCell.warden_id`).
        image: The image it was provisioned from.
        comb_shield: Its own security tier; never NIGHT_VEIL (the lifecycle's invariant).
    """

    cell_id: CellId
    warden_id: WardenId | None
    image: str
    comb_shield: CombShieldLevel


@dataclass(frozen=True, slots=True)
class LifecycleVirtualBackend:
    """One registered backend, as `CellLifecycle.virtual_backend_candidates()` sees it.

    `capabilities.headroom` is already narrowed to this backend's *remaining* room (its own
    declared cap minus how many Cells the lifecycle tracks on it), matching what the queen-layer
    type's own docstring requires of its caller.

    Attributes:
        name: The backend's own registry name.
        capabilities: What this backend can do, with `headroom` narrowed to remaining room.
    """

    name: str
    capabilities: BackendCapabilities


def dormant_views(records: Iterable[TrackedCell]) -> tuple[LifecycleDormantCell, ...]:
    """Return every DORMANT record as a LifecycleDormantCell.

    Args:
        records: Every Cell the lifecycle tracks.

    Returns:
        One view per dormant Cell, in `records`' order.
    """
    return tuple(
        LifecycleDormantCell(
            cell_id=record.cell_id,
            warden_id=record.warden_id,
            image=record.image,
            comb_shield=record.comb_shield,
        )
        for record in records
        if record.status is VirtualCellStatus.DORMANT
    )


def backend_views(
    records: Iterable[TrackedCell], registry: BackendRegistry
) -> tuple[LifecycleVirtualBackend, ...]:
    """Return every registered backend with its *remaining* headroom.

    Every record the lifecycle holds counts against its backend: the table never holds a
    DESTROYED or FAILED Cell, so every row is a Cell the backend still carries.

    Args:
        records: Every Cell the lifecycle tracks.
        registry: Every registered backend.

    Returns:
        One view per registered backend, in the registry's order.
    """
    counts = Counter(record.backend for record in records)
    views: list[LifecycleVirtualBackend] = []
    # Narrow each backend's declared cap by the Cells already on it; an uncapped one stays so.
    for name in registry.names():
        capabilities = registry.get(name).capabilities
        remaining = capabilities.headroom
        if remaining is not None:
            remaining = max(0, remaining - counts.get(name, 0))
        views.append(
            LifecycleVirtualBackend(
                name=name, capabilities=capabilities.model_copy(update={"headroom": remaining})
            )
        )
    return tuple(views)
