"""Name what is unusual about a test frame: a forged sender node, a stale clock, a fixed id.

Most frames a test puts on the wire are plain: from the Queen (or the Warden), signed by the Hive
node, addressed to the device, stamped now, with a fresh id. The tests of the Pollen Packet's
admission rules need the opposite, one oddity at a time: a frame that names another signing node,
is addressed to someone else, claims to be sent a week ago or ten minutes from now, answers a
request, or reuses an id. Both the scripted Queen and the frame builder take those oddities as one
value rather than as a long list of keyword arguments.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by queen_double.py and states.py; depends on
    the standard library only.

Key invariants:
    - A field left None means "as a plain frame would have it".
    - The value is frozen: a test reuses one for several frames without changing it.

See Also:
    - queen_double.py and states.py, which apply it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["Overrides"]


@dataclass(frozen=True, slots=True)
class Overrides:
    """The parts of a test frame that differ from the plain one the Queen sends the device."""

    node_id: str | None = None  # The signing node it names; the Hive node by default.
    recipient: str | None = None  # Who it is addressed to; the device by default.
    sent_at: datetime | None = None  # When it claims to have been sent; now by default.
    correlation_id: str | None = None  # The request it answers, for a reply.
    message_id: str | None = None  # The envelope id to use instead of a fresh one.
