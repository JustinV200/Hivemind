"""Define the forage family's grant messages: a grant issued or revoked, a request and its reply.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Forage is
the Hive's capacity as data, in several dimensions and never one number, and the shared half of
it is divided by grant: a lease with an expiry that the Queen (the central orchestrator) issues
to a Warden (the always-on supervisor of one Cell, a unit of compute), re-issues to grow, shrink
or top up, and can take back entirely. The four messages here are that grant's life:
``GrantIssued`` (every revision of the terms), ``GrantRevoked`` (the whole grant withdrawn),
``ForageRequest`` (a Warden asks for shared Forage beyond its grant, with the task's Tempo, its
speed-against-accuracy setting, as an allocator input) and ``ForageReply`` (granted, partly
granted or denied, with the reason). Within its grant a Warden never asks; outside it, it asks
only while connected and the ask waits in the outbox otherwise. The family's capacity and hosting
messages live in ``waggle.messages.forage_hosting`` and its value models in
``waggle.messages.forage_values`` and ``waggle.messages.forage_capacity``, split out by
responsibility so every file stays under the codingrules 5.1 size limit. Every bound is a named
constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by the Queen's allocator and by every Warden, read by the
    Warden's Fanner (the seat meter every model call passes through); calls into
    waggle.messages.base, waggle.messages.labels and waggle.messages.forage_values only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A ForageRequest never asks for nothing, and a denied ForageReply never carries terms.

See Also:
    - docs/waggle/spec.md section 8.4 for the normative fields, bounds and validators.
    - waggle.messages.forage_hosting for CapacityReport, HostingDecided, CeilingsSet and
      PlanWritten.
    - waggle.messages.forage_values for the value models and enums these messages carry.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    MAX_SUB_BEES_ON_WIRE,
    CellIdField,
    GrantIdField,
    TaskIdField,
    UtcDatetime,
    WaggleMessage,
    WardenIdField,
)
from waggle.messages.forage_values import (
    AllowedBinding,
    ForageDelta,
    ForageOutcome,
    ForageRequestKind,
    RevocationCause,
    SeatReservation,
)
from waggle.messages.labels import Tempo

MAX_ALLOWED_BINDINGS = 32  # One per slot a Warden's sub-bees can fill; the slot enum is smaller.
MAX_SEAT_RESERVATIONS = 32  # One per shared source a grant reserves on; a handful in practice.

__all__ = [
    "MAX_ALLOWED_BINDINGS",
    "MAX_SEAT_RESERVATIONS",
    "ForageReply",
    "ForageRequest",
    "GrantIssued",
    "GrantRevoked",
]


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]


class GrantIssued(WaggleMessage):
    """Issue or re-issue the shared half of a Warden's Forage (forage.grant_issued, an event).

    A lease with an expiry: the same grant_id with a higher revision replaces the previous terms,
    whether they grew, shrank or were topped up, so the holder discards a stale replay by
    revision alone.
    """

    grant_id: GrantIdField = Field(description="The grant's identity, stable across revisions.")
    holder: WardenIdField = Field(description="The Warden that holds it.")
    cell_id: CellIdField = Field(description="The Cell the holder runs.")
    task_id: TaskIdField | None = Field(
        description="The goal or task whose caps sized the budgets; None for a standing grant."
    )
    revision: int = Field(
        ge=0,
        description="0 on first issue, incremented on every change so the holder discards "
        "stale replays.",
    )
    allowed: tuple[AllowedBinding, ...] = Field(
        max_length=MAX_ALLOWED_BINDINGS, description="Bindings the holder may use."
    )
    seats: tuple[SeatReservation, ...] = Field(
        max_length=MAX_SEAT_RESERVATIONS, description="Reservations the Fanner enforces."
    )
    token_budget: int = Field(ge=0, description="Total tokens the grant allows.")
    spend_budget: float = Field(ge=0, description="Total spend the grant allows.")
    tokens_spent: int = Field(ge=0, description="Tokens already consumed at issue time.")
    spent: float = Field(ge=0, description="Spend already consumed at issue time.")
    max_sub_bees: int = Field(
        ge=0,
        le=MAX_SUB_BEES_ON_WIRE,
        description="Sub-bees the holder may run under this grant.",
    )
    expires_at: UtcDatetime = Field(
        description="When the grant returns to the pool unless renewed by heartbeat."
    )
    reason: _Reason = Field(description="Why these terms: the allocator's decision.")


class GrantRevoked(WaggleMessage):
    """Take a grant back entirely (forage.grant_revoked, an event).

    The holder must stop drawing on shared Forage at once. Shrinking is a GrantIssued revision,
    never a revocation.
    """

    grant_id: GrantIdField = Field(description="The revoked grant.")
    holder: WardenIdField = Field(description="The Warden that held it.")
    revision: int = Field(
        ge=0,
        description="The grant revision revoked; a holder whose revision is higher ignores the "
        "message as a stale replay (a receiver rule).",
    )
    cause: RevocationCause = Field(description="Which rule revoked it.")
    reason: _Reason = Field(description="The Queen's reason.")


class ForageRequest(WaggleMessage):
    """Ask for shared Forage beyond the standing grant (forage.request, a request).

    More shared seats, spend, a higher-grade binding or more sub-bees, with a reason and the
    task's Tempo. Answered only while connected; offline it waits in the outbox.
    """

    grant_id: GrantIdField = Field(description="The grant the Warden wants extended.")
    kind: ForageRequestKind = Field(description="Which dimension is asked for.")
    wanted: ForageDelta = Field(description="The delta wanted; at least one non-zero field.")
    task_id: TaskIdField | None = Field(
        description="The task the extra Forage serves; None for the Warden's own needs."
    )
    tempo: Tempo = Field(description="The requesting task's Tempo, an allocator input.")
    reason: _Reason = Field(description="Why the Warden needs it.")

    @model_validator(mode="after")
    def _wanted_is_not_empty(self) -> ForageRequest:
        """Reject a request that asks for nothing."""
        # An empty delta would still cost the Queen an allocator pass and a reply, and a holder
        # that sends one has a bug; refusing it at the wire keeps the allocator's inputs honest.
        if self.wanted.is_empty:
            raise ValueError(
                f"A {self.kind.value} ForageRequest must want at least one non-zero field."
            )
        return self


class ForageReply(WaggleMessage):
    """Grant, partly grant or deny a ForageRequest with a reason (forage.reply, a reply).

    A granted delta is folded into the next GrantIssued revision, which this reply names.
    """

    grant_id: GrantIdField = Field(description="The grant the request was against.")
    outcome: ForageOutcome = Field(description="Granted in full, in part, or denied.")
    granted: ForageDelta = Field(description="What was actually granted; all zeros when denied.")
    revision: Annotated[int, Field(ge=0)] | None = Field(
        description="The grant revision that now carries the delta; None exactly when denied."
    )
    expires_at: UtcDatetime | None = Field(
        description="The grant's expiry after this change; None exactly when denied."
    )
    reason: _Reason = Field(description="Why: headroom, contest, reserve, cost cap.")

    @model_validator(mode="after")
    def _terms_match_outcome(self) -> ForageReply:
        """Require a revision and an expiry unless denied, and an empty grant when denied."""
        # A denial carries no terms: a revision or expiry on it would tell the holder a grant
        # changed when none did, and a non-empty delta would be a grant in disguise. A grant
        # without them leaves the holder unable to tell which revision to expect or when it
        # ends. Both directions are checked.
        denied = self.outcome is ForageOutcome.DENIED
        if denied != (self.revision is None):
            raise ValueError(
                f"ForageReply revision must be None exactly when denied, got outcome "
                f"{self.outcome.value} with revision {self.revision}."
            )
        if denied != (self.expires_at is None):
            raise ValueError(
                f"ForageReply expires_at must be None exactly when denied, got outcome "
                f"{self.outcome.value} with expires_at {self.expires_at}."
            )
        if denied and not self.granted.is_empty:
            raise ValueError("A denied ForageReply must carry an all-zero granted delta.")
        return self
