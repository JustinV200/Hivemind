"""Define the forage resource's read models: capacity, live grants, headroom and the Royal Reserve.

Forage is the Hive's capacity in several dimensions (codingrules 8.10), divided by the Queen by
grant. Her ledger is the live book of it, and ``ForageView`` is that book as the Observation Hive's
Forage view reads it (ADR-0032: reads go to the stores directly): every Cell's latest capacity
report, every live grant (a lease with an expiry and budgets), the headroom those leave in the
shared pool, and the Royal Reserve held back first. A grant's reason and its model bindings stay
out: the reason is the allocator's prose and the bindings name model ids that belong to the LLM
view. ``grant_view`` and ``headroom_view`` are also what the Forage delta stream sends.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models.views``. Built
    by ``hivemind.entrance.reads.forage``; answered by ``hivemind.entrance.routes.hive.forage``
    and sent by the Forage delta stream; published in the OpenAPI document. Calls into the Forage
    models, the Queen's ``Headroom`` and pydantic.

Key invariants:
    - Figures only: no grant reason, no model id, nothing personal.

See Also:
    - hivemind.queen.forage.ledger for the book these views read.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.forage import ForageCapacity, ForageGrant, GrantState, RoyalReserve
from hivemind.queen import ForageLedger
from hivemind.queen.forage.ledger import Headroom
from waggle.messages.base import CellIdField, GrantIdField, TaskIdField, WardenIdField

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

__all__ = [
    "CapacityView",
    "ForageView",
    "GrantView",
    "HeadroomView",
    "ReserveView",
    "capacity_view",
    "forage_view",
    "grant_view",
    "headroom_view",
    "reserve_view",
]


class CapacityView(BaseModel):
    """One Cell's latest capacity report."""

    model_config = _CONFIG

    cell_id: CellIdField = Field(description="The Cell that reported it.")
    cores: int = Field(description="Logical cores.")
    memory_bytes: int = Field(description="Total memory.")
    memory_free_bytes: int = Field(description="Free memory when it reported.")
    disk_bytes: int = Field(description="Total disk where the Hive works.")
    disk_free_bytes: int = Field(description="Free disk when it reported.")
    cpu_load: float = Field(description="Its CPU load when it reported.")
    gpus: int = Field(description="GPUs it reported.")
    local_seats: int = Field(description="Model seats its own servers offer, in total.")
    max_sub_bees: int = Field(description="Sub-bees it can run at once.")


class GrantView(BaseModel):
    """One live grant: the share of Forage a Warden holds, as a lease."""

    model_config = _CONFIG

    id: GrantIdField = Field(description="The grant's id, stable across revisions.")
    holder: WardenIdField = Field(description="The Warden that holds it.")
    cell_id: CellIdField = Field(description="The holder's Cell.")
    task_id: TaskIdField | None = Field(description="The task it was issued for, if one.")
    revision: int = Field(description="Its revision: grown, shrunk or topped up since issue.")
    state: GrantState = Field(description="ISSUED, ACTIVE or EXHAUSTED (a revoked one is gone).")
    max_sub_bees: int = Field(description="Sub-bees it lets the holder run.")
    seats: int = Field(description="Shared model seats it reserves, in total.")
    token_budget: int = Field(description="Tokens it allows.")
    tokens_spent: int = Field(description="Tokens already spent against it.")
    spend_budget: float = Field(description="Spend it allows, in US dollars.")
    spent: float = Field(description="Spend already charged to it.")
    expires_at: datetime = Field(description="When it lapses unless a Heartbeat renews it.")


class HeadroomView(BaseModel):
    """What the shared pool still has free."""

    model_config = _CONFIG

    sub_bees: int = Field(description="Shared sub-bee slots free after reserve and live grants.")
    shared_seats: int = Field(description="Shared model seats free after reserve and grants.")


class ReserveView(BaseModel):
    """The Royal Reserve: what every headroom computation holds back first."""

    model_config = _CONFIG

    seats: int = Field(description="Seats held back from every grant.")
    memory_bytes: int = Field(description="Memory held back from every grant.")
    headroom_fraction: float = Field(description="The fraction of capacity kept as a margin.")


class ForageView(BaseModel):
    """The Queen's Forage ledger as it stands (observe)."""

    model_config = _CONFIG

    capacities: list[CapacityView] = Field(description="Every Cell's latest capacity report.")
    grants: list[GrantView] = Field(description="Every live grant, by holder then id.")
    headroom: HeadroomView = Field(description="What the shared pool still has free.")
    reserve: ReserveView = Field(description="The Royal Reserve.")


def forage_view(ledger: ForageLedger) -> ForageView:
    """Shape the Queen's Forage ledger as it stands.

    Args:
        ledger: The ledger; read without a lock (its reads are snapshot-safe).

    Returns:
        Every capacity report by Cell, every live grant by holder then id, the headroom and the
        Royal Reserve.
    """
    capacities = sorted(ledger.capacities().items())
    grants = sorted(ledger.live_grants(), key=lambda grant: (grant.holder, grant.id))
    return ForageView(
        capacities=[capacity_view(cell_id, capacity) for cell_id, capacity in capacities],
        grants=[grant_view(grant) for grant in grants],
        headroom=headroom_view(ledger.headroom()),
        reserve=reserve_view(ledger.reserve),
    )


def capacity_view(cell_id: str, capacity: ForageCapacity) -> CapacityView:
    """Shape one Cell's capacity report.

    Args:
        cell_id: The Cell that reported it.
        capacity: The report.

    Returns:
        Its view.
    """
    host = capacity.host
    return CapacityView(
        cell_id=cell_id,
        cores=host.cores,
        memory_bytes=host.memory_bytes,
        memory_free_bytes=host.memory_free_bytes,
        disk_bytes=host.disk_bytes,
        disk_free_bytes=host.disk_free_bytes,
        cpu_load=host.cpu_load,
        gpus=len(host.gpus),
        local_seats=sum(seat.seats_total for seat in capacity.local_seats),
        max_sub_bees=capacity.max_sub_bees,
    )


def grant_view(grant: ForageGrant) -> GrantView:
    """Shape one live grant, leaving its reason and bindings behind.

    Args:
        grant: The grant as the ledger holds it.

    Returns:
        Its view.
    """
    return GrantView(
        id=grant.id,
        holder=grant.holder,
        cell_id=grant.cell_id,
        task_id=grant.task_id,
        revision=grant.revision,
        state=grant.state,
        max_sub_bees=grant.max_sub_bees,
        seats=sum(seat.seats for seat in grant.seats),
        token_budget=grant.token_budget,
        tokens_spent=grant.tokens_spent,
        spend_budget=grant.spend_budget,
        spent=grant.spent,
        expires_at=grant.expires_at,
    )


def headroom_view(headroom: Headroom) -> HeadroomView:
    """Shape the ledger's headroom.

    Args:
        headroom: What the ledger computed.

    Returns:
        Its view.
    """
    return HeadroomView(sub_bees=headroom.sub_bees, shared_seats=headroom.shared_seats)


def reserve_view(reserve: RoyalReserve) -> ReserveView:
    """Shape the Royal Reserve.

    Args:
        reserve: The reserve the ledger subtracts first.

    Returns:
        Its view.
    """
    return ReserveView(
        seats=reserve.seats,
        memory_bytes=reserve.memory_bytes,
        headroom_fraction=reserve.headroom_fraction,
    )
