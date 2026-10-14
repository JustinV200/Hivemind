"""Define GateSegmentKeys: the key a Virtual Cell's trail segment is checked against, if signed.

A Swarm node's trail segment must be signed by the key the registry holds for it (ADR-0053), and
the swarm gate says so. A Virtual Cell is different: its Warden is booted inside a container the
backend provisioned, with a key the Queen's readiness gate registered before the Cell ever dialled
(`QueenReadinessGate.expect`), and the segments it has always shipped carry no signature. The Cell
gate therefore asks for no signature, but it does not take one on trust when it is offered: a
segment that arrives signed is verified against that registered key, so a Warden that signs with
anything else is refused like any other forgery. `GateSegmentKeys` is that rule as a `SegmentKeys`,
looked up by the Cell the chunk names (a Virtual Cell's node is only learned at its handshake, but
its Cell's key is known from provisioning to teardown).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.cell_gate`.
    Built by the composition root (`hivemind.cli.compose.virtual_cells`) over the same
    `QueenReadinessGate` the Cell listener verifies frames with, and handed to the
    `TrailSegmentReceiver` bound on that listener. Calls into `hivemind.queen.trail` and waggle
    only.

Key invariants:
    - A Virtual Cell's segment is never required to be signed: this gate answers `is_required`
      False, so an unsigned segment merges as it always did.
    - A signed one is checked against the key registered for the Cell it names; a Cell the gate
      has forgotten (already released) has no opinion expressed and nothing is checked.
    - Nothing here logs or returns the key as text: the receiver gets raw bytes and only compares.

See Also:
    - hivemind.queen.swarm_gate.attach for the swarm gate's registry-backed counterpart, whose
      segments are required to be signed.
    - hivemind.queen.trail.signature for the check this feeds.
"""

from __future__ import annotations

from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.trail import SegmentKey
from waggle.messages.swarm import TrailSegmentSync
from waggle.signing import public_key_from_hex

__all__ = ["GateSegmentKeys"]


class GateSegmentKeys:
    """Look up a Virtual Cell's segment key in the readiness gate: optional, never required."""

    def __init__(self, gate: QueenReadinessGate) -> None:
        """Bind the keys to the gate the Cell listener verifies frames against.

        Args:
            gate: Holds each provisioned Cell's registered public key until it is forgotten.
        """
        self._gate = gate

    async def key_for(self, message: TrailSegmentSync) -> SegmentKey | None:
        """Return the key registered for the Cell `message` names, or None when the gate has none.

        Args:
            message: The final chunk of a segment export; its `cell_id` names the sender.

        Returns:
            The Cell's registered key as an optional one, or None for a Cell the gate does not
            hold (never expected, or already forgotten): nothing is checked then.
        """
        registered = self._gate.expected_key(message.cell_id)
        if registered is None:
            return None
        return SegmentKey(public_key=public_key_from_hex(registered), is_required=False)
