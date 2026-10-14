"""Start and stop a device's model server on the Queen's order: the Warden's side of a Nuc.

A colonized device becomes a Nuc (Level 2 of the runtime ladder, ADR-0052) when its Warden runs a
model server on it, so its sub-bees keep thinking when the link to the Queen (the Hive's central
orchestrator) is lost. The Queen decides and orders it with ``swarm.nuc_promote``; this package is
what the Warden does about it. ``handle`` is the tick handler that accepts the order from the
Queen's link and starts a job (a server takes seconds to turn healthy, and no tick waits); ``run``
is that job, which starts or stops the server through ``ModelServerHost`` within the ceilings the
Queen set and fails closed without them; ``reply`` builds and sends ``swarm.nuc_promoted``, the
answer she is waiting for. A package because the order, the job and the reply are three concepts
and the ticks directory is at its codingrules 5.6 fan-out limit.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks sub-package. Called by ``hivemind.wardens.ticks.dispatch.act`` for
    ``WardenAction.NUC_PROMOTE``. Calls into ``hivemind.wardens.local_pool.hosting``,
    ``hivemind.wardens.ticks.lease``, ``hivemind.wardens.ticks.basket`` and waggle only.

Key invariants:
    - Only the Queen's link may promote or demote a device.
    - A server starts only within ceilings the Queen has set, and only one the device's own
      manifest declares.
    - Every order is answered once, with the rung the device is on afterwards.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder.
    - hivemind.queen.swarm_gate.colonize.nuc for the Queen's side of the order.

Public API:
    - handle_nuc_promote: the tick handler (handle).
    - run_order, live_host: the job and the host it works through (run).
    - promoted, demoted, failed, send_reply: the reply (reply).
"""

from hivemind.wardens.ticks.nuc.handle import handle_nuc_promote
from hivemind.wardens.ticks.nuc.reply import demoted, failed, promoted, send_reply
from hivemind.wardens.ticks.nuc.run import live_host, run_order

__all__ = [
    "demoted",
    "failed",
    "handle_nuc_promote",
    "live_host",
    "promoted",
    "run_order",
    "send_reply",
]
