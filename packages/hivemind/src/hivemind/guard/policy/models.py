"""Define the policy engine's request and decision: who asks, for what, where, and the answer.

`evaluate` (the Guard's pure decision function, ADR-0031) takes one `PolicyRequest` and returns one
`PolicyDecision`. A request names the principal acting (`PrincipalRef`: its kind, its id and the
policy role its set came from), the enforcement point it is passing, the one capability that
action needs, the set the principal holds, and the context the tier floors and the access-level
rule read (`PolicyContext`). A decision says allowed or not, the stable id of the rule that
decided, a reason sentence a human can read on the trail, and what a denial escalates to
(`EscalationAction`). Every model is a frozen boundary value, because a decision is recorded on the
trail and will be returned by the Landing Board.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by every enforcement point
    (roadmap step 10.3) and passed to `hivemind.guard.enforcer.Enforcer.check`; read by
    `hivemind.guard.policy.evaluate`. Calls into `hivemind.cell` (the tier enums and
    RequestOrigin), `hivemind.guard.capabilities`, `.points` and waggle (id validation).

Key invariants:
    - A principal's id matches its kind: the Queen acts as her Hive id, a Warden and a Worker as
      their own ids, a device as its device id, and the operator as `"human"`, the trail's own
      literal for the person, since the operator has no minted id.
    - Every `PolicyContext` field is optional because absence is meaningful: None means "not on
      a Cell" (no tier, no access level), "not yet placed" (no bound tier) or "not a task"
      (no origin), and a rule that needs a missing fact does not apply.
    - A decision's `escalation` is carried on an allow too, so a decision is always one shape;
      it is only acted on when `allowed` is False.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md, "Policy is a pure
      function with a reason".
    - hivemind.guard.policy.evaluate for the function these models feed.
    - hivemind.guard.enforcer for the adapter that records a denial on the trail.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.cell import AccessLevel, CombShieldLevel, RequestOrigin
from hivemind.guard.capabilities import Capability, CapabilitySet
from hivemind.guard.policy.points import EnforcementPoint
from waggle.ids import IdKind
from waggle.messages.base import check_id

OPERATOR_ID = "human"  # The operator's principal id: the trail's own literal for the person.
_ROLE_PATTERN = r"^[a-z][a-z_]*$"  # A policy role name: a lowercase `[guard.roles]` key.
_MAX_ROLE_CHARS = 64  # Role names are short keys ("guard_bee"), never prose.
_MAX_RULE_CHARS = 128  # A rule id is a short dotted name ("guard.tier_floor.night_veil").

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = [
    "OPERATOR_ID",
    "EscalationAction",
    "PolicyContext",
    "PolicyDecision",
    "PolicyRequest",
    "PrincipalKind",
    "PrincipalRef",
]


class PrincipalKind(Enum):
    """Who can act in the Hive (roadmap step 10.4); each kind holds a set from its own root."""

    OPERATOR = "operator"  # The human: the root of every set, and the only holder of some families.
    QUEEN = "queen"  # The orchestrator: the widest set any bee holds.
    WARDEN = "warden"  # A per-Cell supervisor; its set is narrowed by its Cell's access level.
    WORKER = "worker"  # A sub-bee doing one task in one role.
    SWARM_DEVICE = "swarm_device"  # A Real Cell enrolled through a Pollen Packet (phase 11).
    CLIENT_DEVICE = "client_device"  # A phone, laptop or program enrolled at the Entrance.


class EscalationAction(Enum):
    """What happens after a denial at one enforcement point (`[guard.escalation]`)."""

    REFUSE = "refuse"  # Return the refusal to the bee and nothing more: the default everywhere.
    ALARM = "alarm"  # Also raise an Alarm to the principal's supervisor.
    ASK_HUMAN = "ask_human"  # Also put the question to the human through the inbox.


# The id kind each principal kind acts as; the operator is absent because it has no minted id.
_ID_KINDS: Mapping[PrincipalKind, IdKind] = MappingProxyType(
    {
        PrincipalKind.QUEEN: IdKind.HIVE,
        PrincipalKind.WARDEN: IdKind.WARDEN,
        PrincipalKind.WORKER: IdKind.WORKER,
        PrincipalKind.SWARM_DEVICE: IdKind.DEVICE,
        PrincipalKind.CLIENT_DEVICE: IdKind.DEVICE,
    }
)


class PrincipalRef(BaseModel):
    """The principal an action is attempted by: its kind, its own id and its policy role."""

    model_config = _MODEL_CONFIG

    kind: PrincipalKind = Field(description="Which kind of principal is acting.")
    id: str = Field(
        description="The principal's own id: a hive_ id for the Queen, a warden_ or worker_ id, "
        "a device_ id for either kind of device, or 'human' for the operator."
    )
    role: str = Field(
        max_length=_MAX_ROLE_CHARS,
        pattern=_ROLE_PATTERN,
        description="The [guard.roles] table its default set came from ('drone', 'warden', "
        "'device', ...); recorded on a denial so the trail says which default was too narrow.",
    )

    @model_validator(mode="after")
    def _id_matches_kind(self) -> PrincipalRef:
        """Refuse an id that is not the kind of id this principal kind acts as."""
        id_kind = _ID_KINDS.get(self.kind)
        # Only the operator has no id kind; it is always the trail's own literal for the human.
        if id_kind is None:
            if self.id != OPERATOR_ID:
                raise ValueError(f"the operator's principal id is {OPERATOR_ID!r}, not {self.id!r}")
            return self
        check_id(self.id, id_kind)
        return self


class PolicyContext(BaseModel):
    """The facts about where an action happens that the tier floors and access rule read."""

    model_config = _MODEL_CONFIG

    comb_shield: CombShieldLevel | None = Field(
        default=None, description="The Cell's own Comb Shield tier; None when not on a Cell."
    )
    access_level: AccessLevel | None = Field(
        default=None,
        description="The Cell's access level; None when not on a Cell, so no access-level "
        "ceiling applies (a Virtual Cell is always FULL).",
    )
    bound_tier: CombShieldLevel | None = Field(
        default=None,
        description="The tier the task is bound to after placement, or the tier it requested "
        "before it is placed; None when the action is not a task's.",
    )
    origin: RequestOrigin | None = Field(
        default=None,
        description="Who asked for the task to exist (HUMAN, QUEEN, WARDEN); None when the "
        "action is not a task's.",
    )


class PolicyRequest(BaseModel):
    """One question for the policy engine: may this principal do this, here, now?"""

    model_config = _MODEL_CONFIG

    principal: PrincipalRef = Field(description="Who is attempting the action.")
    point: EnforcementPoint = Field(description="Where the action is being authorised.")
    needed: Capability = Field(description="The one capability the action requires.")
    held: CapabilitySet = Field(description="Everything the principal currently holds.")
    context: PolicyContext = Field(
        default_factory=PolicyContext,
        description="Where the action happens; empty when it is not on a Cell nor a task's.",
    )


class PolicyDecision(BaseModel):
    """The policy engine's answer: allowed or not, which rule decided, why, and what follows."""

    model_config = _MODEL_CONFIG

    allowed: bool = Field(description="True when the action may proceed.")
    rule: str = Field(
        min_length=1,
        max_length=_MAX_RULE_CHARS,
        description="The stable id of the rule that decided: 'guard.held', 'guard.not_held', "
        "'guard.deny_list', 'guard.access_level.<level>' or 'guard.tier_floor.<floor>'.",
    )
    reason: str = Field(
        min_length=1,
        description="A full sentence naming the principal, the point and the capability.",
    )
    escalation: EscalationAction = Field(
        description="What a denial at this point escalates to (the policy's per-point table, "
        "REFUSE by default); carried on an allow too, and only acted on when allowed is False.",
    )
