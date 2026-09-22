"""Define LifecycleVirtualCellProvider: the real `hivemind.queen.deps.VirtualCellProvider`.

Roadmap step 5.6's own last piece: `hivemind.queen.dispatcher.acquire.resolve_link` calls
`QueenDeps.virtual_provider.acquire(placement, task)` whenever `hivemind.queen.placement.decide`
returns a `ProvisionVirtual`/`ReuseDormant` Placement; `LifecycleVirtualCellProvider` is the
implementation that actually drives a `hivemind.hive.lifecycle.CellLifecycle` and hands back the
`WardenLink` `hivemind.queen.cell_gate.listener.CellListener` already attached to the Queen once
the Cell's own `CellReady`/`CellHeartbeat` handshake verified.

For a `ProvisionVirtual` Placement: `lifecycle.provision(spec, backend)` -- which, per
`hivemind.hive.backends.base.CellBackend.provision`'s own contract, already blocks until the
backend's own injected `hivemind.hive.backends.bootstrap.ReadinessGate` (the very same
`QueenReadinessGate` instance this provider also holds, wired by the composition root) has seen a
verified `CellReady` and first `CellHeartbeat` -- by which point `CellListener` has already
attached the `WardenLink` (its own module docstring: "Attach before resolving the gate... so it
must already be there the instant `wait_ready` returns"). This provider still calls
`gate.wait_ready(cell_id, ...)` itself afterwards, both for the explicit roadmap-5.6 sequencing and
because it is the one call that actually returns the `CellReadyInfo`/confirms readiness from this
provider's own point of view rather than trusting `provision()`'s side effect alone; it then finds
the already-attached link in `queen.wardens` and calls `lifecycle.mark_ready(cell_id, warden_id)`.

For a `ReuseDormant` Placement: `lifecycle.resume(cell_id)` (pool bookkeeping plus
`backend.resume`), then the same `gate.wait_ready` plus `queen.wardens` lookup. Known
simplification (documented, not fixed here): a resumed Cell's own connection may or may not have
stayed open through the backend's pause (Docker's own freeze-in-place semantics keep the socket
established; a QEMU VM's `savevm`/`loadvm` does not, and would need the Cell to dial back out and
re-run its whole `CellReady`/`CellHeartbeat` handshake). `QueenReadinessGate` today has no way to
force a *fresh* wait per resume (its own `_ready` Event, once set by the first `resolve()`, never
resets short of `forget()`); this provider therefore trusts whichever link `queen.wardens` already
holds for `cell_id` if one is still attached, and only falls back to `gate.wait_ready` when it is
not. A real backend-specific fix (resetting the gate's own per-Cell event on `resume()`, or a
QEMU-side reconnect) is future work, not this dispatch's -- the real in-Cell Warden this whole
path ultimately talks to is also still stubbed (this dispatch's own report names both).

On any failure past a successful `lifecycle.provision`/`.resume` (a wait-ready timeout, or no
matching link ever showing up in `queen.wardens`), this provider tears the Cell down and raises
`hivemind.hive.CellProvisionError` so `hivemind.queen.dispatcher.acquire.resolve_link`'s own
retry-once path runs (ADR-0028 Consequences); a failure from `lifecycle.provision`/`.resume`
themselves already raises `CellProvisionError` on its own and needs no extra teardown (neither
call ever leaves a dangling record on its own failure path -- see `hivemind.hive.lifecycle`'s own
key invariants). Known gap (documented, not fixed here): `hivemind.hive.cell_state.TRANSITIONS`
has no PROVISIONING -> DESTROYING edge, so a Cell that reached PROVISIONING (`lifecycle.provision`
returned) but never `mark_ready` (this provider's own `gate.wait_ready`/`queen.wardens` lookup
failed) cannot be torn down by this provider at all -- `_teardown_best_effort` swallows the
resulting `InvalidCellTransitionError` and the record is left tracked, PROVISIONING, for a future
sweep enhancement. A real, contract-conformant backend never reaches this branch: its own
`provision()` already blocks on the very same gate and raises `CellProvisionError` itself before
this provider's own `gate.wait_ready` could ever see a fresh timeout (module docstring, first
paragraph) -- only a backend that violates that contract could leave a Cell stuck here.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`: this
    provider names `WardenLink`/`Task`/`Queen`, all Layer-6 concepts `hive` (Layer 3) may never
    import back, so it cannot live under `hive` itself despite driving a `hive`-layer
    `CellLifecycle`. Implements `hivemind.queen.deps.VirtualCellProvider`. Built by the composition
    root when `[virtual_cells] backend` is set, then handed the live `Queen` via `bind_queen` once
    one exists (the same late-binding `CellListener.start(queen)` already uses, since a
    `QueenDeps.virtual_provider` must exist before `Queen(deps)` can be constructed at all). Calls
    into `hivemind.brood_chamber` (Task), `hivemind.hive` (CellProvisionError, VirtualCellSpec),
    `hivemind.hive.lifecycle` (CellLifecycle), `hivemind.queen.cell_gate.gate`
    (QueenReadinessGate), `hivemind.queen.deps` (WardenLink), `hivemind.queen.placement`
    (Placement, ProvisionVirtual, ReuseDormant), `hivemind.queen.queen` (Queen) and waggle only.

Key invariants:
    - `acquire` never returns a `WardenLink` for a Cell `lifecycle.mark_ready`/`.resume` has not
      already been told about: the lifecycle's own table and `queen.wardens` agree by the time
      this returns.
    - Every failure past a successful `provision`/`resume` tears the Cell down before raising, so
      a failed acquire never leaves an orphaned Cell for the Undertaker's own sweep to find later.

See Also:
    - .claude/roadmap.md step 5.6 for the acquire sequence this module implements.
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the retry-once Consequences this
      provider's own `CellProvisionError` triggers one layer up.
    - hivemind.queen.dispatcher.acquire for resolve_link, this provider's one caller.
    - hivemind.queen.cell_gate.listener for CellListener, which attaches the WardenLink this
      provider looks up.
"""

from __future__ import annotations

import contextlib
from typing import Protocol

from hivemind.brood_chamber import Task
from hivemind.hive import CellProvisionError
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.models import DEFAULT_READY_TIMEOUT_S
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.deps import WardenLink
from hivemind.queen.placement import Placement, ProvisionVirtual, ReuseDormant, ReuseReal
from waggle.ids import CellId

__all__ = ["LifecycleVirtualCellProvider"]


class LifecycleVirtualCellProvider:
    """Drive a CellLifecycle to acquire a WardenLink for a Virtual Placement.

    Holds a mutable `_queen` reference, set once by `bind_queen` (module docstring: the same
    late-binding shape `CellListener.start(queen)` already uses, for the identical reason -- this
    provider must exist before `QueenDeps`/`Queen` do).
    """

    def __init__(self, lifecycle: CellLifecycle, gate: QueenReadinessGate) -> None:
        """Build a LifecycleVirtualCellProvider; call `bind_queen` before the first `acquire`.

        Args:
            lifecycle: Drives every Virtual Cell state edge and backend call.
            gate: Resolves once the Cell's own CellReady/CellHeartbeat handshake has verified;
                the same instance the composition root also wires into every registered backend.
        """
        self._lifecycle = lifecycle
        self._gate = gate
        self._queen: _WardensView | None = None

    def bind_queen(self, queen: _WardensView) -> None:
        """Give this provider a live view of attached Wardens; call once, after `Queen(deps)`.

        Args:
            queen: Anything with a `.wardens` property (`hivemind.queen.queen.Queen` in
                production); typed structurally here so this module never has to import `Queen`
                at type-check time beyond `TYPE_CHECKING` (see `_WardensView` below).
        """
        self._queen = queen

    async def acquire(self, placement: Placement, task: Task) -> WardenLink:
        """Provision or resume the Cell `placement` names, and return its own WardenLink.

        See `hivemind.queen.deps.VirtualCellProvider.acquire` for the full contract.
        """
        if isinstance(placement, ReuseReal):
            # Unreachable in practice: hivemind.queen.dispatcher.acquire.resolve_link resolves a
            # ReuseReal placement itself and never calls this provider for one.
            raise TypeError("LifecycleVirtualCellProvider.acquire got a ReuseReal placement.")
        if isinstance(placement, ProvisionVirtual):
            return await self._acquire_provision(placement)
        return await self._acquire_dormant(placement)

    async def _acquire_provision(self, placement: ProvisionVirtual) -> WardenLink:
        """Provision a fresh Cell from `placement.spec`, wait for it, and return its link."""
        cell = await self._lifecycle.provision(placement.spec, placement.backend)
        try:
            await self._gate.wait_ready(cell.id, placement.spec.ready_timeout_s)
            link = self._require_link(cell.id)
            await self._lifecycle.mark_ready(cell.id, link.warden_id)
        except Exception as exc:
            await self._teardown_best_effort(cell.id)
            raise CellProvisionError(
                placement.backend, placement.spec.image, f"never became reachable: {exc}"
            ) from exc
        return link

    async def _acquire_dormant(self, placement: ReuseDormant) -> WardenLink:
        """Resume `placement.cell_id`, wait for its Warden, and return its link."""
        cell_id = placement.cell_id
        try:
            await self._lifecycle.resume(cell_id)
            link = self._attached_link(cell_id)
            if link is None:
                # The pause's own backend closed the connection (module docstring's own known
                # simplification): fall back to waiting for a fresh handshake through the gate.
                await self._gate.wait_ready(cell_id, DEFAULT_READY_TIMEOUT_S)
                link = self._require_link(cell_id)
        except Exception as exc:
            await self._teardown_best_effort(cell_id)
            raise CellProvisionError(
                "pool", cell_id, f"dormant Cell never became reachable again: {exc}"
            ) from exc
        return link

    def _require_link(self, cell_id: CellId) -> WardenLink:
        """Return the attached link for `cell_id`, or raise if none is attached yet."""
        link = self._attached_link(cell_id)
        if link is None:
            raise LookupError(f"no WardenLink is attached for Cell {cell_id} yet.")
        return link

    def _attached_link(self, cell_id: CellId) -> WardenLink | None:
        """Return the attached WardenLink whose own Cell is `cell_id`, or None."""
        assert self._queen is not None  # noqa: S101 - bind_queen() always runs before acquire().
        return next((link for link in self._queen.wardens if link.cell.id == cell_id), None)

    async def _teardown_best_effort(self, cell_id: CellId) -> None:
        """Tear `cell_id` down, swallowing any further error: the caller is already failing."""
        # A failed cleanup must never hide the original failure this is already unwinding from.
        with contextlib.suppress(Exception):
            await self._lifecycle.teardown(cell_id)


class _WardensView(Protocol):
    """Structural stand-in for `hivemind.queen.queen.Queen`'s own `.wardens` property.

    Lets `LifecycleVirtualCellProvider.bind_queen` take a real `Queen` without this module ever
    importing `hivemind.queen.queen` at runtime (that module already imports `hivemind.queen.
    deps`, which this provider itself does not need to import back beyond `WardenLink`); a
    `typing.Protocol`, not a base class -- a real `Queen` already satisfies it structurally, with
    no inheritance needed.
    """

    @property
    def wardens(self) -> tuple[WardenLink, ...]:
        """Every Warden currently attached, in attachment order."""
        ...
