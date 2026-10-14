"""Provide the sender's half of the replay story: remember what was sent, and send it again.

Waggle (the Hive's bee-to-bee wire protocol, named after the honeybee waggle dance) deduplicates at
the receiver (``waggle.transport.replay``) so that a sender may safely send a frame twice. This
package is what lets a sender do so for the frames a dead-but-not-yet-noticed connection swallowed.
``ring`` holds ``RecentFrames``, the bounded in-memory record of what a node sent in the last
``UNNOTICED_S`` that must arrive, and the constants its defaults derive from. ``remembering`` holds
``RememberingTransport``, a ``Transport`` wrapper that fills a ring after every send that returned
and sends the frames due again, in order and under the same ids, when a connection is made
again. This package is the face: a caller imports the names below from here.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package's transport sub-package. Used by the Warden's
    reconnecting link and the Pollen Packet's courier; calls into waggle.codec, waggle.envelope,
    waggle.transport.base, waggle.transport.replay and waggle.transport.websocket only.

Key invariants:
    - A frame is sent again under the id it was first sent with, never in a new envelope, so the
      receiver's replay window recognises what it already had.
    - The memory is bounded by age, count and bytes; nothing grows with the rate a peer sends.
    - This file holds re-exports and __all__ only.

See Also:
    - docs/waggle/spec.md section 10 and docs/waggle/gateway-contract.md section 14.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the decision.

Public API:
    - RecentFrames, RecentLimits (ring): the bounded memory and its bounds.
    - RememberingTransport, Wanted, every_frame (remembering): the Transport wrapper that fills
      it, the test that picks what to remember and the test that accepts everything.
    - UNNOTICED_S, RESEND_HORIZON_S, RESEND_MARGIN_S, MAX_RECENT_FRAMES, MAX_RECENT_BYTES,
      RESEND_TIMEOUT_S: the commented constants behind the defaults.
"""

from waggle.transport.recent.remembering import (
    RESEND_TIMEOUT_S,
    RememberingTransport,
    Wanted,
    every_frame,
)
from waggle.transport.recent.ring import (
    MAX_RECENT_BYTES,
    MAX_RECENT_FRAMES,
    RESEND_HORIZON_S,
    RESEND_MARGIN_S,
    UNNOTICED_S,
    RecentFrames,
    RecentLimits,
)

__all__ = [
    "MAX_RECENT_BYTES",
    "MAX_RECENT_FRAMES",
    "RESEND_HORIZON_S",
    "RESEND_MARGIN_S",
    "RESEND_TIMEOUT_S",
    "UNNOTICED_S",
    "RecentFrames",
    "RecentLimits",
    "RememberingTransport",
    "Wanted",
    "every_frame",
]
