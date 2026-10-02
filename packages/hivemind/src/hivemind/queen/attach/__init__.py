"""Admit a remote Warden to the Queen, serve its link, and recognise it when it comes back.

Every Warden the Queen supervises joins her tree through this package, and every remote one (a
Virtual Cell's in-Cell Warden dialling the Cell listener, a colonized device's Warden dialling the
swarm listener) is served by the same code once its gate has proved who it is. `.admit` is the
Queen's own door: the `warden:spawn` Guard check, the bookkeeping, `warden.spawned`, and, for a
Warden she already knows, a reconnection (ADR-0053: a new link replaces the old, `warden.
reconnected`, the dispatcher's hold lifted, and the ceilings, plan and live grants sent again).
`.serve` is what happens after a gate's handshake (the fan-out, the attach, the snapshot relay, the
trail segment receiver and its refusals, the end of the link), `.fanout` the one-reader wrapper it
puts around the connection and `.refusals` what a link refused, recorded for the Guard Bee. They
were the Cell listener's own until phase 11 gave the swarm gate the same need, and a second copy
would drift; the Cell listener keeps its behaviour and its tests by calling the same helper.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called by
    `hivemind.queen.queen.Queen.attach_warden`, `hivemind.queen.cell_gate.listener.CellListener`
    and `hivemind.queen.swarm_gate`. Calls into `hivemind.queen.forage`, `.inbox`, `.isolation`,
    `.ticks` and `.trail` and `hivemind.pheromone`, and reaches into `Queen`'s private tables.

Key invariants:
    - A Warden is attached under one link at a time; a reconnection replaces the old one.
    - A gate's connection is read by one task only: the fan-out's pump (`FanoutTransport`).
    - The end of a link acts only for the Warden's current link (`detach_if_current`).

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for reconnection.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the Cell gate.
    - hivemind.queen.cell_gate and hivemind.queen.swarm_gate for the two callers.

Public API:
    - attach_warden, detach_warden, detach_if_current, current_link, knows_warden, warden_state:
      admit a Warden, remove it, remove it only while a given link is still its own, read its
      link, whether the Queen has had it before, and the state it last reported (admit).
    - serve_warden, ProvedLink, LinkHooks, LinkServices, SnapshotRequestHandler: serve a gate's
      proved connection after its handshake (serve).
    - FanoutTransport, FANOUT_QUEUE_SIZE: the one-reader wrapper around a connection (fanout).
    - LinkRefusals, SegmentRefusal, ENVELOPE_REFUSED_KIND, SEGMENT_REFUSED_KIND,
      SEGMENT_REFUSED_CODE, refusal_error: what a link refused, recorded for the Guard Bee and
      answered to the Warden (refusals).
"""

from hivemind.queen.attach.admit import (
    attach_warden,
    current_link,
    detach_if_current,
    detach_warden,
    knows_warden,
    warden_state,
)
from hivemind.queen.attach.fanout import FANOUT_QUEUE_SIZE, FanoutTransport
from hivemind.queen.attach.refusals import (
    ENVELOPE_REFUSED_KIND,
    SEGMENT_REFUSED_CODE,
    SEGMENT_REFUSED_KIND,
    LinkRefusals,
    SegmentRefusal,
    refusal_error,
)
from hivemind.queen.attach.serve import (
    LinkHooks,
    LinkServices,
    ProvedLink,
    SnapshotRequestHandler,
    serve_warden,
)

__all__ = [
    "ENVELOPE_REFUSED_KIND",
    "FANOUT_QUEUE_SIZE",
    "SEGMENT_REFUSED_CODE",
    "SEGMENT_REFUSED_KIND",
    "FanoutTransport",
    "LinkHooks",
    "LinkRefusals",
    "LinkServices",
    "ProvedLink",
    "SegmentRefusal",
    "SnapshotRequestHandler",
    "attach_warden",
    "current_link",
    "detach_if_current",
    "detach_warden",
    "knows_warden",
    "refusal_error",
    "serve_warden",
    "warden_state",
]
