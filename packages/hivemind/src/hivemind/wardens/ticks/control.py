"""Forward TaskCancel, TaskPause, TaskResume and Intervene from the Queen to the right sub-bee.

Roadmap step 3.19's own dispatch map: "TaskCancel/TaskPause/TaskResume/Intervene from the Queen ->
FORWARD_CONTROL to the sub-bee running that task." `forward_control` is that whole rule for three
of the four: TaskCancel, TaskPause and TaskResume are relayed unchanged, over the sub-bee's own
link, with a fresh envelope addressed by this Warden -- none of them carries a reply the Warden
itself needs to wait for or interpret. A Queen-sent `Intervene(REBIND)` is the one exception (this
dispatch's own fix 3c): relaying it unchanged only ever makes the sub-bee checkpoint and stop
(`hivemind.workers.runtime.loop.WorkerRuntime._handle_intervene`'s own Rebind handling), and
nothing ever spawned a fresh one afterwards, so the goal always finished on the sub-bee's original
binding. `_handle_queen_rebind` is what actually rebinds: it reads the target `[llm.slots]`
manifest key straight off `Intervene.binding` (the Queen already resolved it,
`hivemind.queen.ticks.alarms._fallback_binding_key`, and fills the field before sending) and calls
`hivemind.wardens.ticks.alarms.rebind_sub_bee`, the same retire-and-respawn-on-an-explicit-key
primitive a sub-bee's own locally-decided REBIND uses once it has picked a target.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why). Calls into `hivemind.wardens.ticks.alarms`
    (rebind_sub_bee) and waggle only.

Key invariants:
    - `forward_control` is a no-op, not an error, when `sub_bee` is None (the Queen named a task
      this Warden no longer has a sub-bee running, e.g. it already finished): a stale control
      message from the Queen is not this module's contract to enforce.
    - A Queen-sent `Intervene` reaching this Warden is always from the Queen link (a sub-bee never
      sends one to its own Warden), so no further sender check is needed before treating a REBIND
      action as this module's own rebind path rather than a plain relay.
    - `_handle_queen_rebind` is a no-op, not an error, when `Intervene.binding` is unset: the Queen
      always fills it before sending REBIND (`hivemind.queen.ticks.alarms._rebind` only sends the
      message once it has resolved a fallback key), so an unset field only ever means a peer that
      skipped that step, not something this Warden should guess at.

See Also:
    - .claude/roadmap.md step 3.19's own dispatch map for the FORWARD_CONTROL rule.
    - waggle.messages.task for TaskCancel, TaskPause and TaskResume.
    - waggle.messages.supervision for Intervene and InterventionAction.
    - hivemind.wardens.ticks.alarms for rebind_sub_bee, this module's one rebind primitive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.wardens.ticks.alarms import rebind_sub_bee
from waggle.envelope import Hop, wrap
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.task import TaskCancel, TaskPause, TaskResume

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

__all__ = ["forward_control"]

_Control = TaskCancel | TaskPause | TaskResume | Intervene


async def forward_control(warden: Warden, sub_bee: SubBee | None, payload: _Control) -> None:
    """Relay `payload` to `sub_bee`, or actually carry out a Queen-sent Intervene(REBIND).

    Args:
        warden: The owning Warden (read directly; see the module docstring).
        sub_bee: The sub-bee `payload` concerns; a no-op when None (module docstring).
        payload: The control message to relay: TaskCancel, TaskPause, TaskResume or Intervene.
    """
    if sub_bee is None:
        return
    if isinstance(payload, Intervene) and payload.action is InterventionAction.REBIND:
        await _handle_queen_rebind(warden, sub_bee, payload)
        return
    hop = Hop(
        sender=warden._warden_id, recipient=sub_bee.worker_id, node_id=warden._deps.identity.node_id
    )
    await sub_bee.link.send(wrap(payload, hop, clock=warden._deps.clock))


async def _handle_queen_rebind(warden: Warden, sub_bee: SubBee, intervene: Intervene) -> None:
    """Turn the Queen's own Intervene(REBIND) into a real rebind respawn (module docstring)."""
    if intervene.binding is None:
        return  # Defensive: the Queen always fills this before sending REBIND (module docstring).
    await rebind_sub_bee(warden, sub_bee, intervene.binding)
