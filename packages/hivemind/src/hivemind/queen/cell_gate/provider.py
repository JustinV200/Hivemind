"""Define LifecycleVirtualCellProvider: the real `hivemind.queen.deps.VirtualCellProvider`.

Roadmap step 5.6's own last piece: `hivemind.queen.dispatcher.acquire.acquire_virtual` calls
`QueenDeps.virtual_provider.acquire(placement, task)` whenever `hivemind.queen.placement.decide`
returns a `ProvisionVirtual`/`ReuseDormant` Placement (beside the Queen's tick, as an acquisition
`hivemind.queen.dispatcher.provisions` starts); `LifecycleVirtualCellProvider` is the
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
`hivemind.hive.CellProvisionError` so `hivemind.queen.dispatcher.acquire.acquire_virtual`'s own
retry-once path runs (ADR-0028 Consequences); a failure from `lifecycle.provision`/`.resume`
themselves already raises `CellProvisionError` on its own and needs no extra teardown (neither
call ever leaves a dangling record on its own failure path -- see `hivemind.hive.lifecycle`'s own
key invariants). A Cell that reached PROVISIONING (`lifecycle.provision` returned) but never
`mark_ready` (this provider's own `gate.wait_ready`/`queen.wardens` lookup failed, or Night Veil
attestation was red) is torn down through `hivemind.hive.cell_state`'s PROVISIONING -> DESTROYING
edge, so nothing is left tracked for a sweep to find.

**Night Veil attestation (roadmap step 5.7b, ADR-0030, this branch closing a gap an earlier
implementer's own report named):** for a freshly provisioned Cell whose `comb_shield` is
NIGHT_VEIL, `_acquire_provision` calls `hive.night_veil.attest_cell(probe, self._trail, cell.id)`
after the link is found but before `mark_ready` -- "readiness is attestation of an image, never
configuration of a Cell" (ADR-0030). Any red check, or the probe itself failing to run, tears the
Cell down and raises `CellProvisionError` naming the failing checks; a passed attestation falls
through to `mark_ready` exactly as before this step. The probe itself is injected as
`probe_factory: Callable[[Cell], NightVeilProbe]`, never defaulted to a fake here (codingrules
14.4's fakes are for tests, never a silent production default): the composition root
(`hivemind.cli.compose.virtual_cells`) supplies it, and today supplies one that fails closed with
a clear error, because `WardenLink` (`hivemind.queen.deps`) carries a Waggle `Transport`, not a
`hivemind.cell.CellSession` `SessionNightVeilProbe` needs -- no session-opening seam exists yet
from the Queen to a Virtual Cell (this module's own report names the gap).

**The Night Veil boundary (codingrules section 12):** once a NIGHT_VEIL Cell is acquired for a
task, `acquire` binds the task to it in the boundary's ephemeral segments (`night_veil`), before
the dispatcher records the placement: from then on every record the Queen makes about the task
(`queen.placed`, `queen.assigned`, its grant) waits whole in the Cell's segment and reaches the
trail as the skeleton only, and it is purged with the Cell.

**The announced tier (roadmap step 10.3a):** the link `CellListener` attaches carries the Comb
Shield tier the Cell announced in its own `CellReady`, and the dispatcher binds the task to that
tier (`hivemind.queen.dispatcher.ready`). So before anything else is checked, a freshly provisioned
Cell whose link names a tier other than the one it was provisioned at is torn down and refused:
a Cell that is misconfigured, or lying, never gets a task bound to the wrong tier. (Until the
in-Cell Warden read its tier from its bootstrap, every Night Veil Cell announced MEADOW and its
tasks were bound to MEADOW.)

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`: this
    provider names `WardenLink`/`Task`/`Queen`, all Layer-6 concepts `hive` (Layer 3) may never
    import back, so it cannot live under `hive` itself despite driving a `hive`-layer
    `CellLifecycle`. Implements `hivemind.queen.deps.VirtualCellProvider`. Built by the composition
    root when `[virtual_cells] backend` is set, then handed the live `Queen` via `bind_queen` once
    one exists (the same late-binding `CellListener.start(queen)` already uses, since a
    `QueenDeps.virtual_provider` must exist before `Queen(deps)` can be constructed at all). Calls
    into `hivemind.brood_chamber` (Task), `hivemind.hive` (CellProvisionError, VirtualCellSpec),
    `hivemind.hive.lifecycle` (CellLifecycle), `hivemind.hive.night_veil` (NightVeilProbe,
    attest_cell), `hivemind.pheromone` (TrailRecorder, EphemeralSegments),
    `hivemind.queen.cell_gate.gate` (QueenReadinessGate), `hivemind.queen.deps` (WardenLink),
    `hivemind.queen.placement` (Placement, ProvisionVirtual, ReuseDormant), `hivemind.queen.queen`
    (Queen) and waggle only.

Key invariants:
    - `acquire` never returns a `WardenLink` for a Cell `lifecycle.mark_ready`/`.resume` has not
      already been told about: the lifecycle's own table and `queen.wardens` agree by the time
      this returns.
    - Every failure past a successful `provision`/`resume` tears the Cell down before raising, so
      a failed acquire never leaves an orphaned Cell for the Undertaker's own sweep to find later.
    - A freshly provisioned Cell never reaches `mark_ready` unless its link carries the tier it
      was provisioned at.
    - A NIGHT_VEIL Cell never reaches `mark_ready` without a passed `attest_cell` call first
      (ADR-0030): a red check, or the probe raising, tears the Cell down the same way any other
      post-provision failure does.
    - A task handed a NIGHT_VEIL Cell's link is bound to that Cell's segment before `acquire`
      returns it.

See Also:
    - .claude/roadmap.md step 5.6 for the acquire sequence this module implements.
    - .claude/roadmap.md step 5.7b for the Night Veil attestation hook this module adds.
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the retry-once Consequences this
      provider's own `CellProvisionError` triggers one layer up.
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for "readiness is attestation
      of an image, never configuration of a Cell".
    - hivemind.hive.night_veil for NightVeilProbe and attest_cell, this module's own attestation
      call.
    - hivemind.queen.dispatcher.acquire for acquire_virtual, this provider's one caller.
    - hivemind.queen.cell_gate.listener for CellListener, which attaches the WardenLink this
      provider looks up.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Protocol

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, CombShieldLevel
from hivemind.hive import CellProvisionError
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.models import DEFAULT_READY_TIMEOUT_S
from hivemind.hive.night_veil import NightVeilProbe, attest_cell
from hivemind.pheromone import EphemeralSegments, TrailRecorder
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.deps import WardenLink
from hivemind.queen.placement import Placement, ProvisionVirtual, ReuseDormant, ReuseReal
from waggle.ids import CellId

# A NIGHT_VEIL Cell's own probe factory: builds a fresh NightVeilProbe for the Cell being attested.
# Callable rather than a bare probe instance, since a real SessionNightVeilProbe needs a live
# CellSession bound to *this* Cell, not one shared across every provision (module docstring).
NightVeilProbeFactory = Callable[[Cell], NightVeilProbe]

__all__ = ["LifecycleVirtualCellProvider", "NightVeilProbeFactory"]


class LifecycleVirtualCellProvider:
    """Drive a CellLifecycle to acquire a WardenLink for a Virtual Placement.

    Holds a mutable `_queen` reference, set once by `bind_queen` (module docstring: the same
    late-binding shape `CellListener.start(queen)` already uses, for the identical reason -- this
    provider must exist before `QueenDeps`/`Queen` do).
    """

    def __init__(
        self,
        lifecycle: CellLifecycle,
        gate: QueenReadinessGate,
        trail: TrailRecorder,
        probe_factory: NightVeilProbeFactory,
        night_veil: EphemeralSegments | None = None,
    ) -> None:
        """Build a LifecycleVirtualCellProvider; call `bind_queen` before the first `acquire`.

        Args:
            lifecycle: Drives every Virtual Cell state edge and backend call.
            gate: Resolves once the Cell's own CellReady/CellHeartbeat handshake has verified;
                the same instance the composition root also wires into every registered backend.
            trail: The Queen-side trail-writer identity `attest_cell` records a NIGHT_VEIL Cell's
                own `cell.attested` event with.
            probe_factory: Builds the `NightVeilProbe` a freshly provisioned NIGHT_VEIL Cell is
                attested against (module docstring: never a bare fake, injected by the composition
                root).
            night_veil: The Night Veil boundary's ephemeral segments a task is bound into once it
                is handed a NIGHT_VEIL Cell; None (a Hive with no Virtual side) binds nothing.
        """
        self._lifecycle = lifecycle
        self._gate = gate
        self._trail = trail
        self._probe_factory = probe_factory
        self._night_veil = night_veil
        self._queen: _WardensView | None = None

    def bind_queen(self, queen: _WardensView) -> None:
        """Give this provider a live view of attached Wardens; call once, after `Queen(deps)`.

        Args:
            queen: Anything with a `.wardens` property (`hivemind.queen.queen.Queen` in
                production); typed structurally here so this module never has to import `Queen`
                at type-check time beyond `TYPE_CHECKING` (see `_WardensView` below).
        """
        self._queen = queen

    @property
    def queen(self) -> _WardensView | None:
        """The Queen `bind_queen` bound, or None before that has run.

        Exposed so `hivemind.queen.cell_gate.quiesce.make_quiesce`'s own `queen_getter` can reuse
        this provider's late-bound reference instead of the composition root adding a second
        `bind_queen` call site (`hivemind.cli.compose.hive._assemble_hive`, not in this dispatch's
        allowed-to-fix list): `hivemind.cli.compose.virtual_cells.build_virtual_cells` closes over
        `lambda: provider.queen` before either `provider` or the real `Queen` it will later be
        bound to exists.
        """
        return self._queen

    async def acquire(self, placement: Placement, task: Task) -> WardenLink:
        """Provision or resume the Cell `placement` names, and return its own WardenLink.

        See `hivemind.queen.deps.VirtualCellProvider.acquire` for the full contract.
        """
        if isinstance(placement, ReuseReal):
            # Unreachable in practice: hivemind.queen.dispatcher.acquire resolves a ReuseReal
            # placement itself (`resolve_link`) and never calls this provider for one.
            raise TypeError("LifecycleVirtualCellProvider.acquire got a ReuseReal placement.")
        if isinstance(placement, ProvisionVirtual):
            link = await self._acquire_provision(placement)
        else:
            link = await self._acquire_dormant(placement)
        # Codingrules 12: bound before the dispatcher records this placement, so its records
        # about the task wait in the Cell's segment and keep only their skeleton on the trail.
        if self._night_veil is not None and link.cell.comb_shield is CombShieldLevel.NIGHT_VEIL:
            self._night_veil.bind(task.id, link.cell.id)
        return link

    async def _acquire_provision(self, placement: ProvisionVirtual) -> WardenLink:
        """Provision a fresh Cell from `placement.spec`, wait for it, and return its link."""
        cell = await self._lifecycle.provision(placement.spec, placement.backend)
        try:
            await self._gate.wait_ready(cell.id, placement.spec.ready_timeout_s)
            link = self._require_link(cell.id)
        except Exception as exc:
            await self._teardown_best_effort(cell.id)
            raise CellProvisionError(
                placement.backend, placement.spec.image, f"never became reachable: {exc}"
            ) from exc
        # The task is bound to the tier its link carries, which the Cell announced itself.
        await self._require_provisioned_tier(cell, link, placement)
        if cell.comb_shield is CombShieldLevel.NIGHT_VEIL:
            # ADR-0030: "readiness is attestation of an image, never configuration of a Cell."
            # Runs after the link is found (attest_cell needs nothing from it) but strictly
            # before mark_ready, so a Cell that fails attestation is never handed to placement.
            await self._attest_or_teardown(cell, placement)
        await self._lifecycle.mark_ready(cell.id, link.warden_id)
        return link

    async def _require_provisioned_tier(
        self, cell: Cell, link: WardenLink, placement: ProvisionVirtual
    ) -> None:
        """Tear `cell` down unless its link carries the tier it was provisioned at.

        Raises:
            CellProvisionError: The Cell announced another tier; it is already torn down.
        """
        announced = link.cell.comb_shield
        if announced is cell.comb_shield:
            return
        # Refused, never corrected: the Cell's own view of its tier is what its Warden enforces.
        await self._teardown_best_effort(cell.id)
        raise CellProvisionError(
            placement.backend,
            placement.spec.image,
            f"announced Comb Shield tier {announced.value}, but was provisioned at "
            f"{cell.comb_shield.value}",
        )

    async def _attest_or_teardown(self, cell: Cell, placement: ProvisionVirtual) -> None:
        """Attest `cell` (NIGHT_VEIL only); a red check or a probe failure tears it down.

        Raises:
            CellProvisionError: The probe itself could not run, or attestation recorded at least
                one red check; either way `cell` has already been torn down (best-effort) before
                this raises.
        """
        try:
            probe = self._probe_factory(cell)
            attestation = await attest_cell(probe, self._trail, cell.id)
        except Exception as exc:
            await self._teardown_best_effort(cell.id)
            raise CellProvisionError(
                placement.backend,
                placement.spec.image,
                f"Night Veil attestation could not run: {exc}",
            ) from exc
        if attestation.passed:
            return
        await self._teardown_best_effort(cell.id)
        raise CellProvisionError(
            placement.backend,
            placement.spec.image,
            f"Night Veil attestation failed: {', '.join(sorted(attestation.red))}",
        )

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
