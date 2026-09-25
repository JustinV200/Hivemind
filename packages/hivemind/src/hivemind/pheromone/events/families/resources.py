"""Define the event families of what the Hive draws on: Forage, memory, tools, devices, models.

Five of the Pheromone Trail's thirteen event families record the resources the work draws on:
capacity and its grants (`forage`), the memory tiers and Cell Wax (`memory`), the Royal Jelly
tool lifecycle (`tool`), the Swarm's enrolled devices (`swarm`) and every model call or routing
decision (`llm`). Each is a `PheromoneEvent` subclass (`hivemind.pheromone.events.base`) fixing
`FAMILY` and `KINDS`; `LlmEvent` alone carries three extra fields (`slot`, `provider`, `usage`),
required together exactly on `llm.call`.

Vocabulary (family -> kind -> when it is recorded):
    forage: capacity_reported (a Cell reported ForageCapacity at provision or on change);
        requested (a ForageRequest was made); granted (a ForageGrant was issued); denied (a
        request was refused); revoked (a standing grant was pulled back); expired (a grant's
        expires_at passed unrenewed); hosting_decided (a HostingPlan was chosen for a Cell);
        plan_written (the chosen HostingPlan was recorded with its reason); ceilings_set (the
        Queen set or changed a Warden's Ceilings, roadmap step 4.8).
    memory: checkpoint (a Handoff was written); handoff (control resumed from a Handoff, the other
        half of a checkpoint); reset (a threshold reset ran); compacted (a summary replaced older
        source records); wax_proposed (Cell Wax was proposed); wax_written (the Queen wrote it);
        wax_rejected (the Queen refused it); wax_cleared (the Queen cleared it); wax_expired (its
        expiry passed unrenewed); episode (an EpisodeRecord was written for an awake episode or an
        autopilot decision, roadmap step 3.14); note (a bee wrote a short note directly into hot
        state, bounded per author); pinned (a Pin was added to hot state, from the manifest or at
        runtime); bee_bread_deposited (a BeeBreadEntry was written to the warm tier: an index over
        Brood Chamber/the trail, a Handoff reference, a deposited transcript, or an oversized tool
        result, roadmap step 4.2); overflow (one ContextTooLong overflow was recovered by
        shrinking the budget, roadmap step 4.4); tainted (a checkpoint, Handoff, episode record,
        Nectar or Honey item was labelled tainted by an isolation, a quarantine or the Queen on a
        Guard report, and is refused by assembly and retrieval from then on; carries the item, the
        reason and the event that set it, roadmap step 10.6d, ADR-0043); taint_cleared (a judge
        verdict on the taint rubric cleared a tainted item, 10.6d).
    tool: requested (a Worker asked for a tool the Comb Registry does not yet have); scaffolded
        (Royal Jelly generated a draft implementation); quarantined (a QuarantineReport was
        produced, pass or fail); promoted (CombRegistry.promote admitted it); rejected (promotion
        was refused); retired (a promoted tool was withdrawn from the registry).
    swarm: invited (a device invite was minted); enrolled (a device completed enrolment); promoted
        (a Real Cell was colonized or became a Nuc); demoted (a Nuc lost its model server, or a
        colonized Cell was demoted); revoked (a device's enrolment was revoked); command_sent (a
        signed Waggle command was sent to a device).
    llm: call (one model call completed; carries the normalised Usage, slot and provider);
        rebound (a sub-bee was rebound to another binding inside its grant, by its Warden's own
        REBIND or a Queen-sent Intervene(REBIND), once the slot_binding point allowed it, roadmap
        step 10.3; carries the task id, both binding keys and who ordered it); fallback (a
        call moved to the plan's next binding); spill (the Fanner spilled from a local binding to
        shared Forage, one of the three cases in codingrules 8.10); throttled (a hosted source
        was rate-limited and its headroom masked to zero on the Forage map, roadmap step 4.7a).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.events.
    families`. Constructed by the Queen's Forage ledger, the memory tiers, Royal Jelly, the Swarm
    and the Fanner; decoded by `hivemind.pheromone.events.families.codec`. Calls into
    `hivemind.pheromone.events.base` and `waggle.messages.base` (the slot bounds) only.

Key invariants:
    - Every class's KINDS contains only strings whose family segment equals its own FAMILY.
    - An `llm.call` event always carries `slot`, `provider` and `usage` together.

See Also:
    - .claude/codingrules.md section 8.10 for the Forage terms forage.* and llm.spill reference.
    - hivemind.pheromone.events.families.codec for EVENT_FAMILIES and the JSON codec.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, model_validator

from hivemind.pheromone.events.base import LlmUsage, PheromoneEvent
from waggle.messages.base import MAX_SLOT_CHARS, SLOT_PATTERN

MAX_PROVIDER_CHARS = 64  # A provider name from the manifest ("anthropic", "local"), never a URL.

__all__ = [
    "MAX_PROVIDER_CHARS",
    "ForageEvent",
    "LlmEvent",
    "MemoryEvent",
    "SwarmEvent",
    "ToolEvent",
]


class ForageEvent(PheromoneEvent):
    """A Forage capacity, grant or hosting decision; see the module docstring's `forage` entry."""

    FAMILY: ClassVar[str] = "forage"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "forage.capacity_reported",
            "forage.requested",
            "forage.granted",
            "forage.denied",
            "forage.revoked",
            "forage.expired",
            "forage.hosting_decided",
            "forage.plan_written",
            "forage.ceilings_set",
        }
    )


class MemoryEvent(PheromoneEvent):
    """A memory-tier or Cell Wax transition; see the module docstring's `memory` entry."""

    FAMILY: ClassVar[str] = "memory"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "memory.checkpoint",
            "memory.handoff",
            "memory.reset",
            "memory.compacted",
            "memory.wax_proposed",
            "memory.wax_written",
            "memory.wax_rejected",
            "memory.wax_cleared",
            "memory.wax_expired",
            # roadmap step 3.14 (memory v0): an EpisodeRecord, a bee-written Note, and a Pin.
            "memory.episode",
            "memory.note",
            "memory.pinned",
            # roadmap step 4.2 (Bee Bread, the warm tier): every write into it.
            "memory.bee_bread_deposited",
            # roadmap step 4.4: one ContextTooLong overflow recovered by shrinking the budget.
            "memory.overflow",
            "memory.tainted",  # Roadmap step 10.6d: one label, three setters (ADR-0043).
            "memory.taint_cleared",  # Roadmap step 10.6d: only a judge verdict clears it.
        }
    )


class ToolEvent(PheromoneEvent):
    """A Royal Jelly / Comb Registry tool lifecycle event; see the module docstring's `tool`."""

    FAMILY: ClassVar[str] = "tool"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "tool.requested",
            "tool.scaffolded",
            "tool.quarantined",
            "tool.promoted",
            "tool.rejected",
            "tool.retired",
        }
    )


class SwarmEvent(PheromoneEvent):
    """A Swarm (Real Cell / device) enrolment or role event; see the module docstring's `swarm`."""

    FAMILY: ClassVar[str] = "swarm"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "swarm.invited",
            "swarm.enrolled",
            "swarm.promoted",
            "swarm.demoted",
            "swarm.revoked",
            "swarm.command_sent",
        }
    )


class LlmEvent(PheromoneEvent):
    """One LLM call or routing decision; see the module docstring's `llm` entry.

    Carries three extra fields no other family has: `slot`, `provider` and `usage`. All three are
    required together exactly when `kind == "llm.call"` (`_call_requires_usage_fields` below); the
    other four kinds (`rebound`, `fallback`, `spill`, `throttled` -- roadmap step 4.7a's own
    headroom-masked-at-zero event) may set them or leave them `None`.
    """

    FAMILY: ClassVar[str] = "llm"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {"llm.call", "llm.rebound", "llm.fallback", "llm.spill", "llm.throttled"}
    )

    slot: str | None = Field(
        default=None,
        max_length=MAX_SLOT_CHARS,
        pattern=SLOT_PATTERN,
        description="The ModelSlot name (forage.slots) the call was routed to; required on call.",
    )
    provider: str | None = Field(
        default=None,
        max_length=MAX_PROVIDER_CHARS,
        description="The manifest provider name (never a model id or URL); required on call.",
    )
    usage: LlmUsage | None = Field(
        default=None, description="The normalised token-and-cost usage (codingrules 8.6)."
    )

    @model_validator(mode="after")
    def _call_requires_usage_fields(self) -> LlmEvent:
        """Require slot, provider and usage together exactly when kind is llm.call."""
        # llm.call is the one kind that actually happened on a model; the other kinds describe
        # routing decisions around a call and may not yet know all three values.
        if self.kind == "llm.call" and (
            self.slot is None or self.provider is None or self.usage is None
        ):
            raise ValueError("llm.call requires slot, provider and usage to all be set.")
        return self
