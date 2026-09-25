"""Define the pending-confirmation records: a held request, its settlement, the action handed back.

When a device no person types at asks for something that needs step-up, the Entrance does not
refuse it outright: it holds the request as a ``PendingConfirmation`` (the device, the action's
kind, a bounded JSON payload the caller needs to carry it out later, and an expiry), answers ``403
step_up_required`` with its id, and pushes it to the human (ADR-0041). A person confirms it from an
interactive device that has just stepped up; the confirmation hands back the ``HeldAction`` for the
caller to carry out, once. ``Settlement`` is one status change a settler asks the pending table
for. Ids are ``pend_`` plus a ULID, so they sort by when the request was held.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.confirm``. Built by
    the confirmation flow; stored by ``hivemind.entrance.store.pending``. Calls into
    ``hivemind.entrance.auth.step_up.rules`` (the action kinds), this package's state machine and
    waggle.

Key invariants:
    - A payload serialises to at most ``MAX_HELD_PAYLOAD_BYTES`` of JSON.
    - ``settled_at`` is set exactly when the status is not PENDING, and ``confirmed_by`` exactly
      when it is CONFIRMED.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".
    - hivemind.entrance.auth.confirm.flow for hold, confirm, cancel and expiry.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import NewType

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from hivemind.entrance.auth.confirm.state import PendingStatus
from hivemind.entrance.auth.step_up.rules import ActionKind
from waggle.clock import Clock
from waggle.ids import DeviceId
from waggle.messages.base import DeviceIdField, UtcDatetime
from waggle.ulid import encode_ulid

PENDING_ID_PREFIX = "pend_"  # Distinguishes a pending confirmation's id in a log line.
MAX_HELD_PAYLOAD_BYTES = 65_536  # A goal request with its description; never a file upload.
_ULID_RANDOM_BYTES = 10  # A ULID's 80 random bits, fresh for every id.
_MILLISECONDS_PER_SECOND = 1000  # A ULID's timestamp is in milliseconds.
_PENDING_ID_PATTERN = r"^pend_[0-9A-HJKMNP-TV-Z]{26}$"  # pend_ plus a Crockford ULID.

# A frozen, extras-forbidding config every model here shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

PendingId = NewType("PendingId", str)  # A pending confirmation's id: pend_ plus a ULID.

__all__ = [
    "MAX_HELD_PAYLOAD_BYTES",
    "PENDING_ID_PREFIX",
    "HeldAction",
    "PendingConfirmation",
    "PendingId",
    "Settlement",
    "new_pending_id",
]


def new_pending_id(clock: Clock) -> PendingId:
    """Mint a pending confirmation id: ``pend_`` and a ULID timestamped by ``clock``.

    Args:
        clock: Stamps the ULID, so ids sort by when the request was held.

    Returns:
        A fresh id with 80 random bits.
    """
    timestamp_ms = int(clock.now().timestamp() * _MILLISECONDS_PER_SECOND)
    ulid = encode_ulid(timestamp_ms, secrets.token_bytes(_ULID_RANDOM_BYTES))
    return PendingId(f"{PENDING_ID_PREFIX}{ulid}")


class PendingConfirmation(BaseModel):
    """A request held until a person confirms it: what the pending table stores.

    Crosses into the Entrance tables (``entrance_pending``) and, as its id and kind, out through
    the ``403 step_up_required`` answer and the push that tells the human.
    """

    model_config = _MODEL_CONFIG

    id: PendingId = Field(pattern=_PENDING_ID_PATTERN, description="pend_ plus a ULID.")
    device_id: DeviceIdField = Field(description="The non-interactive device that asked.")
    action: ActionKind = Field(description="What it asked for.")
    payload: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="What the caller needs to carry the action out: identifiers, amounts, the "
        "request body; bounded by MAX_HELD_PAYLOAD_BYTES.",
    )
    status: PendingStatus = Field(description="Where it is in its state machine.")
    created_at: UtcDatetime = Field(description="When it was held.")
    expires_at: UtcDatetime = Field(description="When it can no longer be confirmed.")
    settled_at: UtcDatetime | None = Field(
        default=None, description="When it was confirmed, expired or cancelled; None while held."
    )
    confirmed_by: DeviceIdField | None = Field(
        default=None, description="The interactive device that confirmed it; None otherwise."
    )

    @model_validator(mode="after")
    def _is_consistent(self) -> PendingConfirmation:
        """Require a bounded payload, ordered times, and settlement fields that match the status."""
        if len(json.dumps(self.payload).encode("utf-8")) > MAX_HELD_PAYLOAD_BYTES:
            raise ValueError(f"A held payload is at most {MAX_HELD_PAYLOAD_BYTES} bytes of JSON.")
        if self.expires_at <= self.created_at:
            raise ValueError("A pending confirmation expires after it is held.")
        if (self.status is PendingStatus.PENDING) != (self.settled_at is None):
            raise ValueError("settled_at is set exactly when the confirmation is settled.")
        if (self.status is PendingStatus.CONFIRMED) != (self.confirmed_by is not None):
            raise ValueError("confirmed_by is set exactly when the confirmation is CONFIRMED.")
        return self


@dataclass(frozen=True, slots=True)
class Settlement:
    """One settlement a caller asks the pending table for.

    Attributes:
        new: The status to move to: CONFIRMED, EXPIRED or CANCELLED.
        at: When.
        confirmed_by: The confirming device; set exactly for CONFIRMED.
    """

    new: PendingStatus
    at: datetime
    confirmed_by: DeviceId | None = None


@dataclass(frozen=True, slots=True)
class HeldAction:
    """A confirmed request, handed back for the caller to carry out exactly once.

    Attributes:
        pending_id: The confirmation it came from.
        device_id: The device that asked for it.
        action: What it asked for.
        payload: What it held to carry it out.
        confirmed_by: The interactive device that confirmed it.
    """

    pending_id: PendingId
    device_id: DeviceId
    action: ActionKind
    payload: dict[str, JsonValue] = field(repr=False)
    confirmed_by: DeviceId
