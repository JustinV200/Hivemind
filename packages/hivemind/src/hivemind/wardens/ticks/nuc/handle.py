"""Handle a Queen-sent swarm.nuc_promote: accept it from the Queen and start the job that does it.

Starting a model server takes seconds and stopping one waits on a process, and codingrules 8.8 says
nothing awaits inside a tick, so the tick only checks who is asking and hands the order to a job
the Warden owns (``Warden.stop`` reaps it). The check is the rule that the Queen alone promotes a
device: an order that did not arrive on the Warden's own Queen link is dropped and logged, never
answered, because nobody with the right to ask is waiting for the answer. Everything else about
the order (the Cell it names, the server, the ceilings) is judged by the job, which replies.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.nuc sub-package. Called by ``hivemind.wardens.ticks.dispatch.act`` for
    ``WardenAction.NUC_PROMOTE``. Calls into ``run``, ``hivemind.wardens.links`` and
    ``hivemind.wardens.ticks.basket`` (``start_job``) only.

Key invariants:
    - Only an order from the Queen's link starts a job; any other is dropped.
    - Nothing here awaits: the job runs after the tick returns.
    - Every started job is in ``warden._tool_jobs``, so it is reaped before the lease is released.

See Also:
    - hivemind.wardens.ticks.nuc.run for the job.
    - hivemind.wardens.autopilot.table for the row that routes an order here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens.links import QUEEN_LINK
from hivemind.wardens.ticks.basket.jobs import start_job
from hivemind.wardens.ticks.nuc.run import run_order
from waggle.ids import MessageId
from waggle.messages.swarm import NucPromote

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

log = get_logger(__name__)

__all__ = ["handle_nuc_promote"]


def handle_nuc_promote(warden: Warden, item: InboxItem, order: NucPromote) -> None:
    """Start the job that carries out ``order``, when it came from the Queen.

    Args:
        warden: The owning Warden, whose job set holds the job.
        item: The inbox item the order arrived as; its id is what the reply is correlated to.
        order: The Queen's order.
    """
    if item.principal != QUEEN_LINK:
        # Only the Queen promotes a device; nothing else may start a server on a borrowed machine.
        log.warning("warden.nuc_order_ignored", principal=item.principal)
        return
    start_job(warden, run_order(warden, MessageId(item.id), order))
