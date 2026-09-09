"""Define Intervention: the six levers a Supervisor pulls on a child, and their wire conversion.

`Supervisor.intervene` (codingrules section 8.8) is how the Queen delegates and rebinds rather than
executing: compact a bee's context in place, checkpoint it, hand it off to a fresh bee, rebind it
to another model slot, take it over, or cancel it. Each lever is its own frozen pydantic model
carrying a `reason`, discriminated by a `kind` literal exactly like `waggle.messages.forage`'s
own discriminated unions, so a caller can `match` (or, per this repository's own hygiene script,
an `isinstance` chain) on the concrete type rather than on a bare string. `to_wire`/`from_wire`
convert to and from `waggle.messages.supervision.Intervene`, the wire message carrying the same
six levers as one `InterventionAction` enum plus an optional slot.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by whichever `Supervisor`
    implementation calls `intervene`, and read on the receiving bee's side after `from_wire`.
    Calls into `hivemind.forage` (for `ModelSlot`, the only model-shaped thing this module names)
    and waggle only.

Key invariants:
    - Every Intervention variant is frozen and forbids extras, like every boundary value here.
    - to_wire and from_wire are inverses on every variant except that to_wire drops nothing:
      Rebind's slot is the only lever-specific field, and it round-trips through ModelSlot's own
      to_wire/from_wire.
    - Rebind.slot is a hivemind.forage.ModelSlot, never a bare provider or model name
      (codingrules section 8.6): "the Queen steps in" and "the Queen gives the bee a better
      model" are the same move, made by naming a slot.

See Also:
    - .claude/codingrules.md section 8.8 for the Queen's and a Warden's intervention levers.
    - waggle.messages.supervision for Intervene and InterventionAction, the wire forms this module
      converts to and from.
    - hivemind.forage.slots for ModelSlot, the type Rebind.slot carries.
    - hivemind.supervision.supervisor for Supervisor.intervene, which this type is the argument of.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from hivemind.forage import ModelSlot
from hivemind.supervision.errors import SupervisionError
from waggle.messages.supervision import Intervene, InterventionAction

__all__ = [
    "Cancel",
    "Checkpoint",
    "Compact",
    "Handoff",
    "Intervention",
    "Rebind",
    "Takeover",
    "from_wire",
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

    # Written out literally, not through a shared name: the pydantic mypy plugin only reads
    # frozen=True off a literal ConfigDict on the class body (see waggle.messages.base's own
    # note on WaggleMessage.model_config for the same caveat).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["checkpoint"] = "checkpoint"
    reason: str = Field(description="Why the supervisor intervenes.")


class Handoff(BaseModel):
    """Write a Handoff and stop; a fresh bee resumes the task from it."""

    # Written out literally, not through a shared name: the pydantic mypy plugin only reads
    # frozen=True off a literal ConfigDict on the class body (see waggle.messages.base's own
    # note on WaggleMessage.model_config for the same caveat).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["handoff"] = "handoff"
    reason: str = Field(description="Why the supervisor intervenes.")


class Rebind(BaseModel):
    """Move the bee to another model slot; "a better model" and "the Queen steps in" are this."""

    # Written out literally, not through a shared name: the pydantic mypy plugin only reads
    # frozen=True off a literal ConfigDict on the class body (see waggle.messages.base's own
    # note on WaggleMessage.model_config for the same caveat).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["rebind"] = "rebind"
    reason: str = Field(description="Why the supervisor intervenes.")
    slot: ModelSlot = Field(description="The model slot to rebind the bee to.")


class Takeover(BaseModel):
    """Spawn a bee on the same Cell with the supervisor's own slot and the stuck bee's Handoff."""

    # Written out literally, not through a shared name: the pydantic mypy plugin only reads
    # frozen=True off a literal ConfigDict on the class body (see waggle.messages.base's own
    # note on WaggleMessage.model_config for the same caveat).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["takeover"] = "takeover"
    reason: str = Field(description="Why the supervisor intervenes.")


class Cancel(BaseModel):
    """Stop the bee for good; nothing resumes it."""

    # Written out literally, not through a shared name: the pydantic mypy plugin only reads
    # frozen=True off a literal ConfigDict on the class body (see waggle.messages.base's own
    # note on WaggleMessage.model_config for the same caveat).
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["cancel"] = "cancel"
    reason: str = Field(description="Why the supervisor intervenes.")


Intervention = Annotated[
    Compact | Checkpoint | Handoff | Rebind | Takeover | Cancel, Field(discriminator="kind")
]


def to_wire(intervention: Intervention) -> tuple[InterventionAction, str | None]:
    """Convert an Intervention into the wire's InterventionAction plus its optional slot.

    Args:
        intervention: The lever a Supervisor decided to pull.

    Returns:
        The matching InterventionAction, and the wire slot string when `intervention` is a
        Rebind (None for every other lever).
    """
    # An isinstance chain, not a `match` on `.kind`: scripts/check_no_kind_branches.py flags any
    # match statement whose subject is a `.kind` attribute, and this file's `kind` discriminator
    # is unrelated to that script's actual target (CellKind); an isinstance chain sidesteps the
    # false positive without weakening the check for its real purpose.
    if isinstance(intervention, Rebind):
        return InterventionAction.REBIND, intervention.slot.to_wire()
    if isinstance(intervention, Compact):
        return InterventionAction.COMPACT, None
    if isinstance(intervention, Checkpoint):
        return InterventionAction.CHECKPOINT, None
    if isinstance(intervention, Handoff):
        return InterventionAction.HANDOFF, None
    if isinstance(intervention, Takeover):
        return InterventionAction.TAKEOVER, None
    return InterventionAction.CANCEL, None  # the only remaining variant, Cancel.


def from_wire(wire: Intervene) -> Intervention:
    """Convert a received Intervene into the matching Intervention variant.

    Args:
        wire: The waggle.messages.supervision.Intervene read off an Envelope.

    Returns:
        The matching Intervention variant, with `reason` copied from the wire message.

    Raises:
        SupervisionError: `wire.action` is REBIND but `wire.slot` is None. Intervene's own
            validator forbids sending such a message, so this only guards against a peer that
            skipped validation.
    """
    if wire.action is InterventionAction.REBIND:
        if wire.slot is None:
            raise SupervisionError(f"Intervene REBIND carries no slot: {wire!r}")
        return Rebind(reason=wire.reason, slot=ModelSlot.from_wire(wire.slot))
    if wire.action is InterventionAction.COMPACT:
        return Compact(reason=wire.reason)
    if wire.action is InterventionAction.CHECKPOINT:
        return Checkpoint(reason=wire.reason)
    if wire.action is InterventionAction.HANDOFF:
        return Handoff(reason=wire.reason)
    if wire.action is InterventionAction.TAKEOVER:
        return Takeover(reason=wire.reason)
    return Cancel(reason=wire.reason)  # the only remaining action, CANCEL.
