"""Define decide: the Warden's deterministic dispatch table over one inbox item.

Codingrules section 8.8: "Autopilot returns an action or NEEDS_JUDGEMENT; only the latter runs an
awake episode." `decide` is that table for a Warden: given one `hivemind.supervision.attendant.
InboxItem` (already scored and ordered by the Attendant) and, when the item concerns a sub-bee the
Warden already knows about, a `SubBeeView` of its current `hivemind.workers.state.WorkerState`, it
returns exactly one `hivemind.wardens.autopilot.actions.WardenAction`. The table is keyed by the
wrapped payload's own type: `TaskAssign` and `GrantIssued` both map to a bookkeeping action the
Warden's own tick handler (`hivemind.wardens.ticks.assign`) resolves further (a `TaskAssign` only
actually spawns once a matching grant has arrived; the table itself does not hold that state, only
the sub-bee table does); an `AlarmRaised` is mapped through `hivemind.supervision.policy.decide`,
which reads only the Alarm's `kind` and `attempts`; a `Shutdown` or `CellTeardownRequest` from the
Queen maps to `STOP` (ADR-0027: the one order that ends a Warden, never a judgement call); a
Queen-sent `Intervene(RELEASE_LEASE)` maps to `RELEASE_LEASE` (roadmap step 5.13: the narrower
order that releases the lease but leaves this Warden running, decided ahead of the generic
`Intervene` -> `FORWARD_CONTROL` branch) and a Queen-sent `Intervene(QUARANTINE)` to `QUARANTINE`
(roadmap step 10.6c: carried out here, never relayed to the bee it names); a `PolicyAction.
QUARANTINE` row maps to `QUARANTINE` too, and to `ESCALATE` when the Alarm names no sub-bee this
Warden still supervises; a `CellSnapshotReply`/`CellRollbackReply` (roadmap step 5.10's own
follow-up gap) maps to `RECORD`, resolved by this Warden's own `RelaySnapshotter`; a Queen-sent
`CellTaintOrder` (roadmap step 10.6a) maps to `TAINT_MEMORY`; the Honey Store's traffic (roadmap
step 7.8) -- a `HoneyQuery`, `NectarDeposit` or `HoneyResponse`, and a `control.error` whose
`failed_kind` is `honey.nectar_deposit` -- maps to `FORWARD_HONEY`, which
`hivemind.wardens.ticks.honey` relays or logs; every other recognised kind maps to a fixed
action; anything this table has never seen returns `NEEDS_JUDGEMENT`, the one signal that hands
the item to `hivemind.wardens.awake` instead of silently dropping it.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    autopilot sub-package (which never imports `hivemind.llm`). Called once per ordered inbox item
    by `hivemind.wardens.warden.Warden`'s tick. Calls into `hivemind.supervision` (Alarm, AlarmKind,
    PolicyAction, EscalationPolicy, decide), `hivemind.supervision.attendant` (InboxItem),
    `hivemind.workers.state` (WorkerState) and waggle only.

Key invariants:
    - This module imports no `hivemind.llm`, directly or transitively (codingrules section 4;
      `lint-imports` enforces it for every module under an `autopilot/` directory).
    - `decide` is pure: given the same `(item, sub_bee, policy)`, it always returns the same
      `WardenAction`; it reads nothing beyond its own arguments.
    - Every `PolicyAction` `hivemind.supervision.policy` can return is mapped to exactly one
      `WardenAction` (`_POLICY_ACTION_MAP` is total over the enum); a kind this table does not
      recognise at all is the only path to `NEEDS_JUDGEMENT`.

See Also:
    - .claude/codingrules.md section 8.8 for the autopilot-first-awake-second shape this table
      implements, and for "an issue a bee cannot resolve becomes an Alarm... Each level's
      EscalationPolicy is data".
    - hivemind.wardens.autopilot.actions for WardenAction, this function's return type.
    - hivemind.supervision.policy for PolicyAction and decide, the Alarm-specific half this table
      delegates to.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.supervision import Alarm, EscalationPolicy, PolicyAction
from hivemind.supervision import decide as decide_policy
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens.autopilot.actions import WardenAction
from waggle.messages.cell import CellTaintOrder
from waggle.messages.cell.leases import CellTeardownRequest
from waggle.messages.cell.snapshot import CellRollbackReply, CellSnapshotReply
from waggle.messages.control.protocol import ErrorMessage, Shutdown
from waggle.messages.forage import CeilingsSet, GrantIssued, GrantRevoked, PlanWritten
from waggle.messages.honey import HoneyQuery, HoneyResponse, NectarDeposit
from waggle.messages.registry import kind_for
from waggle.messages.supervision import (
    AlarmRaised,
    Answer,
    Heartbeat,
    Intervene,
    InterventionAction,
    Question,
)
from waggle.messages.task import (
    TaskAssign,
    TaskCancel,
    TaskOutcome,
    TaskPause,
    TaskProgress,
    TaskResult,
    TaskResume,
)

if TYPE_CHECKING:
    # Import-only: hivemind.workers.state.WorkerState is used here purely as a type annotation
    # (never at runtime, e.g. no isinstance check or comparison). Importing it for real would
    # execute hivemind/workers/__init__.py, which needs hivemind.llm.slots.BoundModel for
    # WorkerContext -- exactly the transitive path codingrules section 4 forbids from anything
    # under an autopilot/ directory. TYPE_CHECKING keeps mypy's view of the type while keeping
    # this module's own runtime import graph clear of hivemind.llm (proven by
    # tests/unit/wardens/autopilot/test_no_llm_import.py).
    from hivemind.workers.state import WorkerState

__all__ = ["SubBeeView", "decide"]

# The kind a Queen's control.error names when it refuses a deposit this Warden relayed; read from
# the registry, the one place kinds live, so it can never drift from the wire.
_NECTAR_DEPOSIT_KIND = kind_for(NectarDeposit)

# Every PolicyAction a Warden's own EscalationPolicy can name, mapped onto what a Warden actually
# does about it (roadmap step 3.19's own dispatch map): RESPAWN and RETRY both mean "start this
# sub-bee's task again", which for a Warden is always a fresh WorkerRuntime (the old one's asyncio
# task is already gone by the time an Alarm or a FAILED TaskResult reaches here), so both collapse
# onto WardenAction.RETRY; TAKEOVER has no Warden-level meaning (codingrules section 8.8: "Wardens
# have the same levers minus takeover with the Queen's slot"), so it escalates to the level that
# does hold that lever; QUARANTINE is the Warden's own lever over its own sub-bee (ADR-0043: "a
# Warden may apply it to its own sub-bee by its own policy row"). ISOLATE is the Queen's alone
# (roadmap step 10.6a): a Warden's policy refuses to load such a row (`load_warden_policy`), and
# were one to reach this table anyway it escalates to the level that holds the lever.
_POLICY_ACTION_MAP: Mapping[PolicyAction, WardenAction] = {
    PolicyAction.RETRY: WardenAction.RETRY,
    PolicyAction.RESPAWN: WardenAction.RETRY,
    PolicyAction.REBIND: WardenAction.REBIND,
    PolicyAction.TAKEOVER: WardenAction.ESCALATE,
    PolicyAction.ESCALATE: WardenAction.ESCALATE,
    PolicyAction.CANCEL: WardenAction.CANCEL_TASK,
    PolicyAction.QUARANTINE: WardenAction.QUARANTINE,
    PolicyAction.ISOLATE: WardenAction.ESCALATE,
}


@dataclass(frozen=True, slots=True)
class SubBeeView:
    """The two facts about a sub-bee the dispatch table itself reads: its state and attempt count.

    A deliberately narrow view (codingrules section 8.1's Protocol-at-every-seam spirit, though
    this is a plain value rather than a Protocol since nothing else ever implements it): the full
    mutable `hivemind.wardens.spawn.sub_bee.SubBee` record lives with the Warden's own tick
    handlers, never imported here, so this table stays decoupled from the rest of the spawn
    bookkeeping. `attempt` is what an AlarmRaised's own policy lookup keys on (see `decide`'s own
    docstring for why the wire `attempts` field itself is not what is used).
    """

    state: WorkerState
    attempt: int = 1


def decide(item: InboxItem, sub_bee: SubBeeView | None, policy: EscalationPolicy) -> WardenAction:
    """Return the one WardenAction a Warden's autopilot takes for `item`.

    Args:
        item: One already-scored, already-ordered inbox item; `item.payload` is the underlying
            waggle message (or, for an unrecognised kind, whatever else the Warden's tick wrapped).
        sub_bee: A view of the sub-bee `item` concerns, when the Warden already supervises one;
            None for an item with no sub-bee of its own (a GrantIssued, a Question forwarded from
            a sub-bee the table need not re-identify).
        policy: This Warden's own EscalationPolicy, consulted only for an AlarmRaised.

    Returns:
        Exactly one WardenAction; NEEDS_JUDGEMENT for a payload kind this table does not
        recognise at all.
    """
    payload = item.payload
    if isinstance(payload, TaskAssign):
        return WardenAction.SPAWN
    if isinstance(payload, GrantIssued | GrantRevoked):
        # A revocation (roadmap step 10.6a: the Queen isolating this Cell) is bookkeeping too.
        return WardenAction.RECORD
    if isinstance(payload, TaskResult):
        return _decide_task_result(payload)
    if isinstance(payload, AlarmRaised):
        return _decide_alarm(payload, sub_bee, policy)
    if isinstance(payload, Question):
        return WardenAction.FORWARD_QUESTION
    if isinstance(payload, Answer):
        return WardenAction.FORWARD_ANSWER
    relay_or_record = _decide_relay_or_record(payload)
    if relay_or_record is not None:
        return relay_or_record
    # ADR-0027 / roadmap step 5.3: the Queen's own two ways of ending a Warden. A `Shutdown` is the
    # ordinary "stop now" order; a `CellTeardownRequest` says this Warden's own Cell is about to be
    # destroyed, which for the Warden running *inside* that Cell means exactly the same thing --
    # stop every sub-bee and end the loop. Both used to fall through to NEEDS_JUDGEMENT below,
    # waking a model to decide something that is never a judgement call.
    if isinstance(payload, Shutdown | CellTeardownRequest):
        return WardenAction.STOP
    # A kind this table has never seen: hand off to wardens.awake rather than silently dropping it.
    return WardenAction.NEEDS_JUDGEMENT


def _decide_relay_or_record(payload: object) -> WardenAction | None:
    """Return the WardenAction for a control-lever, RECORD-only or Honey payload; else None.

    Split out of `decide` itself purely to stay under codingrules 5.1's cyclomatic-complexity
    limit as this table has grown more recognised kinds; carries no behaviour of its own beyond
    the isinstance checks decide would otherwise inline.
    """
    if isinstance(payload, Intervene) and payload.action is InterventionAction.RELEASE_LEASE:
        # Roadmap step 5.13: the Queen's own narrower order (stop every sub-bee, release the
        # lease, keep running) -- always addressed to this Warden itself (subject is None), never
        # a lever to relay to a sub-bee, so it is decided before the generic FORWARD_CONTROL branch.
        return WardenAction.RELEASE_LEASE
    if isinstance(payload, Intervene) and payload.action is InterventionAction.QUARANTINE:
        # Roadmap step 10.6c: this Warden carries the quarantine out itself; relayed, the bee it
        # names would only be asked to stop, with nothing checkpointed, killed or tainted.
        return WardenAction.QUARANTINE
    if isinstance(payload, TaskCancel | TaskPause | TaskResume | Intervene):
        return WardenAction.FORWARD_CONTROL
    if isinstance(payload, TaskProgress | Heartbeat | CeilingsSet | PlanWritten):
        return WardenAction.RECORD
    if isinstance(payload, CellSnapshotReply | CellRollbackReply):
        # Roadmap step 5.10's own follow-up gap (the snapshot relay): resolved by this Warden's
        # own RelaySnapshotter, never a judgement call.
        return WardenAction.RECORD
    if isinstance(payload, CellTaintOrder):
        # Roadmap step 10.6a: the Queen isolated this Cell; labelling is never a judgement call.
        return WardenAction.TAINT_MEMORY
    return _decide_honey(payload)


def _decide_honey(payload: object) -> WardenAction | None:
    """Return FORWARD_HONEY for the Honey Store's traffic (roadmap step 7.8); None otherwise."""
    if isinstance(payload, HoneyQuery | NectarDeposit | HoneyResponse):
        # Relayed unchanged between a sub-bee and the Queen; which way is the relay's to check.
        return WardenAction.FORWARD_HONEY
    if isinstance(payload, ErrorMessage) and payload.failed_kind == _NECTAR_DEPOSIT_KIND:
        # The Queen refused a deposit this Warden relayed: logged, never a judgement call. Any
        # other control.error still reaches awake, as before this row existed.
        return WardenAction.FORWARD_HONEY
    return None


def _decide_alarm(
    payload: AlarmRaised, sub_bee: SubBeeView | None, policy: EscalationPolicy
) -> WardenAction:
    """Map an AlarmRaised through the policy table, keyed on this Warden's own attempt count.

    `hivemind.supervision.policy`'s own default-policy.toml is deliberately built so that
    `attempts == 0` (a freshly raised Alarm, "nothing tried yet at this level") always falls
    through to `policy.default`; a Warden's own per-task attempt count (`sub_bee.attempt`, 1 on
    the first run, incremented on every RETRY/REBIND respawn) is what stands in for "how many
    times THIS level has already tried something for this recurring problem" -- the wire
    `AlarmRaised.attempts` field itself only ever says "0 on the raising hop" for a sub-bee's own
    fresh crash, since a respawned sub-bee's next crash is a brand new Alarm, not the same one
    escalated. Falls back to the wire `attempts` when no sub-bee is known (a Warden's own
    self-raised Alarm, such as CELL_UNREACHABLE, carries its own count).
    """
    alarm = Alarm.from_wire(payload)
    attempts = sub_bee.attempt if sub_bee is not None else alarm.attempts
    keyed_alarm = alarm.model_copy(update={"attempts": attempts})
    action = _POLICY_ACTION_MAP[decide_policy(policy, keyed_alarm)]
    # A quarantine acts on one sub-bee this Warden supervises; an Alarm about none (a Warden's own,
    # or a bee already retired) has nobody to quarantine, so the next level up decides instead.
    if action is WardenAction.QUARANTINE and sub_bee is None:
        return WardenAction.ESCALATE
    return action


def _decide_task_result(payload: TaskResult) -> WardenAction:
    """Return ACCEPT for a sub-bee's claim; RECORD for anything else (a Worker may only claim)."""
    # waggle.messages.task.reports.TaskResult's own validator requires checked_by set on any
    # outcome but CLAIMED, and a Worker's own hop always carries checked_by=None -- so a
    # non-CLAIMED TaskResult reaching a Warden's own inbox can only be the Warden's own prior
    # verified result echoed back on the wire (never a fresh claim to act on again).
    if payload.outcome is TaskOutcome.CLAIMED:
        return WardenAction.ACCEPT
    return WardenAction.RECORD
