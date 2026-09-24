"""Define the cells resource's read model: every Cell, its tiers, mode, lease and work at a glance.

The Observation Hive's Fleet list and Cell pages render a Cell from one model (codingrules 8.11:
views are data-shaped; tier and Mask state are always shown). ``CellView`` joins what the Hive
knows about a Cell from each place that knows it: the Cell record its Warden's link carries (kind,
source, Comb Shield tier, access level, capabilities), the Virtual Cell lifecycle's status for a
Virtual Cell, the Pheromone Trail (its newest lease edge, its newest lifecycle event, its Warden's
newest mode edge) and the Brood Chamber (the unfinished tasks placed on it). The Pheromone Mask
(the per-Cell tactic state of roadmap 6.13) is not tracked yet, so ``mask_state`` is always
present in the contract and null until it is. Nothing here is personal: names are the operator's
own labels and every other field an id, an enum, a flag or a time.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models.views``. Built
    by ``hivemind.entrance.reads.census``; answered by ``hivemind.entrance.routes.hive.cells`` and
    sent by the cell-status stream; published in the OpenAPI document. Calls into the Cell, Virtual
    Cell and lease enums and pydantic.

Key invariants:
    - ``comb_shield`` and ``mask_state`` are always present (codingrules 8.11), the second null
      until roadmap 6.13 tracks it.

See Also:
    - hivemind.entrance.reads.census for how each field is found.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import AccessLevel, CellKind, CombShieldLevel, LeaseState, OsFamily
from hivemind.hive import VirtualCellStatus
from waggle.messages.base import CellIdField, TaskIdField, WardenIdField

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

# The Pheromone Mask's states (roadmap 6.13), named here so the contract already carries them.
MaskStateName = Literal["OFF", "WARDEN", "QUEEN_FORCED"]

__all__ = ["CellList", "CellMode", "CellView", "MaskStateName"]


class CellMode(Enum):
    """What a Cell's Warden is doing with it: working, or only watching a Real Cell."""

    ACTIVE = "ACTIVE"  # Its Warden runs (or is ready to run) bees on it.
    WATCH = "WATCH"  # A Real Cell's Warden with no active bees, observing read-only.


class CellView(BaseModel):
    """One Cell, as the Fleet list and a Cell page show it (observe)."""

    model_config = _CONFIG

    id: CellIdField = Field(description="The Cell's id.")
    name: str = Field(description="Its label ('hive-stand'), or a Virtual Cell's image.")
    kind: CellKind = Field(description="REAL (borrowed) or VIRTUAL (provisioned).")
    source: str = Field(description="What produced it: hive_stand, swarm, or a Virtual backend.")
    comb_shield: CombShieldLevel = Field(description="Its security tier: always shown.")
    access_level: AccessLevel = Field(description="How much of it the Hive may touch.")
    mode: CellMode | None = Field(
        description="ACTIVE or WATCH from its Warden's newest mode edge on the trail; null "
        "before its Warden reported one (or while it is clustered, migrating or stopped)."
    )
    mask_state: MaskStateName | None = Field(
        default=None,
        description="The Pheromone Mask state (OFF, WARDEN or QUEEN_FORCED); null until roadmap "
        "6.13 tracks it per Cell.",
    )
    lease_state: LeaseState | None = Field(
        description="A Real Cell's lease from its newest lease edge on the trail: OPEN after "
        "cell.leased, RELEASED after cell.released; null when never leased here."
    )
    virtual_status: VirtualCellStatus | None = Field(
        description="A Virtual Cell's lifecycle status (PROVISIONING ... DESTROYED); null for a "
        "Real Cell or one this process does not track."
    )
    last_event: str | None = Field(description="Its newest cell.* trail event kind, if any.")
    last_event_at: datetime | None = Field(description="When that event happened.")
    warden_id: WardenIdField | None = Field(description="Its Warden; null when none is attached.")
    current_tasks: list[TaskIdField] = Field(description="Unfinished tasks placed on it.")
    os: OsFamily | None = Field(description="Its operating system family, when known.")
    has_display: bool = Field(description="A display is attached or running.")
    has_browser: bool = Field(description="A browser is installed.")
    can_host_model: bool = Field(description="It can run a local model server.")


class CellList(BaseModel):
    """Every Cell the Hive knows, Real and Virtual."""

    model_config = _CONFIG

    cells: list[CellView] = Field(description="The Cells, attached Wardens' first, then Virtual.")
