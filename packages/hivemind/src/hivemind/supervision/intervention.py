"""Define Intervention: the seven levers a Supervisor pulls on a child, and their wire conversion.

`Supervisor.intervene` (codingrules section 8.8) is how the Queen delegates and rebinds rather than
executing: compact a bee's context in place, checkpoint it, hand it off to a fresh bee, rebind it
to another model slot, take it over, cancel it, or (roadmap step 10.6c, ADR-0043) quarantine it.
Each lever is its own frozen pydantic model carrying a `reason`, discriminated by a `kind` literal
exactly like `waggle.messages.forage`'s own discriminated unions, so a caller can `match` (or, per
this repository's own hygiene script, an `isinstance` chain) on the concrete type rather than on a
bare string. `to_wire`/`from_wire` convert to and from `waggle.messages.supervision.Intervene`,
the wire message carrying the same levers as one `InterventionAction` enum plus an optional slot,
and `to_intervene` builds the whole wire message, which a `Quarantine` needs: it names its bee,
its task and the episode from which the bee's memory is suspect. Decoding an action the union has
no lever for (`RELEASE_LEASE`, which a Warden carries out itself, or anything newer) raises
`UnknownInterventionError`: ADR-0043's "decoding an intervention the Hive does not know is an
error, never a silent cancel".

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by whichever `Supervisor`
    implementation calls `intervene`, and read on the receiving bee's side after `from_wire`.
    Calls into `hivemind.forage` (for `ModelSlot`, the only model-shaped thing this module names),
    `hivemind.supervision.errors` and waggle only.

Key invariants:
    - Every Intervention variant is frozen and forbids extras, like every boundary value here.
    - to_wire, to_intervene and from_wire are inverses on every variant: `_ACTIONS` names one wire
      action per variant (a test walks the union), and from_wire rebuilds each one, Rebind's slot
      and Quarantine's bee, task and suspect episode included.
    - from_wire never falls back to a default lever: an action with no variant is refused.
    - Rebind.slot is a hivemind.forage.ModelSlot, never a bare provider or model name
      (codingrules section 8.6): "the Queen steps in" and "the Queen gives the bee a better
      model" are the same move, made by naming a slot.

See Also:
    - .claude/codingrules.md section 8.8 for the Queen's and a Warden's intervention levers.
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md, "Quarantine is
      one intervention".
    - waggle.messages.supervision for Intervene and InterventionAction, the wire forms this module
      converts to and from.
    - hivemind.forage.slots for ModelSlot, the type Rebind.slot carries.
    - hivemind.wardens.quarantine for the one code path that carries a Quarantine out.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.forage import ModelSlot
from hivemind.supervision.errors import SupervisionError, UnknownInterventionError
from waggle.ids import AlarmId, TaskId
from waggle.messages.base import EventIdField, TaskIdField, WorkerIdField
from waggle.messages.supervision import Intervene, InterventionAction

__all__ = [
    "Cancel",
    "Checkpoint",
    "Compact",
    "Handoff",
    "Intervention",
    "Quarantine",
    "Rebind",
    "Takeover",
    "from_wire",
    "to_intervene",
    "to_wire",
]


class Compact(BaseModel):
    """Compact the bee's context in place; it keeps working on the same slot."""

    # Written out literally, not through a shared name: the pydantic mypy plugin only reads
    # frozen=True off a literal ConfigDict on the class body (see waggle.messages.base's own
    # note on WaggleMessage.model_config for the same caveat).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["compact"] = "compact"
    reason: str = Field(description="Why the supervisor intervenes.")


class Checkpoint(BaseModel):
    """Write a Handoff and carry on; the bee resets its own context and resumes from it."""

    # Written out literally, not through a shared name (see Compact's own note).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["checkpoint"] = "checkpoint"
    reason: str = Field(description="Why the supervisor intervenes.")


class Handoff(BaseModel):
    """Write a Handoff and stop; a fresh bee resumes the task from it."""

    # Written out literally, not through a shared name (see Compact's own note).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["handoff"] = "handoff"
    reason: str = Field(description="Why the supervisor intervenes.")


class Rebind(BaseModel):
    """Move the bee to another model slot; "a better model" and "the Queen steps in" are this."""

    # Written out literally, not through a shared name (see Compact's own note).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["rebind"] = "rebind"
    reason: str = Field(description="Why the supervisor intervenes.")
    slot: ModelSlot = Field(description="The model slot to rebind the bee to.")


class Takeover(BaseModel):
    """Spawn a bee on the same Cell with the supervisor's own slot and the stuck bee's Handoff."""

    # Written out literally, not through a shared name (see Compact's own note).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["takeover"] = "takeover"
    reason: str = Field(description="Why the supervisor intervenes.")


class Cancel(BaseModel):
    """Stop the bee for good; nothing resumes it."""

    # Written out literally, not through a shared name (see Compact's own note).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["cancel"] = "cancel"
    reason: str = Field(description="Why the supervisor intervenes.")


class Quarantine(BaseModel):
    """Quarantine one bee: checkpoint it, stop and kill it, taint its memory, hold its task.

    Roadmap step 10.6c (ADR-0043). Carried out by the bee's own Warden, through the one code path
    in `hivemind.wardens.quarantine`, never relayed to the bee itself. The only way out is a
    respawn from the Handoff that path wrote, once a judge has cleared it.
    """

    # Written out literally, not through a shared name (see Compact's own note).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["quarantine"] = "quarantine"
    reason: str = Field(description="Why the supervisor intervenes; ids only, never content.")
    bee: WorkerIdField | None = Field(
        default=None, description="The sub-bee to quarantine; None when task_id names it."
    )
    task_id: TaskIdField | None = Field(
        default=None, description="The quarantined bee's task; None when bee names it."
    )
    suspect_episode_id: EventIdField = Field(
        description="The episode from which the bee's memory is suspect: everything it wrote from "
        "then on is tainted."
    )

    @model_validator(mode="after")
    def _names_its_bee(self) -> Quarantine:
        """Refuse a quarantine naming neither a bee nor a task: it would name no one."""
        if self.bee is None and self.task_id is None:
            raise ValueError("A Quarantine names its bee, its task, or both; it named neither.")
        return self


Intervention = Annotated[
    Compact | Checkpoint | Handoff | Rebind | Takeover | Cancel | Quarantine,
    Field(discriminator="kind"),
]

# One wire action per lever; a lever missing here is a KeyError in to_wire, and a test walks the
# union so that can only ever happen in a change that also fails the suite.
_ACTIONS: Mapping[type[BaseModel], InterventionAction] = {
    Compact: InterventionAction.COMPACT,
    Checkpoint: InterventionAction.CHECKPOINT,
    Handoff: InterventionAction.HANDOFF,
    Rebind: InterventionAction.REBIND,
    Takeover: InterventionAction.TAKEOVER,
    Cancel: InterventionAction.CANCEL,
    Quarantine: InterventionAction.QUARANTINE,
}

# The levers that carry nothing but their reason, rebuilt from the wire by their class alone.
_PlainLever = Compact | Checkpoint | Handoff | Takeover | Cancel
_PLAIN_LEVERS: Mapping[InterventionAction, type[_PlainLever]] = {
    InterventionAction.COMPACT: Compact,
    InterventionAction.CHECKPOINT: Checkpoint,
    InterventionAction.HANDOFF: Handoff,
    InterventionAction.TAKEOVER: Takeover,
    InterventionAction.CANCEL: Cancel,
}


def to_wire(intervention: Intervention) -> tuple[InterventionAction, str | None]:
    """Convert an Intervention into the wire's InterventionAction plus its optional slot.

    Args:
        intervention: The lever a Supervisor decided to pull.

    Returns:
        The matching InterventionAction, and the wire slot string when `intervention` is a
        Rebind (None for every other lever).
    """
    action = _ACTIONS[type(intervention)]
    # An isinstance check, not a `match` on `.kind`: scripts/check_no_kind_branches.py flags any
    # match statement whose subject is a `.kind` attribute (its real target is CellKind).
    slot = intervention.slot.to_wire() if isinstance(intervention, Rebind) else None
    return action, slot


def to_intervene(
    intervention: Intervention, *, task_id: TaskId | None = None, alarm_id: AlarmId | None = None
) -> Intervene:
    """Build the whole wire Intervene for `intervention`.

    Args:
        intervention: The lever a Supervisor decided to pull.
        task_id: The task concerned, for every lever but Quarantine, which names its own.
        alarm_id: The Alarm this intervention answers, so the trail links the two.

    Returns:
        The Intervene a supervisor sends; a Quarantine's bee travels as `subject`, and its task
        and suspect episode as their own fields.
    """
    action, slot = to_wire(intervention)
    if isinstance(intervention, Quarantine):
        return Intervene(
            action=action,
            subject=intervention.bee,
            task_id=intervention.task_id,
            slot=None,
            alarm_id=alarm_id,
            reason=intervention.reason,
            suspect_episode_id=intervention.suspect_episode_id,
        )
    return Intervene(
        action=action,
        subject=None,
        task_id=task_id,
        slot=slot,
        alarm_id=alarm_id,
        reason=intervention.reason,
    )


def from_wire(wire: Intervene) -> Intervention:
    """Convert a received Intervene into the matching Intervention variant.

    Args:
        wire: The waggle.messages.supervision.Intervene read off an Envelope.

    Returns:
        The matching Intervention variant, with `reason` copied from the wire message.

    Raises:
        SupervisionError: `wire.action` is REBIND with no slot, or QUARANTINE with no suspect
            episode. Intervene's own validators forbid sending either, so this only guards
            against a peer that skipped validation.
        UnknownInterventionError: `wire.action` names no lever of the union (RELEASE_LEASE, which
            a Warden carries out itself before this is ever called); never read as a cancel.
    """
    if wire.action is InterventionAction.REBIND:
        if wire.slot is None:
            raise SupervisionError(f"Intervene REBIND carries no slot: {wire!r}")
        return Rebind(reason=wire.reason, slot=ModelSlot.from_wire(wire.slot))
    if wire.action is InterventionAction.QUARANTINE:
        return _quarantine_from_wire(wire)
    lever = _PLAIN_LEVERS.get(wire.action)
    # No variant for this action: refused outright, since any lever chosen in its place would
    # carry out an order nobody gave (ADR-0043).
    if lever is None:
        raise UnknownInterventionError(wire.action.value)
    return lever(reason=wire.reason)


def _quarantine_from_wire(wire: Intervene) -> Quarantine:
    """Rebuild a Quarantine from its wire form, refusing one without its suspect episode."""
    if wire.suspect_episode_id is None:
        raise SupervisionError(f"Intervene QUARANTINE carries no suspect episode: {wire!r}")
    return Quarantine(
        reason=wire.reason,
        bee=wire.subject,
        task_id=wire.task_id,
        suspect_episode_id=wire.suspect_episode_id,
    )
