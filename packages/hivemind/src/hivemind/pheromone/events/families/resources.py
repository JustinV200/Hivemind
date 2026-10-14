"""Define the event families of what the Hive draws on: Forage, memory, tools, blobs, models.

Six of the Pheromone Trail's fifteen event families record the resources the work draws on:
capacity and its grants (`forage`), the memory tiers and Cell Wax (`memory`), the Royal Jelly
tool lifecycle (`tool`), the Basket's content-addressed blobs (`basket`), the Swarm's enrolled
devices (`swarm`) and every model call or routing decision (`llm`). Each is a `PheromoneEvent`
subclass (`hivemind.pheromone.events.base`) fixing `FAMILY` and `KINDS`; `LlmEvent` alone carries
three extra fields (`slot`, `provider`, `usage`), required together exactly on `llm.call`.

Vocabulary (family -> kind -> when it is recorded):
    forage: capacity_reported (a Cell reported ForageCapacity at provision or on change);
        requested (a ForageRequest was made); granted (a ForageGrant was issued); denied (a
        request was refused); revoked (a standing grant was pulled back); expired (a grant's
        expires_at passed unrenewed); hosting_decided (a HostingPlan was chosen for a Cell);
        plan_written (the chosen HostingPlan was recorded with its reason); ceilings_set (the
        Queen set or changed a Warden's Ceilings, roadmap step 4.8); source_added (a Warden loaded
        a model on its Cell's server, or the Queen learned of it: carries the Cell, server and
        source ids and the seats; not in the Night Veil skeleton, so it dies with that Cell);
        source_removed (a source left a server, with the cause; the same two recorders; also
        written alone, with `loaded = false`, for a server that never became healthy; not in the
        Night Veil skeleton); source_evicted (the Queen ordered a Warden to give back seats she
        exported; not in the Night Veil skeleton). Roadmap step 8.3, ADR-0044.
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
        was refused); retired (a promoted tool was withdrawn from the registry). Phase 9 adds the
        stages around a promotion, each recorded where it happens (ADR-0046, ADR-0047):
        quarantine_requested (a `tool.quarantine` was sent or accepted, naming the package by
        hash); sandbox_provisioned (the Queen made a sandbox Cell for one Comb run);
        sandbox_released (that sandbox was torn down after its report); announced (a
        `tool.promoted` was sent to one Warden whose Cell can run it); loaded (a Worker's bundle
        gained a Comb tool after its Warden re-attenuated it); load_refused (a Warden or Worker
        declined to load a promoted tool: hash mismatch, OS, capability or a blob with no entry);
        rolled_back (an earlier version was made current again, the row's own report its evidence).
    basket: deposited (a blob was kept in the Basket under its hash, after a Capping-checked
        retain proposal applied or an operator's deposit; carries the hash, size, kind, clearance
        and origin); deposit_deduplicated (a deposit met a blob already stored by that hash and
        returned it); deposit_refused (the scanner flagged a text-like blob, or it broke a size or
        label rule, and nothing was stored); offered (a `basket.offer` was sent for one blob and
        one destination Cell); received (a blob arrived whole, its hash and signature verified,
        and became resident); refused (an offer or a transfer was refused, either way round,
        with its `BasketRefusal` code); evicted (residency freed a resident copy to make room, or
        absconding removed it); retired (a blob was marked RETIRED: never shipped or loaded
        again); shipped (the last chunk of a shipment was acknowledged complete).
    swarm: invited (a device invite was minted); enrolled (a device completed enrolment); promoted
        (a Real Cell was colonized or became a Nuc; from phase 11 it covers every rung, with the
        payload's `from_level` and `to_level`); demoted (a Nuc lost its model server, or a
        colonized Cell was demoted; the same payload); revoked (a device's enrolment was revoked);
        command_sent (a signed Waggle command was sent to a device: the lease, request and task
        ids, the program's basename and an argument count, never an argument or any content).
        Phase 11 (the Swarm, ADR-0051 to ADR-0056) adds eight: enrol_refused (an enrolment was
        denied: ids and a reason code, never the invite token); online (a device's packet
        connected and its node is ONLINE); unreachable (a device's link was lost: the Queen
        marked its node UNREACHABLE); access_changed (the operator changed a device's access
        level); shield_changed (the operator changed its Comb Shield tier); shield_verified (a
        PROPOLIS device's tunnel checks passed before a lease was kept, with each check's
        result); shield_refused (they failed, so the lease was released at once and nothing was
        placed on the device, with each check's result); node_held (the Queen held a lost
        device's tasks for the grace period rather than failing or re-placing them).
    llm: call (one model call completed; carries the normalised Usage, slot and provider);
        rebound (a sub-bee was rebound to another binding inside its grant, by its Warden's own
        REBIND or a Queen-sent Intervene(REBIND), once the slot_binding point allowed it, roadmap
        step 10.3; carries the task id, both binding keys and who ordered it); fallback (a
        call moved to the plan's next binding); spill (the Fanner spilled from a local binding to
        shared Forage, one of the three cases in codingrules 8.10); throttled (a hosted source
        was rate-limited and its headroom masked to zero on the Forage map, roadmap step 4.7a).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.events.
    families`. Constructed by the Queen's Forage ledger, the memory tiers, Royal Jelly, the Basket,
    the Swarm and the Fanner; decoded by `hivemind.pheromone.events.families.codec`. Calls into
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
    "BasketEvent",
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
            # Roadmap step 8.3 (ADR-0044), none in the Night Veil skeleton (codingrules section 12
            # names the survivors and these are not among them): a Cell's model source became
            # servable (recorded by its Warden and again by the Queen who learned of it), went
            # away, or was evicted by the Queen to reclaim seats she exported. A Night Veil Cell's
            # rows go to its own segment and die with the Cell.
            "forage.source_added",
            "forage.source_removed",
            "forage.source_evicted",
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
            # Phase 9 (ADR-0046, ADR-0047): the stages around a promotion.
            "tool.quarantine_requested",
            "tool.sandbox_provisioned",
            "tool.sandbox_released",
            "tool.announced",
            "tool.loaded",
            "tool.load_refused",
            "tool.rolled_back",
        }
    )


class BasketEvent(PheromoneEvent):
    """A Basket deposit, transfer, eviction or retirement; see the module docstring's `basket`."""

    FAMILY: ClassVar[str] = "basket"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "basket.deposited",
            "basket.deposit_deduplicated",
            "basket.deposit_refused",
            "basket.offered",
            "basket.received",
            "basket.refused",
            "basket.evicted",
            "basket.retired",
            "basket.shipped",
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
            # Phase 11 (ADR-0051 to ADR-0056). A Swarm Cell is never Night Veil, so none of
            # these ever meets the Night Veil teardown boundary (codingrules section 12).
            "swarm.enrol_refused",  # An enrolment was denied; ids and a reason code only.
            "swarm.online",  # A device's packet connected and its Cell is schedulable.
            "swarm.unreachable",  # A device's link was lost; its tasks are held, not failed.
            "swarm.access_changed",  # The operator changed a device's access level.
            "swarm.shield_changed",  # The operator changed a device's Comb Shield tier.
            "swarm.watch_changed",  # The operator changed the directories its Warden watches.
            "swarm.shield_verified",  # A PROPOLIS device's tunnel checks passed before a lease.
            "swarm.shield_refused",  # They failed: the lease was released and the task not placed.
            "swarm.node_held",  # The Queen held a lost device's tasks for the grace period.
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
