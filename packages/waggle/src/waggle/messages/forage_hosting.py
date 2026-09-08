"""Define the forage family's hosting messages: a capacity report, ceilings, a mode and a plan.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Forage is
the Hive's capacity as data, and the four messages here are about where one Cell's (a unit of
compute's) models come from. ``CapacityReport`` is the Cell's own snapshot, sent by its Warden
(its always-on supervisor) or its Pollen Packet (the thin gateway on an enrolled device); live
capacity travels only here, never on a heartbeat. The other three are Queen (the central
orchestrator) decisions with their reasons: ``CeilingsSet`` bounds what a Warden may use of its
own Cell without asking, ``HostingDecided`` records the mode (shared only, local first, local
only) and ``PlanWritten`` writes the plan the Fanner (the seat meter every model call passes
through) follows: per slot a primary source and a fallback chain, plus a default chain. Each
carries a revision so a replayed older decision never overrides a newer one, a rule the receiver
applies. The family's grant messages live in ``waggle.messages.forage`` and its value models in
``waggle.messages.forage_values`` and ``waggle.messages.forage_capacity``, split out by
responsibility so every file stays under the codingrules 5.1 size limit. Every bound is a named
constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; CapacityReport is built by a Warden or a Pollen Packet and read by
    the Queen, the other three are built by the Queen and read by a Warden; calls into
    waggle.messages.base, waggle.messages.reports, waggle.messages.forage_capacity and
    waggle.messages.forage_values only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A plan never names a slot twice, so the Fanner's lookup by slot is unambiguous.

See Also:
    - docs/waggle/spec.md section 8.4 for the normative fields, bounds and validators.
    - waggle.messages.forage for GrantIssued, GrantRevoked, ForageRequest and ForageReply.
    - waggle.messages.forage_capacity for the value models and enums these messages carry.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    MAX_SUB_BEES_ON_WIRE,
    CellIdField,
    TaskIdField,
    WaggleMessage,
    WardenIdField,
)
from waggle.messages.forage_capacity import (
    CapacityTrigger,
    CeilingsReport,
    HostingMode,
    LocalPoolUsage,
    ModelServerReport,
    SlotPlan,
    SourceChain,
)
from waggle.messages.reports import HostCapacityReport

MAX_MODEL_SERVERS = 8  # Model servers on one Cell; a Nuc runs one or two, never a fleet.
MAX_SLOT_PLANS = 16  # Named slots in a plan; hivemind's ModelSlot enum has fewer members.

__all__ = [
    "MAX_MODEL_SERVERS",
    "MAX_SLOT_PLANS",
    "CapacityReport",
    "CeilingsSet",
    "HostingDecided",
    "PlanWritten",
]


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]


class CapacityReport(WaggleMessage):
    """Report a Cell's capacity snapshot (forage.capacity_report, an event).

    Host compute, the model servers on it, its sub-bee cap and the Warden's usage against its
    ceilings, sent at provision or enrolment, on change, on reconnection and on a timer. Live
    capacity travels only here, never on heartbeats.
    """

    cell_id: CellIdField = Field(description="The Cell this capacity belongs to.")
    trigger: CapacityTrigger = Field(description="Why this report was sent.")
    host: HostCapacityReport = Field(description="Static and live host figures, a full snapshot.")
    model_servers: tuple[ModelServerReport, ...] = Field(
        max_length=MAX_MODEL_SERVERS,
        description="Every model server on the Cell; empty for a Cell that hosts no models.",
    )
    max_sub_bees: int = Field(
        ge=0,
        le=MAX_SUB_BEES_ON_WIRE,
        description="The Cell's own cap on concurrent sub-bees.",
    )
    usage: LocalPoolUsage = Field(
        description="What the Warden uses of its local pool; all zeros for a Cell with no "
        "Warden yet."
    )


class HostingDecided(WaggleMessage):
    """Record where a Cell's bees get their models (forage.hosting_decided, an event).

    Shared only, local first, or local only, with the reason; the detailed plan follows as
    PlanWritten.
    """

    cell_id: CellIdField = Field(description="The Cell the decision is about.")
    revision: int = Field(
        ge=0,
        description="0 for the first decision about this Cell, incremented on each change, so "
        "a replayed older decision never overrides a newer one (a receiver rule).",
    )
    mode: HostingMode = Field(
        description="The decision. LOCAL_ONLY is mandatory for a NIGHT_VEIL Cell: the Warden "
        "of such a Cell refuses anything else (a receiver rule)."
    )
    task_id: TaskIdField | None = Field(description="The task that prompted it, if any.")
    reason: _Reason = Field(
        description="Why: free VRAM, seat pressure, distance against Tempo, need to survive "
        "disconnection, cost."
    )


class CeilingsSet(WaggleMessage):
    """Set or change a Warden's local-pool ceilings (forage.ceilings_set, an event).

    For a device Warden or a Nuc (a colonized Real Cell that also runs its own model server);
    within them the Warden never asks.
    """

    cell_id: CellIdField = Field(description="The Cell whose local pool the ceilings bound.")
    holder: WardenIdField = Field(description="The Warden they apply to.")
    revision: int = Field(ge=0, description="0 when first set, incremented on each change.")
    ceilings: CeilingsReport = Field(description="The ceilings.")
    reason: _Reason = Field(
        description="Why these ceilings; changing one is a Queen decision on the trail."
    )


class PlanWritten(WaggleMessage):
    """Write a Cell's hosting plan (forage.plan_written, an event).

    For each named slot a primary source and a fallback chain, plus a default chain for slots
    not named, with the reason.
    """

    cell_id: CellIdField = Field(description="The Cell the plan is for.")
    revision: int = Field(ge=0, description="0 for the first plan, incremented on each rewrite.")
    slots: tuple[SlotPlan, ...] = Field(
        max_length=MAX_SLOT_PLANS,
        description="Per named slot, its chain; slot names unique.",
    )
    default: SourceChain = Field(description="The chain for any slot not named.")
    reason: _Reason = Field(description="Why this plan.")

    @field_validator("slots")
    @classmethod
    def _slot_names_unique(cls, value: tuple[SlotPlan, ...]) -> tuple[SlotPlan, ...]:
        """Reject a slot planned twice."""
        # The Fanner looks a chain up by slot name; two entries for one slot would make the
        # lookup depend on order, and the sender that wrote them has a bug.
        names = [plan.slot for plan in value]
        if len(set(names)) != len(names):
            raise ValueError("PlanWritten slot names must be unique.")
        return value
