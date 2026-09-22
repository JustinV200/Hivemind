"""Define QueenReadinessGate: the Queen-side hivemind.hive.backends.bootstrap.ReadinessGate.

A `CellBackend.provision` call (`hivemind.hive.backends.docker.DockerCellBackend`, `.qemu.
QemuCellBackend`) blocks on its own injected `ReadinessGate` until the Cell it just started has
announced itself; that Protocol is defined backend-side (`hivemind.hive.backends.bootstrap`)
precisely so a backend never has to know who is actually listening. `QueenReadinessGate` is the
real, Queen-side implementation roadmap step 5.4's own dispatch left as a documented gap: `expect`
registers a Cell's public key before its container/VM exists, `wait_ready` blocks until both a
signed `CellReady` and the Cell's first `CellHeartbeat` have arrived, and `forget` drops a
registration on any cleanup path.

The identity puzzle this class exists to solve: `expect(cell_id, verify_key_hex)` is called before
the Cell exists, so it can only ever be keyed by `cell_id`. But `waggle.signing.Ed25519Verifier`
(what actually checks a signature) is keyed by `node_id`, and a Virtual Cell mints a *fresh*
`NodeId` at boot (`hivemind.cli.in_cell.config.build_runtime_config`: `node_id=new_node_id(clock)`,
unrelated to `cell_id`) -- there is no way to know which `node_id` a given `cell_id` will announce
itself as before it does. This class's own answer: `hivemind.queen.cell_gate.listener.
_GateVerifier` (the `waggle.codec.Codec` `Verifier` this gate hands `CellListener` for every fresh
connection) reads the Cell's claimed `cell_id` straight out of the *unverified* canonical bytes
`Codec.decode` already hands its `Verifier.verify` (the raw wire dict, JSON-decodable again,
before the payload is parsed into a typed `CellReady`) on the very first frame from an unknown
`node_id`, looks up that `cell_id`'s own registered key here, and verifies the signature against
it before trusting anything -- signatures stay mandatory throughout, only *which* key to check is
resolved differently for a first frame than for every frame after it (see `listener.py`'s own
module docstring for the full mechanism). Once a first frame verifies, the gate never needs the
`cell_id` lookup again for that connection: `CellListener` binds `node_id` to this gate's own
`CellReadyInfo` the moment it also has the follow-up `CellHeartbeat`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.cell_gate`
    sub-package -- the Queen is the only global view, so the Queen-side half of a backend-defined
    seam belongs here, not in `hive` (Layer 3, which must never import `queen`). Implements
    `hivemind.hive.backends.bootstrap.ReadinessGate`. Constructed once per Hive by the composition
    root and shared by every `CellBackend` that needs it; driven by `hivemind.queen.cell_gate.
    listener.CellListener` on the receiving side. Calls into `hivemind.hive.backends.bootstrap`
    (CellReadyInfo) and `waggle.ids` (CellId) only.

Key invariants:
    - `expect` never blocks and never fails: it only records a key, so a backend can call it
      before starting any infrastructure (ADR-0027's own ordering).
    - `wait_ready` raises `TimeoutError` (never returns) if `timeout_s` elapses before both a
      verified `CellReady` and a `CellHeartbeat` for `cell_id` have arrived, matching
      `hivemind.hive.backends.fake.FakeReadinessGate`'s own documented contract.
    - `forget` is idempotent: forgetting a `cell_id` never `expect`-ed, or already forgotten, is a
      no-op (`ReadinessGate.forget`'s own contract).

See Also:
    - hivemind.hive.backends.bootstrap for ReadinessGate, CellReadyInfo and CellBootstrap, the
      seam this class implements and the identity `expect` is handed.
    - hivemind.queen.cell_gate.listener for CellListener, this gate's own wire-side counterpart.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the per-Cell
      signing key and the fresh-node-id-per-boot decision this module works around.
"""

from __future__ import annotations

import asyncio

from hivemind.hive.backends.bootstrap import CellReadyInfo
from waggle.ids import CellId, NodeId

__all__ = ["QueenReadinessGate"]


class QueenReadinessGate:
    """The real ReadinessGate: registers a Cell's key by id, resolves once its Warden is live.

    Owns its own mutable state in place (codingrules section 8.5): `_expected`, `_ready` and
    `_ready_info` change as Cells are expected, connect and are forgotten. Safe to call
    concurrently: a fresh `asyncio.Event` per Cell means one Cell's own wait never blocks another.
    """

    def __init__(self) -> None:
        """Create a gate expecting nothing yet; a backend's `provision()` calls `expect` first."""
        # cell_id -> the hex public key expect() registered; read by CellListener's own
        # _GateVerifier the moment an unknown node_id's first frame claims that cell_id.
        self._expected: dict[CellId, str] = {}
        # cell_id -> set once CellListener has seen both a verified CellReady and the Cell's own
        # first CellHeartbeat; wait_ready blocks on this.
        self._ready: dict[CellId, asyncio.Event] = {}
        self._ready_info: dict[CellId, CellReadyInfo] = {}
        # cell_id -> the node_id it was verified under, once CellListener has bound the two; read
        # back by whichever caller (hivemind.hive.provider, roadmap step 5.6) needs it to build a
        # WardenLink's own Hop after wait_ready resolves.
        self._node_id: dict[CellId, NodeId] = {}

    async def expect(self, cell_id: CellId, verify_key_hex: str) -> None:
        """Register `cell_id`'s public key; see `ReadinessGate.expect`."""
        self._expected[cell_id] = verify_key_hex
        self._ready[cell_id] = asyncio.Event()

    async def wait_ready(self, cell_id: CellId, timeout_s: float) -> CellReadyInfo:
        """Block until `cell_id` is ready; see `ReadinessGate.wait_ready`."""
        event = self._ready.get(cell_id)
        if event is None:
            # Never expect()-ed (or already forgotten): this can never become ready, so failing
            # immediately -- rather than waiting out timeout_s for nothing -- still raises the
            # documented TimeoutError shape a caller already handles.
            raise TimeoutError(f"Cell {cell_id} was never registered with this ReadinessGate.")
        try:
            async with asyncio.timeout(timeout_s):
                await event.wait()
        except TimeoutError as exc:
            raise TimeoutError(
                f"Cell {cell_id} never reported ready within {timeout_s}s (QueenReadinessGate)."
            ) from exc
        return self._ready_info[cell_id]

    async def forget(self, cell_id: CellId) -> None:
        """Drop any registration or pending wait for `cell_id`; see `ReadinessGate.forget`."""
        self._expected.pop(cell_id, None)
        self._ready.pop(cell_id, None)
        self._ready_info.pop(cell_id, None)
        self._node_id.pop(cell_id, None)

    def expected_key(self, cell_id: CellId) -> str | None:
        """Return the hex public key `expect()` registered for `cell_id`, or None if unregistered.

        Read by `hivemind.queen.cell_gate.listener.CellListener`'s own per-connection Verifier to
        resolve which key to check an unknown node_id's first frame against (module docstring).

        Args:
            cell_id: The Cell whose registered key to look up.

        Returns:
            The hex public key, or None when `cell_id` was never `expect`-ed or was forgotten.
        """
        return self._expected.get(cell_id)

    def resolve(self, cell_id: CellId, node_id: NodeId, info: CellReadyInfo) -> None:
        """Mark `cell_id` ready: called by `CellListener` once both frames have arrived.

        Args:
            cell_id: The Cell that is now ready.
            node_id: The node id its frames verified under, for a caller of `node_id_for`.
            info: What its `CellReady` and probed capacity reported.
        """
        self._ready_info[cell_id] = info
        self._node_id[cell_id] = node_id
        event = self._ready.get(cell_id)
        if event is not None:
            event.set()

    def node_id_for(self, cell_id: CellId) -> NodeId | None:
        """Return the node id `cell_id` was verified under, once `resolve()` has run for it.

        Args:
            cell_id: The Cell to look up.

        Returns:
            The bound `NodeId`, or None before `resolve()` has run (or after `forget()`).
        """
        return self._node_id.get(cell_id)
