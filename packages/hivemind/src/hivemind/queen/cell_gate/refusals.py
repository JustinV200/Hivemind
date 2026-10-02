"""Run the Virtual Cell listener's transport defences, and keep its link refusals where they were.

Roadmap step 10.6 has the Guard Bee (the Hive's security watcher, reading the Queen's trail) watch
for node integrity failures, and two of them are recorded about a remote Warden's attached link: a
frame whose signature fails (`guard.envelope_refused`) and a trail segment the Queen would not
merge (`guard.segment_refused`). From phase 11 the Queen's other gate, the swarm gate, attaches
colonized devices' Wardens through the very same code, so that recording moved with it to
`hivemind.queen.attach.refusals` (`LinkRefusals`, `SegmentRefusal`) and is re-exported from here,
where the Cell gate's importers have always found it.

What stays here is the Cell listener's own: the transport has two limits of its own (ADR-0055,
roadmap steps 11.3a and 11.3b), switched on for this listener as they are for the swarm's. A
connection that sends frames faster than its ceiling is closed with code 4029, and an envelope
whose id was already seen inside the replay window is dropped before anything handles it. Waggle
writes no trail, so `transport_refusals` builds the callback that records each as
`guard.frame_ceiling` or `guard.replay_refused` with the node id, the same record the swarm
listener's `TransportRefusals` writes (and is reused for it here, so the two listeners cannot
drift apart).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.cell_gate`.
    Built by `.listener`: `transport_refusals` once per listener. Calls into `hivemind.cell`
    (CellIdentity), `hivemind.pheromone` (TrailRecorder), `hivemind.queen.attach` (the link
    refusals it re-exports), `hivemind.swarm` (the swarm listener's transport refusals) and waggle
    (clock, transport limits) only.

Key invariants:
    - A flood is always recorded. A repeated envelope id is recorded too, except one shortly after
      a Cell's Warden reconnected: its outbox replay is expected and silent (ADR-0055).
    - Nothing is recorded without a recorder: a listener built without one refuses the same way.

See Also:
    - hivemind.queen.attach.refusals for LinkRefusals, the per-link record this re-exports.
    - hivemind.queen.cell_gate.listener for the listener these options are built for.
    - hivemind/workers/roles/guard_bee/rules.toml for the rules that count these events.
"""

from __future__ import annotations

from hivemind.cell import CellIdentity
from hivemind.pheromone import TrailRecorder
from hivemind.queen.attach import (
    ENVELOPE_REFUSED_KIND,
    SEGMENT_REFUSED_CODE,
    SEGMENT_REFUSED_KIND,
    LinkRefusals,
    SegmentRefusal,
    refusal_error,
)
from hivemind.swarm import SwarmRecorder
from hivemind.swarm.gateway import GatewayHub, TransportRefusals
from waggle.clock import Clock
from waggle.transport.limits import RefusalHandler, ServerLimits
from waggle.transport.websocket_server import ServerOptions

__all__ = [
    "ENVELOPE_REFUSED_KIND",
    "SEGMENT_REFUSED_CODE",
    "SEGMENT_REFUSED_KIND",
    "LinkRefusals",
    "SegmentRefusal",
    "refusal_error",
    "transport_options",
    "transport_refusals",
]


def transport_options(
    recorder: TrailRecorder | None, clock: Clock, attached: GatewayHub | None = None
) -> ServerOptions:
    """Build the options the Cell listener's server runs with: the limits and their record.

    Args:
        recorder: The Queen's trail and identity; None records nothing.
        clock: The injected clock the limits read time from.
        attached: Remembers when each Cell's node last reconnected (`transport_refusals`).

    Returns:
        The frame ceiling and replay window switched on at their defaults (ADR-0055), the callback
        that records each refusal, and `clock`.
    """
    refusals = transport_refusals(recorder, attached)
    return {"limits": ServerLimits(), "on_refusal": refusals, "clock": clock}


def transport_refusals(
    recorder: TrailRecorder | None, attached: GatewayHub | None = None
) -> RefusalHandler | None:
    """Build the callback that records what this listener's frame ceiling and replay window refuse.

    Args:
        recorder: The Queen's trail and identity; None records nothing, and the limits then
            refuse in silence.
        attached: Where a reconnected Cell's node was noted (`GatewayHub.note_attach`): a duplicate
            envelope id shortly after is its outbox replaying (ADR-0055) and is dropped silently.
            None gives an empty hub, which has seen no reconnection, so every repeated id counts
            as the refusal it is.

    Returns:
        The handler to give the listener's `WebSocketServer` as `on_refusal`, or None.
    """
    if recorder is None:
        return None
    identity = CellIdentity(hive_id=recorder.hive_id, node_id=recorder.node_id, actor="system")
    swarm_recorder = SwarmRecorder(recorder.trail, recorder.clock, identity)
    # The swarm's handler stays silent for a duplicate shortly after a link attached (an outbox
    # replay after a reconnection), reading when links attached from a hub; the same handler
    # serves this listener, so the two cannot drift apart.
    hub = attached if attached is not None else GatewayHub(recorder.clock)
    return TransportRefusals(swarm_recorder, hub, recorder.hive_id)
