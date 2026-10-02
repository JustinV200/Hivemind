"""Verify a Virtual Cell's frames by the key its Cell registered, and record a failed handshake.

`GateVerifier` is the `waggle.codec.Verifier` the Cell gate's one shared `Codec` checks every
incoming frame's signature against (the listener's module docstring explains why a fresh node's
first frame is resolved by the `cell_id` it claims). A frame that fails before its link is attached
proves nothing: whoever dialled may have named any Cell's id and any node's. Roadmap step 10.6
still wants the Guard Bee to see such a knock, so `HandshakeRefusals` records it as
`guard.handshake_refused`: about the Hive being knocked on (the subject), never about the claimed
Cell, with what the frame claimed kept apart as `claimed_cell_id` and `claimed_node_id` (each only
when it is a well-formed id) beside the reason and the peer's address. Nothing about the claimed
Cell is trusted or acted on, so a forger cannot frame an honest Cell by naming it; a rule that
counts these knocks counts the Hive's, not a Cell's.

The claim travels from the verifier to the listener's handshake through a context variable, not a
field on the shared verifier: the codec raises inside the connection's own handler task, but the
transport closes the connection (an await) before the error reaches the handler, and another
connection's failure may land in between. A context variable set in the handler's task is that
task's alone.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.cell_gate`.
    Built once per `CellListener`. Calls into `hivemind.common.logging`, `hivemind.pheromone`
    (GuardEvent, TrailRecorder), `hivemind.queen.cell_gate.gate` and waggle (errors, ids,
    signing) only.

Key invariants:
    - A frame's signature is always checked against the key registered for the Cell it claims
      (or the key its node already proved), never a key the frame carries.
    - A handshake failure is recorded about the Hive, never about the claimed Cell or node, and
      at most `HANDSHAKE_RECORDS_PER_WINDOW` times per `HANDSHAKE_WINDOW_S`; the knocks skipped
      in between are counted on the next record (`suppressed`), so a flood cannot fill the trail.
    - Nothing is recorded without a recorder.

See Also:
    - hivemind.queen.cell_gate.listener for the handshake this records about.
    - hivemind.queen.cell_gate.refusals for what an attached link refused.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta

from hivemind.common.logging import get_logger
from hivemind.pheromone import GuardEvent, TrailRecorder
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from waggle.errors import InvalidIdError, SignatureError, UnknownSignerError
from waggle.ids import CellId, IdKind, new_event_id, parse_id
from waggle.signing import Ed25519Verifier, public_key_from_hex

log = get_logger(__name__)

HANDSHAKE_REFUSED_KIND = "guard.handshake_refused"
# A real Cell's handshake fails rarely (a wrong key, a recreated Cell); ten records a minute show
# a knocking pattern to the Guard Bee without letting a flood of dials write a row each.
HANDSHAKE_RECORDS_PER_WINDOW = 10
HANDSHAKE_WINDOW_S = 60.0

# What the frame now failing in this task claimed; set by the verifier, read by the handshake.
_CLAIM: ContextVar[HandshakeClaim | None] = ContextVar("cell_gate_handshake_claim", default=None)

__all__ = [
    "HANDSHAKE_RECORDS_PER_WINDOW",
    "HANDSHAKE_REFUSED_KIND",
    "HANDSHAKE_WINDOW_S",
    "GateVerifier",
    "HandshakeClaim",
    "HandshakeRefusals",
]


@dataclass(frozen=True, slots=True)
class HandshakeClaim:
    """What a frame that failed its signature claimed to be; proved by nothing.

    Attributes:
        cell_id: The `payload.cell_id` it named, when that is a well-formed Cell id.
        node_id: The envelope `node_id` it named, when that is a well-formed node id.
    """

    cell_id: str | None
    node_id: str | None


class GateVerifier:
    """The Verifier every accepted connection's shared Codec checks a signature against.

    An already-bound node_id looks its key up directly; an unknown node_id's first frame is
    resolved by reading the claimed cell_id out of the still-raw canonical bytes.
    """

    def __init__(self, gate: QueenReadinessGate) -> None:
        """Hold the gate this verifier resolves an unknown node_id's first frame against."""
        self._gate = gate
        self._learned: dict[str, bytes] = {}  # node_id -> the raw public key it verified under.

    def verify(self, node_id: str, canonical: bytes, signature: str) -> None:
        """Verify `signature`, resolving `node_id`'s key by cell_id on its very first frame.

        Raises:
            SignatureError: UnknownSignerError when no key is registered for what the frame
                claims, InvalidSignatureError when the signature does not verify; either way
                the claim is kept for this task's handshake to record (module docstring).
        """
        try:
            raw_key = self._learned.get(node_id)
            if raw_key is None:
                raw_key = _key_for_first_frame(self._gate, canonical)
            # A one-off Ed25519Verifier over exactly this key: it raises on a bad signature.
            Ed25519Verifier({node_id: raw_key}).verify(node_id, canonical, signature)
        except SignatureError:
            _CLAIM.set(_claim_of(node_id, canonical))
            raise
        self._learned[node_id] = raw_key  # Only cached once the signature actually verified.


class HandshakeRefusals:
    """Record a handshake that failed before attach, about the Hive, at a bounded rate."""

    def __init__(self, recorder: TrailRecorder | None) -> None:
        """Build the refusals one listener records through.

        Args:
            recorder: The Queen's trail and identity; None records nothing.
        """
        self._recorder = recorder
        self._window_start: datetime | None = None
        self._recorded = 0  # Records in the current window.
        self._suppressed = 0  # Knocks not recorded since the last record.

    async def refused(self, error: SignatureError, address: str) -> None:
        """Record that a connection's handshake ended on a frame whose signature failed.

        Args:
            error: What the codec raised; its code's last part is the reason (`invalid`,
                `missing` or `unknown_node`), the spec's own words.
            address: The peer's `host:port`, as the transport reported it.
        """
        claim = _CLAIM.get() or HandshakeClaim(cell_id=None, node_id=None)
        reason = error.code.rsplit(".", 1)[-1]
        log.warning("queen.handshake_refused", reason=reason, address=address)
        recorder = self._recorder
        if recorder is None or not self._admit(recorder.clock.now()):
            return
        payload: dict[str, str | int | None] = {
            "reason": reason,
            "address": address,
            "claimed_cell_id": claim.cell_id,
            "claimed_node_id": claim.node_id,
            "suppressed": self._suppressed,
        }
        self._suppressed = 0
        event = GuardEvent(
            id=new_event_id(recorder.clock),
            hive_id=recorder.hive_id,
            node_id=recorder.node_id,  # The Queen's own record.
            at=recorder.clock.now(),
            actor="system",
            kind=HANDSHAKE_REFUSED_KIND,
            subject_id=recorder.hive_id,  # The Hive was knocked on; no Cell is proved.
            payload=dict(payload),
        )
        await recorder.trail.record(event)

    def _admit(self, now: datetime) -> bool:
        """Count one knock; return whether it may be recorded in the current window."""
        window = timedelta(seconds=HANDSHAKE_WINDOW_S)
        if self._window_start is None or now - self._window_start >= window:
            self._window_start, self._recorded = now, 0
        if self._recorded >= HANDSHAKE_RECORDS_PER_WINDOW:
            self._suppressed += 1
            return False
        self._recorded += 1
        return True


def _key_for_first_frame(gate: QueenReadinessGate, canonical: bytes) -> bytes:
    """Read the claimed cell_id out of raw canonical bytes and return its registered key.

    Raises:
        UnknownSignerError: The bytes are not readable JSON, carry no valid CellId-shaped
            payload.cell_id, or that cell_id has no key registered (never expected, or forgotten).
    """
    cell_id = _claimed_cell_id(canonical)
    if cell_id is None:
        raise UnknownSignerError(
            "The first frame from an unknown node did not carry a readable payload.cell_id."
        )
    key_hex = gate.expected_key(cell_id)
    if key_hex is None:
        raise UnknownSignerError(
            f"Cell {cell_id} is not registered with this ReadinessGate (never expected, or "
            "already forgotten)."
        )
    return public_key_from_hex(key_hex)


def _claimed_cell_id(canonical: bytes) -> CellId | None:
    """The well-formed `payload.cell_id` the raw frame names, or None."""
    try:
        wire = json.loads(canonical)
        return CellId(parse_id(str(wire["payload"]["cell_id"]), IdKind.CELL))
    except (ValueError, KeyError, TypeError, InvalidIdError):
        # Unreadable JSON, no payload.cell_id, or a malformed one: a frame that names no Cell.
        return None


def _claim_of(node_id: str, canonical: bytes) -> HandshakeClaim:
    """What a failing frame claimed, keeping only well-formed ids (nothing else is recorded)."""
    # The envelope's node_id is checked only once its signature verifies; here it may be anything.
    try:
        claimed_node: str | None = parse_id(node_id, IdKind.NODE)
    except InvalidIdError:
        claimed_node = None
    cell_id = _claimed_cell_id(canonical)
    claimed_cell = str(cell_id) if cell_id is not None else None
    return HandshakeClaim(cell_id=claimed_cell, node_id=claimed_node)
