"""Define the eleven Pheromone event families and the JSON codec built on them.

Every state-changing action in the Hive writes a `PheromoneEvent` (`hivemind.pheromone.events.
base`) whose `kind` is one of the strings documented below. This module is the normative
vocabulary: the complete, stable set of `kind` strings the Observation Hive and a human auditor
read, and the only place a new one may be added (codingrules section 12, roadmap 2.1). Each
family is a `PheromoneEvent` subclass that fixes `FAMILY` (the prefix before the dot) and `KINDS`
(every kind string that family accepts); the base class's validators refuse anything outside a
subclass's own `KINDS` or `FAMILY`, so this module is also the one place `lint`/`mypy`/tests can
prove the vocabulary is exhaustive and non-overlapping (`test_event_families_covers_exactly_the_
eleven_families`).

Vocabulary (family -> kind -> when it is recorded):
    cell: provisioned (a Virtual Cell backend created it); attested (Night Veil attestation ran,
        pass or fail per check); ready (its Warden's first heartbeat arrived); leased (a Real Cell
        lease opened); released (a Real Cell lease closed and the device restored); touched_
        outside_scratch (a lease wrote or read outside its scratch directory); sting_cut (a human
        disconnected the Cell); overwintered (a Virtual Cell was paused dormant); destroyed (a
        Virtual Cell was torn down); purged (Night Veil teardown purge completed for the Cell).
    task: submitted (BroodChamber.submit minted it); assigned (PENDING -> ASSIGNED); unassigned
        (ASSIGNED -> PENDING, Warden lost); started (ASSIGNED -> RUNNING); progressed (a progress
        report, no transition); blocked (RUNNING -> BLOCKED, a question was asked); answered
        (BLOCKED -> RUNNING, a question was answered); question_withdrawn (BLOCKED -> RUNNING, the
        question was withdrawn); paused (RUNNING -> PAUSED, Clustering); resumed (PAUSED ->
        RUNNING); succeeded (-> SUCCEEDED, acceptance checks passed); failed (-> FAILED); cancelled
        (-> CANCELLED).
    alarm: raised (a bee could not resolve an issue itself); handled (its supervisor resolved it
        without escalating); escalated (passed to the next level up); resolved (the issue is
        closed, at whichever level handled it).
    forage: capacity_reported (a Cell reported ForageCapacity at provision or on change);
        requested (a ForageRequest was made); granted (a ForageGrant was issued); denied (a
        request was refused); revoked (a standing grant was pulled back); expired (a grant's
        expires_at passed unrenewed); hosting_decided (a HostingPlan was chosen for a Cell);
        plan_written (the chosen HostingPlan was recorded with its reason).
    memory: checkpoint (a Handoff was written); handoff (control resumed from a Handoff, the other
        half of a checkpoint); reset (a threshold reset ran); compacted (a summary replaced older
        source records); wax_proposed (Cell Wax was proposed); wax_written (the Queen wrote it);
        wax_rejected (the Queen refused it); wax_cleared (the Queen cleared it); wax_expired (its
        expiry passed unrenewed).
    queen: started (the Queen process came up); placed (a Placement decision was made for a task);
        woke (an awake episode ran); clustered (Clustering paused affected bees); resumed (bees
        resumed from Clustering); stopped (the Queen process is shutting down).
    warden: spawned (a Warden started supervising a Cell); offline (its connection to the Queen
        was lost); reconnected (its connection came back); migrated (it moved to another host, a
        Supersedure or promotion step); stopped (it is shutting down).
    tool: requested (a Worker asked for a tool the Comb Registry does not yet have); scaffolded
        (Royal Jelly generated a draft implementation); quarantined (a QuarantineReport was
        produced, pass or fail); promoted (CombRegistry.promote admitted it); rejected (promotion
        was refused); retired (a promoted tool was withdrawn from the registry).
    swarm: invited (a device invite was minted); enrolled (a device completed enrolment); promoted
        (a Real Cell was colonized or became a Nuc); demoted (a Nuc lost its model server, or a
        colonized Cell was demoted); revoked (a device's enrolment was revoked); command_sent (a
        signed Waggle command was sent to a device).
    capping: proposed (a Proposal entered CHECKING); checked (one tier check ran, pass or fail);
        capped (every required check passed, CAPPED); applied (the proposal's side effect ran);
        verified (postconditions held after applying); rejected (a check failed, before applying);
        rolled_back (postconditions failed after applying, and the effect was undone); summary
        (a per-tier rollup of approved/rejected/rolled_back counts, the only capping.* record kept
        through a Night Veil teardown, codingrules section 12).
    llm: call (one model call completed; carries the normalised Usage, slot and provider);
        rebound (a call was retried on the same binding after a transient failure); fallback (a
        call moved to the plan's next binding); spill (the Fanner spilled from a local binding to
        shared Forage, one of the three cases in codingrules 8.10).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Every layer above Layer 1 constructs one
    of these subclasses each time it mutates state; hivemind.pheromone.trail (phase 2.2) is the
    first consumer of parse_event/parse_event_json, its JSON segment codec. Calls into
    hivemind.pheromone.events.base and hivemind.pheromone.errors only.

Key invariants:
    - Every class's FAMILY is unique across EVENT_FAMILIES (asserted when this module is
      imported, by _build_event_families); two families never share a prefix.
    - Every class's KINDS contains only strings whose family segment equals that class's own
      FAMILY (codingrules 5.1: PheromoneEvent._validate_kind enforces this per instance; the
      vocabulary list above is the source of truth these frozensets must match).
    - parse_event and parse_event_json are the only JSON entry points: both dispatch on `kind`
      alone, so a caller never has to know which family a stored event belongs to before decoding
      it (codingrules section 9: "Pydantic models are the only thing that reads JSON").

See Also:
    - .claude/codingrules.md section 12 for the audit-log rules this vocabulary follows.
    - .claude/codingrules.md section 8.10 for the Forage terms forage.* and llm.spill reference.
    - .claude/codingrules.md section 8.12 for the Capping state machine capping.* mirrors.
    - hivemind.pheromone.events.base for PheromoneEvent, LlmUsage and the shared validators.
    - hivemind.pheromone.errors for UnknownEventFamilyError, raised by event_class_for.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import ClassVar

from pydantic import Field, model_validator

from hivemind.pheromone.errors import UnknownEventFamilyError
from hivemind.pheromone.events.base import LlmUsage, PheromoneEvent
from waggle.messages.base import MAX_SLOT_CHARS, SLOT_PATTERN

MAX_PROVIDER_CHARS = 64  # A provider name from the manifest ("anthropic", "local"), never a URL.

__all__ = [
    "EVENT_FAMILIES",
    "MAX_PROVIDER_CHARS",
    "AlarmEvent",
    "CappingEvent",
    "CellEvent",
    "ForageEvent",
    "LlmEvent",
    "MemoryEvent",
    "QueenEvent",
    "SwarmEvent",
    "TaskEvent",
    "ToolEvent",
    "WardenEvent",
    "event_class_for",
    "parse_event",
    "parse_event_json",
]


class CellEvent(PheromoneEvent):
    """A Cell (Real or Virtual) lifecycle event; see the module docstring's `cell` entry."""

    FAMILY: ClassVar[str] = "cell"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "cell.provisioned",
            "cell.attested",
            "cell.ready",
            "cell.leased",
            "cell.released",
            "cell.touched_outside_scratch",
            "cell.sting_cut",
            "cell.overwintered",
            "cell.destroyed",
            "cell.purged",
        }
    )


class TaskEvent(PheromoneEvent):
    """A Brood Chamber task-state-machine transition; see the module docstring's `task` entry."""

    FAMILY: ClassVar[str] = "task"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "task.submitted",
            "task.assigned",
            "task.unassigned",
            "task.started",
            "task.progressed",
            "task.blocked",
            "task.answered",
            "task.question_withdrawn",
            "task.paused",
            "task.resumed",
            "task.succeeded",
            "task.failed",
            "task.cancelled",
        }
    )


class AlarmEvent(PheromoneEvent):
    """An Alarm's escalation-chain step; see the module docstring's `alarm` entry."""

    FAMILY: ClassVar[str] = "alarm"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {"alarm.raised", "alarm.handled", "alarm.escalated", "alarm.resolved"}
    )


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
        }
    )


class QueenEvent(PheromoneEvent):
    """A Queen lifecycle or mode event; see the module docstring's `queen` entry."""

    FAMILY: ClassVar[str] = "queen"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "queen.started",
            "queen.placed",
            "queen.woke",
            "queen.clustered",
            "queen.resumed",
            "queen.stopped",
        }
    )


class WardenEvent(PheromoneEvent):
    """A Warden lifecycle event; see the module docstring's `warden` entry."""

    FAMILY: ClassVar[str] = "warden"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "warden.spawned",
            "warden.offline",
            "warden.reconnected",
            "warden.migrated",
            "warden.stopped",
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


class CappingEvent(PheromoneEvent):
    """A Capping gate proposal-state-machine step; see the module docstring's `capping` entry."""

    FAMILY: ClassVar[str] = "capping"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "capping.proposed",
            "capping.checked",
            "capping.capped",
            "capping.applied",
            "capping.verified",
            "capping.rejected",
            "capping.rolled_back",
            "capping.summary",
        }
    )


class LlmEvent(PheromoneEvent):
    """One LLM call or routing decision; see the module docstring's `llm` entry.

    Carries three extra fields no other family has: `slot`, `provider` and `usage`. All three are
    required together exactly when `kind == "llm.call"` (`_call_requires_usage_fields` below); the
    other three kinds (`rebound`, `fallback`, `spill`) may set them or leave them `None`.
    """

    FAMILY: ClassVar[str] = "llm"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {"llm.call", "llm.rebound", "llm.fallback", "llm.spill"}
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
        # llm.call is the one kind that actually happened on a model; the other three kinds
        # describe routing decisions around a call and may not yet know all three values.
        if self.kind == "llm.call" and (
            self.slot is None or self.provider is None or self.usage is None
        ):
            raise ValueError("llm.call requires slot, provider and usage to all be set.")
        return self


# Every family class, in the order the vocabulary is documented above; the tuple, not the mapping
# built from it, is the single place a twelfth family would be added.
_FAMILY_CLASSES: tuple[type[PheromoneEvent], ...] = (
    CellEvent,
    TaskEvent,
    AlarmEvent,
    ForageEvent,
    MemoryEvent,
    QueenEvent,
    WardenEvent,
    ToolEvent,
    SwarmEvent,
    CappingEvent,
    LlmEvent,
)


def _build_event_families(
    classes: tuple[type[PheromoneEvent], ...],
) -> Mapping[str, type[PheromoneEvent]]:
    """Map each class's FAMILY to itself, asserting no two classes share a FAMILY.

    Args:
        classes: Every family subclass to register, in declaration order.

    Returns:
        An immutable family-prefix to class mapping with one entry per element of `classes`.
    """
    families: dict[str, type[PheromoneEvent]] = {}
    for event_cls in classes:
        # A collision here is a programming error in this module, not bad input, so it fails at
        # import time (module-level, pure computation -- codingrules 5.5 allows this) rather than
        # waiting to be discovered by whichever caller happens to hit the clash first.
        if event_cls.FAMILY in families:
            existing = families[event_cls.FAMILY]
            raise AssertionError(
                f"family {event_cls.FAMILY!r} is registered by both {existing.__name__} and "
                f"{event_cls.__name__}."
            )
        families[event_cls.FAMILY] = event_cls
    return families


EVENT_FAMILIES: Mapping[str, type[PheromoneEvent]] = _build_event_families(_FAMILY_CLASSES)


def event_class_for(kind: str) -> type[PheromoneEvent]:
    """Look up the PheromoneEvent subclass registered for `kind`'s family segment.

    Args:
        kind: A candidate kind string, e.g. `"task.submitted"`.

    Returns:
        The subclass whose `FAMILY` equals the segment of `kind` before its first `.`.

    Raises:
        UnknownEventFamilyError: `kind` has no `.`, or its family segment is not in
            `EVENT_FAMILIES`.
    """
    family, sep, _ = kind.partition(".")
    if not sep:
        raise UnknownEventFamilyError(f"kind {kind!r} is not '<family>.<name>' shaped.")
    event_cls = EVENT_FAMILIES.get(family)
    if event_cls is None:
        raise UnknownEventFamilyError(f"kind {kind!r} names an unknown event family {family!r}.")
    return event_cls


def parse_event(data: Mapping[str, object]) -> PheromoneEvent:
    """Decode `data` into the right PheromoneEvent subclass, chosen by its `kind` field.

    The one JSON-codec entry point for a Pheromone event: a caller never needs to know which
    family a stored or wire-carried event belongs to before decoding it.

    Args:
        data: A JSON-object-shaped mapping, as produced by `PheromoneEvent.model_dump(mode=
            "json")` or read back from storage.

    Returns:
        A validated instance of the subclass `data["kind"]` selects.

    Raises:
        UnknownEventFamilyError: `data` has no string `kind` field, or `event_class_for` rejects
            it.
        pydantic.ValidationError: `data` fails the selected subclass's own field validation.
    """
    kind = data.get("kind")
    if not isinstance(kind, str):
        raise UnknownEventFamilyError(f"event data has no string 'kind' field: {kind!r}.")
    event_cls = event_class_for(kind)
    return event_cls.model_validate(data)


def parse_event_json(raw: str | bytes) -> PheromoneEvent:
    """Parse `raw` as JSON and decode it into the right PheromoneEvent subclass.

    Args:
        raw: A JSON document, as text or UTF-8 bytes, expected to be a single JSON object.

    Returns:
        A validated instance of the subclass the document's `kind` field selects.

    Raises:
        UnknownEventFamilyError: `raw` does not decode to a JSON object, or `parse_event` rejects
            the decoded mapping.
        json.JSONDecodeError: `raw` is not valid JSON at all.
        pydantic.ValidationError: The decoded object fails the selected subclass's own validation.
    """
    document = json.loads(raw)
    if not isinstance(document, Mapping):
        raise UnknownEventFamilyError(f"event JSON is not an object: {type(document).__name__}.")
    return parse_event(document)
