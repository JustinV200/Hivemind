"""Define LocalPoolReport and Headroom: the ledger's own small value types.

The Forage ledger (`hivemind.queen.forage.ledger.book.ForageLedger`) is the Queen's live book of
Forage (the Hive's capacity, modelled as data): every Cell's latest
`hivemind.forage.ForageCapacity`, every Warden's usage report against its ceilings, every live
`hivemind.forage.ForageGrant`, and the `hivemind.forage.RoyalReserve` it subtracts first.
`LocalPoolReport` is the ledger-side shape of a Warden's own local pool as the ledger sees it:
reported, never granted (codingrules section 8.10, "local pools appear in the ledger as reported,
not granted") -- it mirrors
`waggle.messages.forage.capacity.LocalPoolUsage` field for field, plus the ids that name whose
report this is. `Headroom` is the ledger's own answer to "how much shared capacity is still free":
shared totals minus the Royal Reserve minus the sum of every live shared grant (codingrules section
8.10). v1 (roadmap step 4.7) scopes `Headroom` to the sub-bee dimension: the dimension every grant
and every `ForageRequest` actually spends, and the one the roadmap's own exit criteria and the
synthetic fifty-Warden test both exercise ("the sum of live grants never exceeds capacity minus
reserve"); spend and tokens are still recorded per grant and per goal (`ForageLedger.grants_for`),
but the shared Hive has no single configured spend ceiling to compute a spend headroom against.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Built and read by `hivemind.queen.forage.ledger.book.ForageLedger`; persisted by
    `hivemind.queen.forage.ledger.store_protocol.LedgerStore` implementations. Calls into
    `hivemind.forage` (ForageCapacity) and waggle only.

Key invariants:
    - LocalPoolReport is frozen and forbids extras, like every boundary value in this repository.
    - Headroom.sub_bees is never negative: `book.py`'s own computation clamps it at zero.

See Also:
    - .claude/roadmap.md step 4.7 for the ledger's own field-by-field description.
    - .claude/codingrules.md section 8.10 for "headroom as shared totals minus reserve minus the
      sum of live shared grants" and "local pools appear... as reported, not granted".
    - waggle.messages.forage.capacity for LocalPoolUsage, the wire shape this mirrors.
    - hivemind.queen.forage.ledger.book for ForageLedger, this module's one consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from waggle.ids import CellId, WardenId

__all__ = ["Headroom", "LocalPoolReport"]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class LocalPoolReport(BaseModel):
    """A Warden's own usage against its ceilings, as the ledger sees it: reported, not granted.

    Mirrors `waggle.messages.forage.capacity.LocalPoolUsage` field for field, plus the ids naming
    whose report this is.
    """

    model_config = _MODEL_CONFIG

    warden_id: WardenId = Field(description="The Warden this report is from.")
    cell_id: CellId = Field(description="The Cell that Warden's local pool is on.")
    sub_bees_active: Annotated[int, Field(ge=0)] = Field(
        description="Sub-bees running on the local pool now."
    )
    model_vram_bytes: Annotated[int, Field(ge=0)] = Field(
        description="VRAM the Warden's loaded models occupy."
    )
    model_disk_bytes: Annotated[int, Field(ge=0)] = Field(
        description="Disk the Warden's model files occupy."
    )
    seats_exported: Annotated[int, Field(ge=0)] = Field(
        description="Seats on the Cell's own servers lent to the shared pool."
    )


@dataclass(frozen=True, slots=True)
class Headroom:
    """How much of the shared pool is still free, v1's one dimension (module docstring).

    Attributes:
        sub_bees: Shared sub-bee slots still free: reported shared capacity, minus the Royal
            Reserve's own seats, minus every live grant's `max_sub_bees`. Never negative.
    """

    sub_bees: int
