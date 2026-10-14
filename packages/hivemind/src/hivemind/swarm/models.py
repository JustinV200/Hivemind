"""Define SwarmNode, SwarmInvite and InviteState: what the Hive knows about a device and its invite.

A Swarm node is one enrolled device (an existing machine the Hive borrows as a Real Cell and
leaves exactly as found), reached through its Pollen Packet (the thin gateway installed on it).
`SwarmNode` is the registry's record of it: the ids the Queen (the Hive's central orchestrator)
minted for it (`cell_id` for the Real Cell it is, `warden_id` for the Warden that supervises it
at every rung of the runtime ladder), its pinned public key, what it reported about itself at
enrolment, the access level (how much of the device the Hive may touch) and Comb Shield tier (its
security tier) the operator allowed, and the capability strings the packet enforces locally.
`as_cell()` turns it into the `hivemind.cell.Cell` placement reads. `SwarmInvite` is the other
record: one single-use, short-lived invite the operator minted with `hive swarm invite`, kept as
the SHA-256 of its token and never the token itself. `InviteState` and `INVITE_TRANSITIONS` are
its machine (codingrules Appendix C): issued, then exactly one of redeemed, expired or revoked.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Stored by the swarm registry
    (`hivemind.swarm.registry`), built by the enrolment door (`hivemind.swarm.enrolment`), read by
    the gateway, `SwarmSource` and the Queen's swarm gate (Layer 6). Calls into hivemind.cell,
    hivemind.forage, hivemind.swarm.node_state and waggle only.

Key invariants:
    - A node's `comb_shield` is never NIGHT_VEIL (a Real Cell cannot hold the virtual-only tier).
    - `revoked_at` is set exactly when `status` is REVOKED, and `desired_level` is None or a rung
      the node is not already on. `ceiling` is any rung or None: a device may sit on its ceiling.
    - A node's `platform` is derived from its `capabilities`, so the two cannot disagree.
    - An invite stores only its token's hash, and `device_id` is set exactly when it is REDEEMED.

See Also:
    - docs/waggle/enrolment.md sections 3.2 and 7 for the invite row and the node it creates.
    - .claude/codingrules.md Appendix C, "Swarm invite" and "Swarm node status" rows.
    - hivemind.swarm.node_state and hivemind.swarm.level for the two machines on a node.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hivemind.cell import AccessLevel, Cell, CellCapabilities, CellKind, CombShieldLevel
from hivemind.cell.models import MAX_CELL_NAME_CHARS
from hivemind.forage import ForageCapacity
from hivemind.swarm.errors import InviteConflictError
from hivemind.swarm.node_state import NodeStatus
from waggle.messages.base import (
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    CellIdField,
    DeviceIdField,
    NodeIdField,
    UtcDatetime,
    WardenIdField,
)
from waggle.messages.reports import PlatformReport
from waggle.messages.swarm import RuntimeLevel
from waggle.messages.swarm.enrolment import (
    MAX_CAPABILITY_CHARS,
    MAX_GRANTED_CAPABILITIES,
    MAX_HOSTNAME_CHARS,
    MAX_RUNTIME_VERSION_CHARS,
    PUBLIC_KEY_HEX_PATTERN,
)

SWARM_SOURCE = "swarm"  # The `source` every Cell a Swarm node makes carries (codingrules 6.1).
MAX_LABEL_CHARS = 64  # The operator's name for a device ("kitchen-pi"); a label, never a sentence.

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = [
    "INVITE_TRANSITIONS",
    "MAX_LABEL_CHARS",
    "SWARM_SOURCE",
    "InviteState",
    "SwarmInvite",
    "SwarmNode",
    "assert_invite_transition",
]


class InviteState(Enum):
    """Where one device invite is in its short life: issued, then exactly one way out."""

    ISSUED = "ISSUED"  # Minted and still admitting one device until it expires.
    REDEEMED = "REDEEMED"  # A device presented its token and became a node: final.
    EXPIRED = "EXPIRED"  # The time to live lapsed before anyone redeemed it: final.
    REVOKED = "REVOKED"  # The operator cancelled it before it was used: final.


# The single transition table for invites (codingrules section 9), one entry per state with a
# comment on every edge. The three later states are final, so a second redemption of a token
# finds REDEEMED and is denied.
INVITE_TRANSITIONS: Mapping[InviteState, frozenset[InviteState]] = {
    InviteState.ISSUED: frozenset(
        {
            InviteState.REDEEMED,  # the enrolment that presented the token, with the node row
            InviteState.EXPIRED,  # invite_ttl_minutes passed before anyone presented it
            InviteState.REVOKED,  # `hive swarm revoke` cancelled it
        }
    ),
    InviteState.REDEEMED: frozenset(),  # final: a token admits exactly one device
    InviteState.EXPIRED: frozenset(),  # final: a lapsed invite is never revived
    InviteState.REVOKED: frozenset(),  # final: a cancelled invite is never revived
}


def assert_invite_transition(
    from_state: InviteState, to_state: InviteState, code_hash: str | None = None
) -> None:
    """Raise unless INVITE_TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The invite's current state.
        to_state: The state a caller wants to move it to.
        code_hash: The invite's token hash, when the caller has it, folded into the message.

    Raises:
        InviteConflictError: `to_state` is not one of the edges listed for `from_state`, for
            instance redeeming an invite that was already redeemed.
    """
    if to_state not in INVITE_TRANSITIONS[from_state]:
        subject = f" {code_hash[:12]}..." if code_hash is not None else ""
        raise InviteConflictError(
            f"Invite{subject} cannot move from {from_state.name} to {to_state.name}: no such "
            "edge exists in the invite machine."
        )


class SwarmInvite(BaseModel):
    """One device invite as the registry stores it: the token's hash, the cap and the tier.

    The token itself exists only in the invite string the operator was shown once; the access
    cap and the tier ride on this row and never on the wire, so a device can ask for an access
    level but cannot raise the ceiling it is asked against.
    """

    model_config = _MODEL_CONFIG

    code_hash: str = Field(
        pattern=SHA256_PATTERN,
        description="The SHA-256 of the token exactly as it travels, lowercase hex; the only "
        "trace of the token the Hive keeps.",
    )
    label: str | None = Field(
        default=None,
        max_length=MAX_LABEL_CHARS,
        description="The operator's name for the device, shown in `hive swarm list`.",
    )
    access_level: AccessLevel = Field(
        description="The most the device may be granted; it receives the lower of this and the "
        "level it asks for."
    )
    comb_shield: CombShieldLevel = Field(
        description="The tier the operator vouches for: MEADOW, or PROPOLIS for a device that "
        "already runs a VPN. Never NIGHT_VEIL."
    )
    created_at: UtcDatetime = Field(description="When the invite was minted.")
    expires_at: UtcDatetime = Field(description="When it stops being accepted.")
    state: InviteState = Field(
        default=InviteState.ISSUED, description="Where the invite is in its machine."
    )
    device_id: DeviceIdField | None = Field(
        default=None, description="The device it admitted; set exactly when REDEEMED."
    )

    @field_validator("comb_shield")
    @classmethod
    def _never_night_veil(cls, value: CombShieldLevel) -> CombShieldLevel:
        """Reject the virtual-only tier on a Real Cell's invite."""
        # NIGHT_VEIL is teardown-only (VPN plus Tor on a Cell that is destroyed afterwards); a
        # borrowed device can never be torn down, so no invite may carry it.
        if value is CombShieldLevel.NIGHT_VEIL:
            raise ValueError("A Swarm invite is never NIGHT_VEIL: that tier is for Virtual Cells.")
        return value

    @model_validator(mode="after")
    def _lifetime_and_device_match(self) -> SwarmInvite:
        """Require a positive lifetime, and a device exactly when the invite was redeemed."""
        # An invite that expires before it exists could never admit anyone.
        if self.expires_at <= self.created_at:
            raise ValueError("A Swarm invite must expire after it is created.")
        # The device an invite admitted is the one fact that distinguishes REDEEMED, so the pair
        # must agree in both directions.
        if (self.device_id is not None) != (self.state is InviteState.REDEEMED):
            raise ValueError(
                f"A Swarm invite's device_id is set exactly when it is REDEEMED, got state "
                f"{self.state.value} with device_id {self.device_id}."
            )
        return self


class SwarmNode(BaseModel):
    """One enrolled device: its ids, its pinned key, its limits and its present condition.

    Frozen like every boundary value (codingrules 8.5): a change (a heartbeat, a promotion, a new
    access level) produces a new node through the registry, never an edit in place.
    """

    model_config = _MODEL_CONFIG

    device_id: DeviceIdField = Field(description="The packet's bee address for life.")
    node_id: NodeIdField = Field(description="The node whose key signs the device's frames.")
    cell_id: CellIdField = Field(description="The Real Cell the device is, minted by the Queen.")
    warden_id: WardenIdField = Field(
        description="The device's Warden at every rung of the ladder, minted at enrolment and "
        "kept for the life of the node."
    )
    public_key_hex: str = Field(
        pattern=PUBLIC_KEY_HEX_PATTERN,
        description="The device's raw Ed25519 public key, pinned at enrolment and replaced only "
        "by enrolling again.",
    )
    hostname: str = Field(
        max_length=MAX_HOSTNAME_CHARS,
        description="The device's hostname. Text the device wrote: shown to the operator, "
        "trusted for nothing.",
    )
    label: str | None = Field(
        default=None, max_length=MAX_LABEL_CHARS, description="The operator's name from the invite."
    )
    runtime_version: str = Field(
        max_length=MAX_RUNTIME_VERSION_CHARS, description="The `pollen` version it last reported."
    )
    capabilities: CellCapabilities = Field(
        description="What the device is and can do, as it reported at enrolment."
    )
    capacity: ForageCapacity = Field(description="Its latest Forage capacity report.")
    requested_access: AccessLevel = Field(description="The access level the packet asked for.")
    access_level: AccessLevel = Field(
        description="The level granted: the lower of what it asked for and the invite's cap."
    )
    comb_shield: CombShieldLevel = Field(
        description="The operator-set tier: MEADOW or PROPOLIS, never NIGHT_VEIL."
    )
    status: NodeStatus = Field(
        default=NodeStatus.ONLINE, description="Whether the Hive can reach it right now."
    )
    level: RuntimeLevel = Field(
        default=RuntimeLevel.GATEWAY, description="The rung of the runtime ladder it is on."
    )
    desired_level: RuntimeLevel | None = Field(
        default=None,
        description="A rung the operator asked for with `promote` or `demote` and the Queen has "
        "not yet reached; None when nothing is pending.",
    )
    ceiling: RuntimeLevel | None = Field(
        default=None,
        description="The highest rung the Queen may take this device to on her own. The "
        "operator's `demote` sets it to the rung the device lands on, which pins it there; their "
        "`promote` clears it. None leaves her free to climb as far as her own reasons carry "
        "her. It never limits the operator.",
    )
    granted_capabilities: tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...] = (
        Field(
            max_length=MAX_GRANTED_CAPABILITIES,
            description="The capability strings the packet checks locally on every request.",
        )
    )
    offline_limit_s: float = Field(
        gt=0, description="The dead-man limit for a device with no Warden on it."
    )
    heartbeat_interval_s: float = Field(
        gt=0, description="How often the packet reports; the cadence liveness is judged against."
    )
    max_sub_bees: int | None = Field(
        default=None,
        ge=0,
        description="The operator's cap on concurrent sub-bees on the device; None for no cap.",
    )
    enrolled_at: UtcDatetime = Field(description="When its invite was redeemed.")
    last_seen_at: UtcDatetime | None = Field(
        default=None, description="When a verified frame last arrived; None before the first."
    )
    revoked_at: UtcDatetime | None = Field(
        default=None, description="When it was revoked; set exactly when REVOKED."
    )
    reason: str = Field(
        max_length=MAX_REASON_CHARS, description="Why it was admitted at this level and tier."
    )

    @field_validator("comb_shield")
    @classmethod
    def _never_night_veil(cls, value: CombShieldLevel) -> CombShieldLevel:
        """Reject the virtual-only tier on a Real Cell."""
        # The same rule Cell enforces for kind REAL, caught here at the registry boundary so a
        # bad row never reaches as_cell().
        if value is CombShieldLevel.NIGHT_VEIL:
            raise ValueError("A Swarm node is never NIGHT_VEIL: that tier is for Virtual Cells.")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> SwarmNode:
        """Check the cross-field rules: revocation and the pending rung agree with the rest."""
        # revoked_at is the timestamp of the one final status; both directions are checked.
        if (self.revoked_at is not None) != (self.status is NodeStatus.REVOKED):
            raise ValueError(
                f"A node's revoked_at is set exactly when REVOKED, got status "
                f"{self.status.value} with revoked_at {self.revoked_at}."
            )
        # A pending rung the node is already on is nothing pending; the registry clears it.
        if self.desired_level is not None and self.desired_level is self.level:
            raise ValueError(f"A node's desired_level is already its level {self.level.value}.")
        return self

    @property
    def platform(self) -> PlatformReport:
        """The wire form of its platform facts, derived from `capabilities`.

        Derived rather than stored so the two cannot disagree after a capability report updates
        one of them.
        """
        return self.capabilities.to_wire()[0]

    def as_cell(self) -> Cell:
        """Build the `hivemind.cell.Cell` placement and Wardens read for this device.

        Returns:
            A REAL Cell, source `"swarm"`, named for the operator's label (or the hostname),
            carrying this node's capabilities, capacity, access level and Comb Shield tier.
        """
        return Cell(
            id=self.cell_id,
            kind=CellKind.REAL,
            name=(self.label or self.hostname)[:MAX_CELL_NAME_CHARS],
            source=SWARM_SOURCE,
            capabilities=self.capabilities,
            capacity=self.capacity,
            access_level=self.access_level,
            comb_shield=self.comb_shield,
        )
