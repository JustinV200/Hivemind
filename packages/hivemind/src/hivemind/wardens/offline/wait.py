"""Tell the Queen what a held task is waiting for, in a request that waits in the outbox.

A Warden (the always-on supervisor of one Cell, a unit of compute) that is cut off from the Queen
(the Hive's central orchestrator) holds the tasks whose sub-bees need a hosted model: they stop at a
Handoff and wait for the link (`freeze`). ADR-0053 says more of such a task than that it waits: "a
subtask whose tempo needs a shared source files a `ForageRequest`, which waits in the outbox; it
does not fail, and it is answered after reconnection". This module is that filing. A BINDING request
(the kind that asks for a higher-grade binding of a slot) names the slot the task runs on and the
grade its tempo asks of a model, and goes out over the Warden's own Queen link, whose offline table
queues `forage.request` with the results and Alarms. The Queen hears it, in order, once the link is
back, and answers it then; the Warden never waits for the answer, because the task is already held
and is started again when the link returns, whatever she says.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline sub-package. Called by `hivemind.wardens.offline.freeze` once for each task it holds
    because the link went. Calls into `hivemind.forage` (Tempo, grade_floor),
    `hivemind.wardens.requests` (the ForageRequest builder), `hivemind.wardens.links` and waggle
    only.

Key invariants:
    - The request is sent and forgotten: a link that has already gone is logged by `send_guarded`
      and never raised, and the task stays held either way.
    - It asks for nothing a grant could change (no seats, no spend, no sub-bees): the slot and the
      grade only say what the task would need, so a Warden cannot grow itself by filing one.
    - The reason carries a slot and a grade, never a model name or a word the task's bee wrote.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for "what keeps running".
    - hivemind.wardens.offline.policy for the table that queues `forage.request`.
    - hivemind.wardens.spawn.route for the other ForageRequest a Warden files (a starved seat).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.forage import Tempo, grade_floor
from hivemind.wardens.links import send_guarded
from hivemind.wardens.requests import ForageRequestInputs, forage_request
from waggle.envelope import wrap
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.forage import ForageDelta, ForageRequestKind

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

__all__ = ["ask_for_binding"]


async def ask_for_binding(warden: Warden, sub_bee: SubBee) -> None:
    """File a BINDING ForageRequest for the task `sub_bee` was running, which is now held.

    Args:
        warden: The cut-off Warden (its Queen link and clock are read).
        sub_bee: The bee whose task is held because its binding needs a hosted model.
    """
    deps = warden._deps
    assignment = sub_bee.assignment
    # The grade comes from the task's own tempo, the same floor the Fanner spills a call by, so the
    # Queen reads the bar the task set and not the one the manifest happens to give its slot.
    grade = grade_floor(Tempo.from_wire(assignment.tempo).accuracy)
    reason = (
        f"Held while cut off from the Queen: the task's tempo asks a {assignment.slot} binding for "
        f"a model of grade {grade} or better, and the binding it ran on needs a hosted source."
    )
    request = forage_request(
        ForageRequestInputs(
            grant_id=assignment.grant_id,
            kind=ForageRequestKind.BINDING,
            wanted=ForageDelta(seats=0, source_id=None, slot=assignment.slot, minimum_grade=grade),
            task_id=assignment.task_id,
            tempo=assignment.tempo,
        ),
        reason[:MAX_REASON_CHARS],
    )
    # Queued by the link's offline table when it is down, sent at once when it is not (a Warden
    # that holds a task on a link that is up has nothing to wait for, so this does not happen).
    await send_guarded(deps.queen_link, wrap(request, deps.hop, clock=deps.clock))
