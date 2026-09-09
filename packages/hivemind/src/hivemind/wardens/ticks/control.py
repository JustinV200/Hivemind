"""Forward TaskCancel, TaskPause, TaskResume and Intervene from the Queen to the right sub-bee.

Roadmap step 3.19's own dispatch map: "TaskCancel/TaskPause/TaskResume/Intervene from the Queen ->
FORWARD_CONTROL to the sub-bee running that task." `forward_control` is that whole rule: every one
of the four is relayed unchanged, over the sub-bee's own link, with a fresh envelope addressed by
this Warden -- none of them carries a reply the Warden itself needs to wait for or interpret.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why). Calls into waggle only.

Key invariants:
    - `forward_control` is a no-op, not an error, when `sub_bee` is None (the Queen named a task
      this Warden no longer has a sub-bee running, e.g. it already finished): a stale control
      message from the Queen is not this module's contract to enforce.

See Also:
    - .claude/roadmap.md step 3.19's own dispatch map for the FORWARD_CONTROL rule.
    - waggle.messages.task for TaskCancel, TaskPause and TaskResume.
    - waggle.messages.supervision for Intervene.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from waggle.envelope import Hop, wrap
from waggle.messages.supervision import Intervene
from waggle.messages.task import TaskCancel, TaskPause, TaskResume

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

__all__ = ["forward_control"]

_Control = TaskCancel | TaskPause | TaskResume | Intervene


async def forward_control(warden: Warden, sub_bee: SubBee | None, payload: _Control) -> None:
    """Relay `payload` unchanged to `sub_bee`'s own link, addressed by this Warden.

    Args:
        warden: The owning Warden (read directly; see the module docstring).
        sub_bee: The sub-bee `payload` concerns; a no-op when None (module docstring).
        payload: The control message to relay: TaskCancel, TaskPause, TaskResume or Intervene.
    """
    if sub_bee is None:
        return
    hop = Hop(
        sender=warden._warden_id, recipient=sub_bee.worker_id, node_id=warden._deps.identity.node_id
    )
    await sub_bee.link.send(wrap(payload, hop, clock=warden._deps.clock))
