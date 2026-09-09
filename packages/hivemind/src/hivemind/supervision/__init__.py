"""Provide the one Supervisor protocol used at every level: human, Queen, Wardens, sub-bees.

Supervision is the Hive's kernel shape (codingrules section 8.8, README "Core concepts" 1 and 2):
autopilot handles every event by a deterministic dispatch table first, and only what autopilot
cannot decide runs an awake episode; an issue a bee cannot resolve becomes a typed `Alarm` climbing
the tree through the same `EscalationPolicy` shape at every level, with the human always last; and
every supervisor triages its inbox with the same deterministic `Attendant`. This package defines
that shared surface -- `Supervisor`, `Alarm` and its state machine, `ContextTelemetry`'s helpers,
the six `Intervention` levers, `EscalationPolicy` and `Attendant` -- once, so the Queen and every
Warden are built on the same shape rather than each reinventing it. It never imports
`hivemind.llm`: both autopilot packages (`hivemind.queen.autopilot`, `hivemind.wardens.autopilot`)
import this package, and `lint-imports` forbids any path from either into `hivemind.llm`, so
anything model-shaped this package or its callers need (`ModelSlot`, the `TieBreaker` protocol) is
an injected value or a Protocol, never a call to a provider.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by `hivemind.queen` and
    `hivemind.wardens` (both of which implement `Supervisor`) and by `hivemind.workers` (which
    raises `Alarm`s). Calls into `hivemind.common`, `hivemind.cell` (for `HoneyClearance`),
    `hivemind.forage` (for `ModelSlot`) and waggle only.

Key invariants:
    - Nothing in this package imports hivemind.llm, directly or transitively
      (tests/unit/supervision and lint-imports both check it).
    - AlarmKind and AlarmSeverity mirror their waggle.messages.supervision/.labels counterparts
      member for member; ContextTelemetry and AlarmContext are the waggle value models themselves,
      not mirrors (codingrules section 6.1).
    - Alarm.state only ever moves along hivemind.supervision.alarm.TRANSITIONS' edges.
    - Attendant.order is deterministic given its WeightTable, Clock and (if set) TieBreaker.

See Also:
    - .claude/codingrules.md section 8.8 for the kernel/Warden/Attendant/Alarm/Intervention shape
      this package implements.
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies and the
      "supervision must never import hivemind.llm" corollary.
    - docs/adr/0011-kernel-shape-autopilot-then-awake.md,
      docs/adr/0012-wardens-alarms-and-the-escalation-chain.md and
      docs/adr/0013-attendant-for-every-supervisor.md for the decisions this package implements.
    - README.md "Core concepts" 1 (the Queen) and 2 (Wardens).
    - hivemind.supervision.README for the module-by-module map of this package.

Public API:
    - SupervisionError, InvalidAlarmTransitionError, PolicyError, UnknownChildError: this
      subsystem's error tree (errors).
    - ChildKind, ChildRef, Supervisor: the one supervision protocol (supervisor).
    - Alarm, AlarmKind, AlarmSeverity, AlarmState, TRANSITIONS, can_transition, assert_transition:
      the mirrored kinds, the model, and its state machine (alarm).
    - ContextTelemetry, fraction_used, is_past_threshold, summarise: waggle's own telemetry value
      model, re-exported, plus the pure helpers over it (telemetry).
    - Compact, Checkpoint, Handoff, Rebind, Takeover, Cancel, Intervention, to_wire, from_wire: the
      six intervention levers and their wire conversion (intervention).
    - PolicyAction, PolicyRule, EscalationPolicy, load_policy, decide: policy as data (policy).
    - InboxKind, InboxItem, WeightTable, Priority, TieBreaker, Attendant, score_item: one
      supervisor's inbox triage (attendant).
    - FakeSupervisor: a scripted Supervisor for tests (fake).
"""

from hivemind.supervision.alarm import (
    TRANSITIONS,
    Alarm,
    AlarmKind,
    AlarmSeverity,
    AlarmState,
    assert_transition,
    can_transition,
)
from hivemind.supervision.attendant import (
    Attendant,
    InboxItem,
    InboxKind,
    Priority,
    TieBreaker,
    WeightTable,
    score_item,
)
from hivemind.supervision.errors import (
    InvalidAlarmTransitionError,
    PolicyError,
    SupervisionError,
    UnknownChildError,
)
from hivemind.supervision.fake import FakeSupervisor
from hivemind.supervision.intervention import (
    Cancel,
    Checkpoint,
    Compact,
    Handoff,
    Intervention,
    Rebind,
    Takeover,
    from_wire,
    to_wire,
)
from hivemind.supervision.policy import (
    EscalationPolicy,
    PolicyAction,
    PolicyRule,
    decide,
    load_policy,
)
from hivemind.supervision.supervisor import ChildKind, ChildRef, Supervisor
from hivemind.supervision.telemetry import (
    ContextTelemetry,
    fraction_used,
    is_past_threshold,
    summarise,
)

__all__ = [
    "TRANSITIONS",
    "Alarm",
    "AlarmKind",
    "AlarmSeverity",
    "AlarmState",
    "Attendant",
    "Cancel",
    "Checkpoint",
    "ChildKind",
    "ChildRef",
    "Compact",
    "ContextTelemetry",
    "EscalationPolicy",
    "FakeSupervisor",
    "Handoff",
    "InboxItem",
    "InboxKind",
    "Intervention",
    "InvalidAlarmTransitionError",
    "PolicyAction",
    "PolicyError",
    "PolicyRule",
    "Priority",
    "Rebind",
    "SupervisionError",
    "Supervisor",
    "Takeover",
    "TieBreaker",
    "UnknownChildError",
    "WeightTable",
    "assert_transition",
    "can_transition",
    "decide",
    "fraction_used",
    "from_wire",
    "is_past_threshold",
    "load_policy",
    "score_item",
    "summarise",
    "to_wire",
]
