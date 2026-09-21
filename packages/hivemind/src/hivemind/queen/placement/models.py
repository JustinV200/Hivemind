"""Define Placement: the Queen's pure choice of where a task's Cell comes from (ADR-0028).

Phase 5 gives placement two kinds of source instead of one: Real Cells (existing, borrowed
devices) already attached as a `hivemind.queen.deps.WardenLink`, and Virtual Cells (VM or
container Cells the Hive provisions and later destroys or Overwinters, `hivemind.hive`) that do
not exist yet or sit paused in the Overwintering pool (`docs/adr/0029-overwintering-policy.md`).
`Placement` is the union ADR-0028 names: `ReuseReal` (place on an already-attached Warden's own
Cell, v0's only outcome), `ReuseDormant` (resume an Overwintered Virtual Cell instead of paying to
provision a fresh one) or `ProvisionVirtual` (provision a fresh Virtual Cell from `spec` on
`backend`). Every variant carries its own `reason`: the rule that decided, and any Cell Wax
(a Queen-written caution about one Cell) that weighed on it, so `hivemind.queen.dispatcher` can
record it verbatim as the `queen.placed` trail event's own payload.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.placement`
    sub-package. Returned by `hivemind.queen.placement.decide.decide`; read by
    `hivemind.queen.dispatcher`, which turns a `ReuseReal` into a wire send over its own already-
    attached link and a `ReuseDormant`/`ProvisionVirtual` into one over the `VirtualCellProvider`
    seam (`hivemind.queen.deps`) roadmap step 5.6 implements. Calls into `hivemind.hive`
    (VirtualCellSpec) and `waggle.ids` only.

Key invariants:
    - Every variant is a frozen, slotted dataclass (codingrules section 8.5): a Placement is a
      value, never mutated once `decide` returns it.
    - `ReuseDormant.warden_id` is `None` until the Cell's Warden reconnects after resume
      (`docs/adr/0029`): the Cell itself is already known (its `cell_id`), but its Warden is not
      live again until `VirtualCellProvider.acquire` finishes waiting for its Heartbeat.
    - `ProvisionVirtual.spec` is always a fully-formed `hivemind.hive.VirtualCellSpec`: `decide`
      never builds one from scratch (it lacks a Hive id and a measured `ForageCapacity`, both only
      the caller's `Inventory` snapshot supplies); it only ever selects or narrows one already on
      `hivemind.queen.placement.inventory.Inventory.virtual_backends`.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the Placement union this module
      implements verbatim.
    - docs/adr/0029-overwintering-policy.md for what `ReuseDormant` resumes.
    - hivemind.queen.placement.decide for decide, this module's one producer.
    - hivemind.queen.placement.inventory for Inventory, the snapshot `decide` chooses among.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.hive import VirtualCellSpec
from waggle.ids import CellId, WardenId

__all__ = ["Placement", "ProvisionVirtual", "ReuseDormant", "ReuseReal"]


@dataclass(frozen=True, slots=True)
class ReuseReal:
    """Place the task on an already-attached Warden's own Real Cell (v0's only outcome)."""

    cell_id: CellId
    warden_id: WardenId
    reason: str


@dataclass(frozen=True, slots=True)
class ReuseDormant:
    """Resume an Overwintered Virtual Cell (`docs/adr/0029`) instead of provisioning a fresh one.

    `warden_id` is `None` until the Cell's own Warden reconnects: `hivemind.queen.deps.
    VirtualCellProvider.acquire` waits for that Heartbeat before returning a `WardenLink`, so a
    caller that only has this Placement (before `acquire` runs) cannot yet name the Warden.
    """

    cell_id: CellId
    warden_id: WardenId | None
    reason: str


@dataclass(frozen=True, slots=True)
class ProvisionVirtual:
    """Provision a fresh Virtual Cell from `spec` on the named backend."""

    spec: VirtualCellSpec
    backend: str
    reason: str


# ADR-0028: "Placement is a union": every rule below returns exactly one of these three shapes.
Placement = ReuseReal | ReuseDormant | ProvisionVirtual
