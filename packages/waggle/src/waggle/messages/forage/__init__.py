"""Re-export the forage family: capacity as data, divided by grant and hosted per Cell.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Forage is
the Hive's capacity as data (seats, context, speed, spend; never one number), and the shared half
of it is divided by grant. ``grants`` holds a grant's life (issued, revoked, a request beyond it
and the reply); ``values`` the value models and enums those carry; ``capacity`` the capacity and
plan value models; ``hosting`` a Cell's capacity report and the hosting decision, ceilings and plan
the Queen (the central orchestrator) writes for it. This package is the family's face: a caller
imports any of its messages, enums or value models from here without knowing which module defines
them. The bounds each module names stay in that module, because the spec makes the number
normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a forage payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``forage.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.4 for the family's normative fields and rules.
    - waggle.messages.forage.grants, waggle.messages.forage.values,
      waggle.messages.forage.capacity and waggle.messages.forage.hosting for the definitions.

Public API:
    - Grants (grants): ForageReply, ForageRequest, GrantIssued, GrantRevoked.
    - Values (values): AllowedBinding, Effort, ForageDelta, ForageOutcome, ForageRequestKind,
      RevocationCause, SeatReservation, SourceRef.
    - Capacity (capacity): CapacityTrigger, CeilingsReport, HostingMode, LocalPoolUsage,
      LocalSourceReport, ModelServerReport, SlotPlan, SourceChain.
    - Hosting (hosting): CapacityReport, CeilingsSet, HostingDecided, PlanWritten.
"""

from waggle.messages.forage.capacity import (
    CapacityTrigger,
    CeilingsReport,
    HostingMode,
    LocalPoolUsage,
    LocalSourceReport,
    ModelServerReport,
    SlotPlan,
    SourceChain,
)
from waggle.messages.forage.grants import ForageReply, ForageRequest, GrantIssued, GrantRevoked
from waggle.messages.forage.hosting import CapacityReport, CeilingsSet, HostingDecided, PlanWritten
from waggle.messages.forage.values import (
    AllowedBinding,
    Effort,
    ForageDelta,
    ForageOutcome,
    ForageRequestKind,
    RevocationCause,
    SeatReservation,
    SourceRef,
)

__all__ = [
    "AllowedBinding",
    "CapacityReport",
    "CapacityTrigger",
    "CeilingsReport",
    "CeilingsSet",
    "Effort",
    "ForageDelta",
    "ForageOutcome",
    "ForageReply",
    "ForageRequest",
    "ForageRequestKind",
    "GrantIssued",
    "GrantRevoked",
    "HostingDecided",
    "HostingMode",
    "LocalPoolUsage",
    "LocalSourceReport",
    "ModelServerReport",
    "PlanWritten",
    "RevocationCause",
    "SeatReservation",
    "SlotPlan",
    "SourceChain",
    "SourceRef",
]
