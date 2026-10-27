"""Decide what the Cell gate trusts on a Virtual Cell's link, and record what it refuses.

Three pieces of the Cell gate (the Queen's listener every Virtual Cell's Warden dials, roadmap
step 5.6) answer one question, whether a frame or a segment on that link is the Cell's own: the
handshake's verifier and the record of a handshake that failed (`handshake`), the transport's own
defences and the link refusals recorded for the Guard Bee (`refusals`), and the key a signed trail
segment is checked against (`segment_keys`). They sit together here so the gate's directory stays
within codingrules 5.6's module limit.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.cell_gate`.
    Used by `hivemind.queen.cell_gate.listener` and the composition root; calls into the modules
    each piece names.

Key invariants:
    - A frame's signature is always checked against the key registered for the Cell it claims, or
      the key its node already proved, never a key the frame carries (`handshake`).
    - Nothing a link refused is recorded about a Cell its link did not prove (`handshake`,
      `refusals`).

See Also:
    - hivemind.queen.cell_gate for the gate's public face, which re-exports these names.

Public API:
    - GateVerifier, HandshakeClaim, HandshakeRefusals, HANDSHAKE_REFUSED_KIND,
      HANDSHAKE_RECORDS_PER_WINDOW, HANDSHAKE_WINDOW_S (handshake).
    - transport_options, transport_refusals, and the link refusals re-exported from
      `hivemind.queen.attach` (refusals).
    - GateSegmentKeys (segment_keys).
"""

from hivemind.queen.cell_gate.trust.handshake import (
    HANDSHAKE_RECORDS_PER_WINDOW,
    HANDSHAKE_REFUSED_KIND,
    HANDSHAKE_WINDOW_S,
    GateVerifier,
    HandshakeClaim,
    HandshakeRefusals,
)
from hivemind.queen.cell_gate.trust.refusals import (
    ENVELOPE_REFUSED_KIND,
    RELAY_REFUSED_KIND,
    SEGMENT_REFUSED_CODE,
    SEGMENT_REFUSED_KIND,
    LinkRefusals,
    SegmentRefusal,
    refusal_error,
    transport_options,
    transport_refusals,
)
from hivemind.queen.cell_gate.trust.segment_keys import GateSegmentKeys

__all__ = [
    "ENVELOPE_REFUSED_KIND",
    "HANDSHAKE_RECORDS_PER_WINDOW",
    "HANDSHAKE_REFUSED_KIND",
    "HANDSHAKE_WINDOW_S",
    "RELAY_REFUSED_KIND",
    "SEGMENT_REFUSED_CODE",
    "SEGMENT_REFUSED_KIND",
    "GateSegmentKeys",
    "GateVerifier",
    "HandshakeClaim",
    "HandshakeRefusals",
    "LinkRefusals",
    "SegmentRefusal",
    "refusal_error",
    "transport_options",
    "transport_refusals",
]
