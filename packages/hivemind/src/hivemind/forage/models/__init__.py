"""Re-export every Forage model: capacity, map sources, grants and local-pool/hosting values.

Frozen pydantic models, one per Forage concept named in codingrules section 6.1's table and
roadmap step 3.12's field list, split across four modules by responsibility (`capacity.py`,
`sources.py`, `grants.py`, `pools.py`) so each stays under the codingrules 5.1 size limit. This
package is their face: a caller imports any of them from here without knowing which module defines
it, the same convention `waggle.messages.forage` follows for the wire forms several of these
convert to and from.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Imported by `hivemind.forage`
    (the package face), `hivemind.forage.map`, `hivemind.forage.allocate` and `hivemind.manifest`
    (which embeds `RoleFootprint`, `ModelSourceSpec` and `RoyalReserve` directly as section
    models for `[forage.roles.<role>]`, `[forage.map.<source_id>]` and `[forage.reserve]`).

Key invariants:
    - This file holds re-exports and __all__ only; no model is defined here.

See Also:
    - .claude/roadmap.md step 3.12 for the field-by-field description these models implement.
    - .claude/codingrules.md section 6.1 for the Forage vocabulary table naming these classes.
    - hivemind.forage.models.capacity, .sources, .grants, .pools for the definitions.

Public API:
    - Capacity (capacity): ForageCapacity, GpuInfo, HostCapacity, RoleFootprint, Seat.
    - Map sources (sources): Abundance, Distance, ModelCost, ModelSource, ModelSourceSpec.
    - Grants (grants): AllowedBinding, ForageGrant, ForageRequest, ForageRequestKind,
      RoyalReserve, SeatReservation.
    - Local pool and hosting (pools): Ceilings, HostingPlan, LocalPool, SlotPlan, SourceChain.
"""

from hivemind.forage.models.capacity import (
    ForageCapacity,
    GpuInfo,
    HostCapacity,
    RoleFootprint,
    Seat,
)
from hivemind.forage.models.grants import (
    AllowedBinding,
    ForageGrant,
    ForageRequest,
    ForageRequestKind,
    RoyalReserve,
    SeatReservation,
)
from hivemind.forage.models.pools import Ceilings, HostingPlan, LocalPool, SlotPlan, SourceChain
from hivemind.forage.models.sources import (
    Abundance,
    Distance,
    ModelCost,
    ModelSource,
    ModelSourceSpec,
)

__all__ = [
    "Abundance",
    "AllowedBinding",
    "Ceilings",
    "Distance",
    "ForageCapacity",
    "ForageGrant",
    "ForageRequest",
    "ForageRequestKind",
    "GpuInfo",
    "HostCapacity",
    "HostingPlan",
    "LocalPool",
    "ModelCost",
    "ModelSource",
    "ModelSourceSpec",
    "RoleFootprint",
    "RoyalReserve",
    "Seat",
    "SeatReservation",
    "SlotPlan",
    "SourceChain",
]
