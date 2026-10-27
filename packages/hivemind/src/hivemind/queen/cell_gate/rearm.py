"""Define rearm_gate: tell a restarted Queen's readiness gate the key of every live Virtual Cell.

A Virtual Cell's Warden proves itself on every connection with the Cell's own signing key, whose
public half the gate learns when the Cell is provisioned (`ReadinessGate.expect`). That was only
ever in the gate's memory, so a Queen that restarted could verify none of the Cells that outlived
her, and their Wardens, now redialing her (ADR-0062), were refused at the handshake. Every backend
now stamps the public key on the Cell as a label (`hivemind.hive.CELL_KEY_LABEL`), and
`rearm_gate` reads every backend's live Cells at her start and registers each key the gate does not
already hold. It returns what it listed: the Virtual Cells that outlived her, which Requeening
waits for (`hivemind.queen.requeening.RequeeningStart.virtual_cells`).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`. Called
    by `hivemind.cli.compose.hive.run` once the listener is accepting and the lifecycle has been
    reconciled, before the Queen's loop starts. Calls into `hivemind.common.logging`,
    `hivemind.hive` (BackendRegistry, CELL_KEY_LABEL, VirtualCellRecord),
    `hivemind.queen.cell_gate.gate` and waggle only.

Key invariants:
    - A key is registered only for a Cell the gate does not already expect, so a Cell this very
      process provisioned keeps the readiness wait its provisioning registered.
    - A label that is not a well-formed Ed25519 public key registers nothing: such a Cell's Warden
      can never prove itself, and is left to the Undertaker.
    - Nothing but the public key is read from a label, and only backend labels (infrastructure the
      Hive stamps itself) are read: a Cell cannot name its own key.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for why a Cell's key rides in a label.
    - hivemind.hive.backends.bootstrap for CELL_KEY_LABEL and where the key is minted.
"""

from __future__ import annotations

from hivemind.common.logging import get_logger
from hivemind.hive import CELL_KEY_LABEL, BackendRegistry, VirtualCellRecord
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from waggle.ids import HiveId
from waggle.signing import public_key_from_hex

log = get_logger(__name__)

__all__ = ["rearm_gate"]


async def rearm_gate(
    gate: QueenReadinessGate, registry: BackendRegistry, hive_id: HiveId
) -> tuple[VirtualCellRecord, ...]:
    """Register the key of every live Virtual Cell the gate does not already expect.

    Args:
        gate: The Queen's readiness gate, which her Cell listener verifies handshakes against.
        registry: Every Virtual Cell backend this Hive wires.
        hive_id: This Hive; each backend lists only its Cells.

    Returns:
        Every Cell the backends list for this Hive now, in backend order.
    """
    listed: list[VirtualCellRecord] = []
    for name in registry.names():
        # External await: one backend's own listing (a Docker or QEMU query), bounded by its
        # client's timeout; a failure propagates, as reconcile's own listing would.
        for record in await registry.get(name).list_cells(hive_id):
            listed.append(record)
            await _expect(gate, record)
    return tuple(listed)


async def _expect(gate: QueenReadinessGate, record: VirtualCellRecord) -> None:
    """Register `record`'s labelled key unless the gate already expects the Cell."""
    key = record.labels.get(CELL_KEY_LABEL)
    if key is None or gate.expected_key(record.cell_id) is not None:
        return  # No key to read (a Cell from before ADR-0062), or one this process registered.
    try:
        public_key_from_hex(key)
    except ValueError:
        log.warning("cell_gate.rearm_bad_key", cell_id=record.cell_id)
        return  # Never registered: a malformed key could only ever fail every handshake.
    await gate.expect(record.cell_id, key)
