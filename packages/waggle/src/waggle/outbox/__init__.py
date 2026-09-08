"""Provide the outbox: the durable queue of envelopes a node could not send, and its replay.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). When a
node's link is down, what it cannot send waits on disk until the link is back; this package is
that queue. ``queue`` holds ``Outbox``, the first-in-first-out queue of unsigned frames with its
append, ack and pending operations; ``log`` the append-only JSONL file underneath it, its two
record shapes, the torn-line rule and compaction; ``replay`` the drain through a transport after
reconnection and the freshness expiry that precedes it. This package is the face: a caller
imports ``Outbox`` and ``replay_outbox`` from here; the log's record functions are the queue's
own business and stay in their module.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Written by every bee loop that fails a send and
    drained by the same loop right after a reconnect; calls into waggle.codec, waggle.envelope,
    waggle.ids, waggle.transport.base and the injected Clock.

Key invariants:
    - Frames are stored unsigned and signed at send time, so a key rotated between a crash and
      its replay still yields valid signatures and a stolen outbox file carries nothing a peer
      would accept.
    - This file holds re-exports and __all__ only.

See Also:
    - waggle.outbox.queue, waggle.outbox.log and waggle.outbox.replay for the definitions.
    - docs/waggle/spec.md section 10 and docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md
      for the decisions that shaped this package.

Public API:
    - Outbox (queue): the durable first-in-first-out queue of unsent envelopes.
    - replay_outbox, ReplayReport, expire_older_than (replay): drain the queue through a
      transport after reconnection, and expire what waited too long first.
"""

from waggle.outbox.queue import Outbox
from waggle.outbox.replay import ReplayReport, expire_older_than, replay_outbox

__all__ = ["Outbox", "ReplayReport", "expire_older_than", "replay_outbox"]
