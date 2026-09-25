"""Define the wardens resource's read model: each Warden's state, pulse, Cell and sub-bees.

A Warden (the always-on supervisor of one Cell) is known to the Queen through its link and its
Heartbeats, and nowhere durable: the Queen is the only global view (codingrules 8.8). ``WardenView``
is that view as an observing device reads it: the state its newest Heartbeat reported (or OFFLINE
once the Queen marked it so after missed Heartbeats), when she last heard from it, how many
Heartbeats it has missed, its Cell, how many sub-bees its last Heartbeat listed and how many live
Forage grants it holds. Telemetry text (a bee's goal line, its last actions) is not here: it is
``observe:thoughts`` and ``C2``, on the telemetry stream.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.views``. Built
    by ``hivemind.entrance.reads.census``; answered by ``hivemind.entrance.routes.hive.wardens``;
    published in the OpenAPI document. Calls into the wire WardenState and pydantic.

Key invariants:
    - No field carries telemetry text: counts, states and times only.

See Also:
    - hivemind.queen.ticks.liveness for how the Queen judges a Warden offline.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from waggle.messages.base import CellIdField, WardenIdField
from waggle.messages.supervision import WardenState

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

__all__ = ["WardenList", "WardenView"]


class WardenView(BaseModel):
    """One attached Warden, as the Queen sees it (observe)."""

    model_config = _CONFIG

    id: WardenIdField = Field(description="The Warden's id.")
    cell_id: CellIdField = Field(description="The Cell it supervises.")
    state: WardenState | None = Field(
        description="Its newest Heartbeat's state (STARTING, ACTIVE, WATCH, CLUSTERED, ...), or "
        "OFFLINE once the Queen marked it so; null before its first Heartbeat."
    )
    offline: bool = Field(description="The Queen marked it offline after missed Heartbeats.")
    last_heartbeat_at: datetime | None = Field(description="When its newest Heartbeat arrived.")
    missed_heartbeats: int = Field(description="Whole intervals since then, as she counts them.")
    sub_bees: int = Field(description="Sub-bees its newest Heartbeat listed.")
    live_grants: int = Field(description="Live Forage grants it holds in the Queen's ledger.")


class WardenList(BaseModel):
    """Every Warden attached to the Queen."""

    model_config = _CONFIG

    wardens: list[WardenView] = Field(description="The Wardens, in attachment order.")
