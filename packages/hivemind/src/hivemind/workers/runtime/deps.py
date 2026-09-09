"""Define RuntimeDeps: the transport-side collaborators a WorkerRuntime is built with.

`hivemind.workers.runtime.loop.WorkerRuntime.__init__` takes a `WorkerContext`, a `Worker` and
this one bundle, matching codingrules 5.1's parameter limit exactly the way
`hivemind.memory.context.MemoryContext` bundles a writer's collaborators. `hop` fixes this
Worker's own address as `sender` and its Warden's as `recipient` for every envelope
`hivemind.workers.runtime.mailbox.Mailbox` sends; `transport` is the one end of a
`waggle.transport.base.Transport` pair this Worker owns (a `waggle.transport.memory.MemoryTransport`
end in phase 3, since every bee runs in the Queen's own process).

Fits into the Hive:
    Layer 4 (roles that do the work). Built by `hivemind.wardens.spawn` (roadmap step 3.19) once
    per Worker it starts, alongside the `WorkerContext` it constructs for the same Worker. Calls
    into waggle only.

Key invariants:
    - `heartbeat_interval_s` is strictly positive: zero would size `hivemind.workers.runtime.
      mailbox.Mailbox`'s own heartbeat wait to nothing and spin the tick loop.

See Also:
    - .claude/codingrules.md section 5.1 for the parameter-count limit this bundle exists to keep.
    - waggle.envelope for Hop, the addressing this bundle carries.
    - waggle.transport.base for Transport, the protocol `transport` satisfies.
    - hivemind.workers.runtime.loop for WorkerRuntime, this bundle's one consumer.
"""

from __future__ import annotations

from dataclasses import dataclass

from waggle.clock import Clock
from waggle.envelope import Hop
from waggle.transport.base import Transport

__all__ = ["RuntimeDeps"]


@dataclass(frozen=True, slots=True)
class RuntimeDeps:
    """The transport, addressing, heartbeat cadence and clock one WorkerRuntime is built with.

    Attributes:
        transport: This Worker's own end of its link to its Warden.
        hop: This Worker's own address (`sender`) and its Warden's (`recipient`), plus the
            sending node's id, for every envelope this runtime wraps and sends.
        heartbeat_interval_s: Seconds between one Heartbeat and the next; strictly positive.
        clock: Injected time source for every id minted, every timestamp written and every sleep
            (the heartbeat cadence and a TaskCancel's grace period).
    """

    transport: Transport
    hop: Hop
    heartbeat_interval_s: float
    clock: Clock
