"""Carry out one decided WardenAction by handing its item to the tick handler that does it.

A Warden (the per-Cell supervisor) decides every inbox item first by autopilot
(`hivemind.wardens.autopilot.decide`, a deterministic table that never awaits a model) and only
for `NEEDS_JUDGEMENT` by an awake episode; either way it ends with one `WardenAction`. `act` is
the step after that decision: it routes the action and its item to the handler in this package
that does the work -- spawn (`assign`), acceptance (`results`), an Alarm's levers (`alarms`), the
relays between the Queen and a sub-bee (`questions`, `control`, and `honey` for the Honey Store's
traffic, roadmap step 7.8), the routine records (`heartbeat`, `control`) and the Queen's two
orders that end or narrow this Warden (`control`). It lived in `hivemind.wardens.warden` as
`_act`; it moved here, unchanged in behaviour, so that module stays within codingrules 5.1's size
limit as the table grows, the same reason every other tick handler lives in this package.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks sub-package. Called once per decided item by `hivemind.wardens.warden.Warden`'s own
    tick. Calls into this package's `alarms`, `assign`, `control`, `heartbeat`, `honey`,
    `questions` and `results`, `hivemind.wardens.autopilot` (WardenAction),
    `hivemind.supervision.attendant` (InboxItem) and waggle only.

Key invariants:
    - Each action reaches at most one handler, and only with a payload of the type that handler
      takes; an action whose payload does not fit (a stale or misrouted item) does nothing.
    - Nothing here decides anything: every choice was made by the autopilot table or an awake
      episode before `act` is called.

See Also:
    - hivemind.wardens.autopilot for WardenAction and decide, where every action comes from.
    - hivemind.wardens.warden for Warden, whose tick calls `act`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.supervision.attendant import InboxItem
from hivemind.wardens.autopilot import WardenAction
from hivemind.wardens.ticks import alarms, assign, control, heartbeat, honey, questions, results
from waggle.ids import MessageId
from waggle.messages.cell.snapshot import CellRollbackReply, CellSnapshotReply
from waggle.messages.forage import CeilingsSet, GrantIssued, PlanWritten
from waggle.messages.supervision import AlarmRaised, Answer, Heartbeat, Intervene, Question
from waggle.messages.task import (
    TaskAssign,
    TaskCancel,
    TaskPause,
    TaskProgress,
    TaskResult,
    TaskResume,
)

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

# The four ways an Alarm's escalation policy maps onto something a Warden does itself.
_ALARM_ACTIONS = frozenset(
    {WardenAction.RETRY, WardenAction.REBIND, WardenAction.ESCALATE, WardenAction.CANCEL_TASK}
)
# The actions that relay a message between the Queen and a sub-bee rather than act on it.
_RELAY_ACTIONS = frozenset(
    {
        WardenAction.FORWARD_QUESTION,
        WardenAction.FORWARD_ANSWER,
        WardenAction.FORWARD_CONTROL,
        WardenAction.FORWARD_HONEY,
    }
)

__all__ = ["act"]


async def act(
    warden: Warden,
    action: WardenAction,
    item: InboxItem,
    sub_bee: SubBee | None,
    binding: str | None,
) -> None:
    """Carry out one decided WardenAction on `item`.

    Args:
        warden: The owning Warden; every handler reads and writes its state directly.
        action: What autopilot (or an awake episode) decided.
        item: The ordered InboxItem the action is about; its payload is the wire message.
        sub_bee: The sub-bee `item` concerns, when the Warden's tick resolved one.
        binding: The `[llm.slots]` key an awake REBIND chose; None otherwise.
    """
    payload = item.payload
    if action in _RELAY_ACTIONS:
        await _relay(warden, action, item, sub_bee)
    elif action is WardenAction.SPAWN and isinstance(payload, TaskAssign):
        await assign.handle_assign(warden, payload)
    elif action is WardenAction.RECORD:
        await _record_routine(warden, item, payload)
    elif action is WardenAction.ACCEPT and isinstance(payload, TaskResult) and sub_bee is not None:
        await results.handle_accept(warden, sub_bee, payload)
    elif action in _ALARM_ACTIONS and sub_bee is not None and isinstance(payload, AlarmRaised):
        await alarms.handle_alarm_action(warden, sub_bee, payload, action, binding)
    elif action is WardenAction.STOP:
        # ADR-0027 / roadmap step 5.3: the Queen's own Shutdown or CellTeardownRequest. `stop()`
        # already stops every sub-bee, releases the lease and sets the loop's own stop flag, so
        # the Warden's `_run_tick` returns straight after this and `run()` ends on its next check.
        await control.handle_stop(warden, payload)
    elif action is WardenAction.RELEASE_LEASE and isinstance(payload, Intervene):
        # Roadmap step 5.13: the Queen's own narrower order. Unlike STOP, this Warden keeps
        # running afterwards; `assign.settle_after_tick` (still called by the tick) settles it
        # back to WATCH on its own once `_sub_bees` is empty.
        await control.handle_release_lease(warden, payload)


async def _relay(
    warden: Warden, action: WardenAction, item: InboxItem, sub_bee: SubBee | None
) -> None:
    """Relay one message between the Queen and a sub-bee, by the relay the action names."""
    payload = item.payload
    if action is WardenAction.FORWARD_QUESTION and isinstance(payload, Question):
        await questions.forward_question(warden, MessageId(item.id), payload)
    elif action is WardenAction.FORWARD_ANSWER and isinstance(payload, Answer):
        await questions.forward_answer(warden, payload)
    elif action is WardenAction.FORWARD_CONTROL and isinstance(
        payload, TaskCancel | TaskPause | TaskResume | Intervene
    ):
        await control.forward_control(warden, item, sub_bee, payload)
    elif action is WardenAction.FORWARD_HONEY:
        # Roadmap step 7.8: the relay checks the direction and the first-hop identity itself.
        await honey.handle_honey_item(warden, item, sub_bee)


async def _record_routine(warden: Warden, item: InboxItem, payload: object) -> None:
    """Handle a RECORD-only item: a grant, a heartbeat, routine progress, ceilings or a plan."""
    if isinstance(payload, GrantIssued):
        await assign.handle_grant(warden, payload)
    elif isinstance(payload, Heartbeat):
        heartbeat.record_heartbeat(warden, item.principal, payload)
    elif isinstance(payload, TaskProgress):
        heartbeat.record_progress(warden, item.principal, payload)
    elif isinstance(payload, CeilingsSet):
        # Roadmap step 4.8's own wiring step: the Queen's own ceilings never record a fresh trail
        # event here (module docstring of hivemind.queen.forage.ceilings: she already recorded
        # forage.ceilings_set on her own side before sending it).
        control.handle_ceilings_set(warden, payload)
    elif isinstance(payload, PlanWritten):
        # Same reasoning: hivemind.queen.forage.hosting already recorded forage.plan_written.
        control.handle_plan_written(warden, payload)
    elif isinstance(payload, CellSnapshotReply | CellRollbackReply):
        # Roadmap step 5.10's own follow-up gap: resolve this Warden's own RelaySnapshotter,
        # never a trail write of its own (the Capping gate's own proposal handling records
        # whatever it does with the snapshot/rollback outcome).
        control.handle_snapshot_reply(warden, payload)
