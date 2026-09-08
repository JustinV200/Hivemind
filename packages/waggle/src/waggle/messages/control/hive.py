"""Define the control family's Hive-wide messages: a human's words, a mask override, a relocation.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Its control
family is protocol housekeeping plus Hive-wide orders; the three messages here are the Hive-wide
half, split out of ``waggle.messages.control.protocol`` by responsibility so each file stays under
the codingrules 5.1 size limit. ``HumanMessage`` carries the human's own words into the Hive, from
the device they spoke on into the Queen's (the central orchestrator's) inbox or on to the Warden
(the always-on supervisor of one Cell, a unit of compute) of the task it concerns. ``MaskOverride``
forces or clears a Pheromone Mask (a short-lived behaviour overlay that makes a bee write or move
like a human) at the scope of one Cell. ``QueenMoved`` is the notice that the Hive Stand (the
machine the Queen runs on) has moved, signed with the Hive key, and the one address that ever
crosses Waggle. Every bound is a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by the Queen (and by a device, for HumanMessage) and read by
    every Warden and Pollen Packet; calls into waggle.messages.base, waggle.messages.labels
    and waggle.uris only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.

See Also:
    - docs/waggle/spec.md section 8.11 for the normative fields, bounds and validators.
    - waggle.messages.control.protocol for the family's other six messages and ClusterCause.
    - waggle.uris for the ws://-only-on-loopback rule QueenMoved applies.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, field_validator, model_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    CellIdField,
    DeviceIdField,
    NodeIdField,
    TaskIdField,
    UtcDatetime,
    WaggleMessage,
)
from waggle.messages.labels import HoneyClearance
from waggle.uris import WAGGLE_URI_PATTERN, check_waggle_uri

MIN_HUMAN_TEXT_CHARS = 1  # An empty human message carries nothing to act on.
MAX_HUMAN_TEXT_CHARS = 16_000  # Typed or transcribed speech: a few pages, never a document.
MAX_MASK_TACTICS = 2  # One per tactic that exists: WRITE_LIKE_HUMAN and MOUSE_LIKE_HUMAN.
MAX_ADDRESS_CHARS = 512  # A WebSocket URI, onion hosts included, fits with room to spare.
ADDRESS_PATTERN = WAGGLE_URI_PATTERN  # ^wss?://\S+$; ws:// only on loopback (validator).
PUBLIC_KEY_HEX_PATTERN = r"^[0-9a-f]{64}$"  # 32 raw Ed25519 public-key bytes as lowercase hex.
MIN_SEQUENCE = 1  # Sequences start at 1 so a recipient can persist 0 as "none accepted yet".

__all__ = [
    "ADDRESS_PATTERN",
    "MAX_ADDRESS_CHARS",
    "MAX_HUMAN_TEXT_CHARS",
    "MAX_MASK_TACTICS",
    "MIN_HUMAN_TEXT_CHARS",
    "MIN_SEQUENCE",
    "PUBLIC_KEY_HEX_PATTERN",
    "HumanMessage",
    "MaskOverride",
    "MaskOverrideAction",
    "MaskTactic",
    "QueenMoved",
]


class MaskOverrideAction(Enum):
    """Whether a MaskOverride forces a Pheromone Mask or clears one."""

    FORCE = "FORCE"
    CLEAR = "CLEAR"


class MaskTactic(Enum):
    """The Pheromone Mask tactics a Queen can force at Cell scope."""

    WRITE_LIKE_HUMAN = "WRITE_LIKE_HUMAN"
    MOUSE_LIKE_HUMAN = "MOUSE_LIKE_HUMAN"


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]


def _clearance_from_wire(value: object) -> object:
    """Turn the wire string "C2" back into the enum member the Literal below expects."""
    # pydantic matches a Literal of enum members by identity, in JSON mode too, so the wire
    # value "C2" must become HoneyClearance.C2 first; anything else is left for the Literal
    # check to reject with its own message.
    if isinstance(value, str):
        return HoneyClearance(value)
    return value


class HumanMessage(WaggleMessage):
    """Carry a human's free-text message into the Hive (control.human_message, an event).

    Into the Queen's inbox, or on to the Warden of the task it concerns; labelled C2 by
    provenance because a human's words are personal.
    """

    text: str = Field(
        min_length=MIN_HUMAN_TEXT_CHARS,
        max_length=MAX_HUMAN_TEXT_CHARS,
        description="The message, typed or transcribed.",
    )
    task_id: TaskIdField | None = Field(
        description="The task it concerns; None addresses the Queen at large."
    )
    device_id: DeviceIdField = Field(
        description="The enrolled device the human spoke from; kept in the payload so a relayed "
        "copy still names it."
    )
    clearance: Annotated[Literal[HoneyClearance.C2], BeforeValidator(_clearance_from_wire)] = Field(
        default=HoneyClearance.C2,
        description="Always C2: a human's words are personal by provenance, and a program "
        "or device cannot launder them lower.",
    )


class MaskOverride(WaggleMessage):
    """Force a Pheromone Mask tactic at Cell scope, or clear it (control.mask_override, an event).

    The Warden enforces it on the Cell's sub-bees while active.
    """

    cell_id: CellIdField = Field(description="The Cell the override applies to.")
    action: MaskOverrideAction = Field(description="Force or clear.")
    tactics: tuple[MaskTactic, ...] = Field(
        max_length=MAX_MASK_TACTICS,
        description="The tactics forced; unique, and non-empty exactly when action is FORCE.",
    )
    expires_at: UtcDatetime | None = Field(
        description="When the forced override ends on its own; required exactly when action "
        "is FORCE."
    )
    reason: _Reason = Field(description="Why the Queen forces or clears it.")

    @field_validator("tactics")
    @classmethod
    def _tactics_unique(cls, value: tuple[MaskTactic, ...]) -> tuple[MaskTactic, ...]:
        """Reject a tactic listed twice."""
        # A repeated tactic is a sender bug; refusing it keeps the Warden's set semantics honest.
        if len(set(value)) != len(value):
            raise ValueError("MaskOverride tactics must be unique.")
        return value

    @model_validator(mode="after")
    def _fields_match_action(self) -> MaskOverride:
        """Require tactics and an expiry for FORCE, and neither for CLEAR."""
        # FORCE without tactics forces nothing and FORCE without an expiry never ends; CLEAR
        # with either is ambiguous about what is being cleared. Both directions are checked.
        forcing = self.action is MaskOverrideAction.FORCE
        if forcing != bool(self.tactics):
            raise ValueError(
                f"MaskOverride tactics must be non-empty exactly when action is FORCE, got "
                f"action {self.action.value} with {len(self.tactics)} tactic(s)."
            )
        if forcing != (self.expires_at is not None):
            raise ValueError(
                f"MaskOverride expires_at is required exactly when action is FORCE, got action "
                f"{self.action.value} with expires_at {self.expires_at}."
            )
        return self


class QueenMoved(WaggleMessage):
    """Announce that the Hive Stand has moved (control.queen_moved, an event).

    Signed with the Hive key (spec section 6); a recipient replaces the Queen's address only on
    a notice that key verifies. This is the one address that crosses Waggle.
    """

    sequence: int = Field(
        ge=MIN_SEQUENCE,
        description="Strictly increasing per Hive across every notice, rollbacks included; a "
        "recipient persists the highest accepted value and ignores a lower or equal one.",
    )
    new_address: str = Field(
        max_length=MAX_ADDRESS_CHARS,
        pattern=ADDRESS_PATTERN,
        description="The new Queen's Waggle endpoint, a WebSocket URI (an onion host for a "
        "Night Veil link); ws:// only with a loopback host.",
    )
    new_node_id: NodeIdField = Field(
        description="The new Hive Stand's node id, whose key signs every envelope from the new "
        "Queen."
    )
    new_node_public_key_hex: str = Field(
        pattern=PUBLIC_KEY_HEX_PATTERN,
        description="The new node's raw 32-byte Ed25519 public key, hex; pinned for new_node_id "
        "on acceptance and treated as the Hive key from grace_until.",
    )
    effective_at: UtcDatetime = Field(
        description="When recipients start connecting to the new address."
    )
    grace_until: UtcDatetime = Field(
        description="Until when both addresses are honoured; at least effective_at."
    )
    is_rollback: bool = Field(
        default=False,
        description="True when the old Queen points recipients back at itself after a failed "
        "handover.",
    )
    reason: _Reason = Field(description="Why the move, or the rollback cause.")

    @field_validator("new_address")
    @classmethod
    def _plaintext_only_on_loopback(cls, value: str) -> str:
        """Allow ws:// (no TLS) only when the host is a loopback address (waggle.uris)."""
        # Waggle gives authenticity, never confidentiality; the link does (spec section 6). A
        # plaintext endpoint anywhere but the local machine would expose every frame.
        return check_waggle_uri(value)

    @model_validator(mode="after")
    def _grace_covers_effective(self) -> QueenMoved:
        """Reject a grace period that ends before the move takes effect."""
        # Both addresses are honoured from effective_at until grace_until; a window that ends
        # first would leave recipients with no valid address at all for a while.
        if self.grace_until < self.effective_at:
            raise ValueError(
                f"QueenMoved grace_until {self.grace_until.isoformat()} precedes effective_at "
                f"{self.effective_at.isoformat()}."
            )
        return self
