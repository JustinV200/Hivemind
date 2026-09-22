"""Provide QemuCellBackend: provision, destroy, pause and list Virtual Cells as real QEMU VMs.

The QEMU backend (ADR-0026: "Docker first and QEMU second", ADR-0027: "connect outbound only,
boot a Warden") is the second working `hivemind.hive.backends.base.CellBackend`, giving
`isolation = "required"` a real hypervisor boundary instead of Docker's container isolation
(`hivemind.hive.backends.docker`'s own module docstring names this exact trade-off). It is built
entirely from four collaborators handed to its constructor -- a
`hivemind.hive.backends.qemu.runner.QemuRunnerPort` (the real `ProcessQemuRunner` or, in every
test, `FakeQemuRunner`), a `hivemind.hive.backends.bootstrap.ReadinessGate`, a `QueenEndpoint`, and
a `Clock` -- mirroring `hivemind.hive.backends.docker.backend.DockerCellBackend`'s own shape almost
exactly, with one addition ADR-0027's "the image's entry point ... boot a Warden" needs for a VM
instead of a container: a VM has no daemon to ask "has the process inside reached the point of
dialling out yet", so `provision` also waits for a fixed line
(`hivemind.hive.backends.qemu.cloud_init.READINESS_MARKER`) on the VM's own serial console before
it ever calls `gate.wait_ready` -- the serial marker proves systemd started the unit; `wait_ready`
proves the Warden actually connected and sent its first Heartbeat (ADR-0027: "provision returns
only after CellReady and the first Heartbeat").

Fits into the Hive:
    Layer 3 (sources of Cells). Implements `hivemind.hive.backends.base.CellBackend`; constructed
    by the composition root (a later phase's `cli/`) when `[hive] backend = "qemu"` and registered
    through `hivemind.hive.registry.BackendRegistry`. Calls into hivemind.cell,
    hivemind.hive.backends.base, hivemind.hive.backends.bootstrap, hivemind.hive.backends.qemu
    (runner, network, cloud_init), hivemind.hive.cell_state, hivemind.hive.errors,
    hivemind.hive.models and waggle only.

Key invariants:
    - provision() either returns a Cell of kind VIRTUAL or raises CellProvisionError; any resource
      already created (overlay disk, seed image, VM process) is removed before the error is
      raised, and the ReadinessGate registration is forgotten too (codingrules Appendix A.1).
    - destroy() is idempotent even after this backend's own process restarted with no in-memory
      state: `hivemind.hive.backends.qemu.runner.vm_dir_for` recomputes every VM's own directory
      from `cell_id` alone, never a table this instance might not still hold.
    - `spec.cpu_cores` is rounded up to a whole vCPU count before it ever reaches
      `hivemind.hive.backends.qemu.runner.QemuVmSpec`: QEMU has no fractional-core concept, unlike
      Docker's `nano_cpus`.

See Also:
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md for the CellBackend contract this
      class implements, and for QEMU giving isolation="required" a real hypervisor boundary.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the readiness
      handshake this backend waits on: the serial marker, then CellReady plus the first Heartbeat.
    - hivemind.hive.backends.qemu.network for exactly what each NetworkPolicy enforces under QEMU
      user-mode networking.
    - hivemind.hive.backends.docker.backend for DockerCellBackend, the pattern this module mirrors.
    - hivemind.hive.backends.qemu.fake and hivemind.hive.backends.fake for the two fakes this
      backend is tested against with no real QEMU or Queen.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import AccessLevel, Cell, CellKind
from hivemind.hive.backends.base import BackendCapabilities, VirtualCellRecord
from hivemind.hive.backends.bootstrap import (
    CellBootstrap,
    CellReadyInfo,
    QueenEndpoint,
    ReadinessGate,
    mint_cell_bootstrap,
)
from hivemind.hive.backends.qemu.cloud_init import (
    READINESS_MARKER,
    render_meta_data,
    render_user_data,
)
from hivemind.hive.backends.qemu.network import plan_network
from hivemind.hive.backends.qemu.runner import (
    QemuRunnerError,
    QemuRunnerPort,
    QemuVmSpec,
    vm_dir_for,
)
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import BackendCapabilityError, CellDestroyError, CellProvisionError
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import Clock
from waggle.ids import CellId, HiveId

_BACKEND_NAME = "qemu"
# One poll of the serial console per this many seconds while waiting for READINESS_MARKER; small
# enough that a fast-booting VM is not held up noticeably, large enough not to hammer the log file.
_SERIAL_POLL_INTERVAL_S = 0.5

__all__ = ["QemuBackendConfig", "QemuCellBackend", "build_qemu_backend"]


@dataclass(frozen=True, slots=True)
class QemuBackendConfig:
    """The three collaborator values `QemuCellBackend` needs beyond runner/gate/endpoint/clock.

    Bundled into one value (codingrules 5.1: "Introduce a frozen dataclass for the argument
    group" once a constructor's own parameter count crosses the limit) rather than three more
    keyword arguments on `QemuCellBackend.__init__`/`build_qemu_backend`.

    Attributes:
        base_image: The prebuilt qcow2 every Cell is backed by (`scripts/build_cell_image.py`'s
            own output). Every provisioned Cell uses this one image regardless of `spec.image`; a
            per-image catalogue is future roadmap work once `desktop-ubuntu`/`night-veil-ubuntu`
            VM images exist.
        vm_root: Every VM's own directory lives under this root; must match the `vm_root` the
            `QemuRunnerPort` handed to the same backend (if it is a `ProcessQemuRunner`) was
            itself constructed with.
        max_cells: The most Cells this backend may hold at once, or None for no cap of its own.
    """

    base_image: Path
    vm_root: Path
    max_cells: int | None = None


class QemuCellBackend:
    """CellBackend over real QEMU VMs, driven entirely through its injected collaborators."""

    def __init__(
        self,
        runner: QemuRunnerPort,
        gate: ReadinessGate,
        endpoint: QueenEndpoint,
        clock: Clock,
        *,
        config: QemuBackendConfig,
    ) -> None:
        """Create a QemuCellBackend with nothing provisioned yet.

        Args:
            runner: How this backend talks to QEMU; `ProcessQemuRunner` for a real host,
                `FakeQemuRunner` for tests. Must be constructed with the same `config.vm_root`
                given here.
            gate: How this backend learns a Cell's Warden has connected and sent its Heartbeat.
            endpoint: Where and who the Queen is, as every provisioned Cell must reach her.
            clock: Source of every minted CellId (`hivemind.hive.backends.bootstrap.
                mint_cell_bootstrap`) and of the readiness-poll interval's own waits.
            config: This backend's own base image, VM directory root and headroom cap.
        """
        self._runner = runner
        self._gate = gate
        self._endpoint = endpoint
        self._clock = clock
        self._config = config
        # In-process bookkeeping only, for `capabilities.headroom`: destroy/list_cells never read
        # this, since they work from the runner's own on-disk state instead (module docstring's
        # own key invariant: destroy survives a process restart).
        self._active_ids: set[CellId] = set()

    @property
    def name(self) -> str:
        """This backend's registry name, "qemu"."""
        return _BACKEND_NAME

    @property
    def runner(self) -> QemuRunnerPort:
        """This backend's own QemuRunnerPort, for `hivemind.hive.snapshot.snapshotter_for`.

        Roadmap step 5.10: the snapshotter factory builds a `QemuSnapshotter` over the exact same
        runner this backend provisions and destroys through, rather than starting a second one.
        """
        return self._runner

    @property
    def capabilities(self) -> BackendCapabilities:
        """QEMU can snapshot (roadmap 5.10) and pause; headroom tracks this instance's count."""
        max_cells = self._config.max_cells
        headroom = None if max_cells is None else max(0, max_cells - len(self._active_ids))
        return BackendCapabilities(can_snapshot=True, can_pause=True, headroom=headroom)

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """See `CellBackend.provision`."""
        self._check_headroom(spec)
        if spec.network_policy is NetworkPolicy.VPN_TOR:
            # Night Veil needs its own image (roadmap 5.3a) and routing (5.7a), neither of which
            # exists yet; refusing here is cheaper than failing partway through provisioning,
            # mirroring DockerCellBackend's own refusal.
            raise CellProvisionError(
                self.name,
                spec.image,
                "VPN_TOR requires the Night Veil image and routing (roadmap steps 5.3a/5.7a), "
                "not yet available to the QEMU backend",
            )
        bootstrap = mint_cell_bootstrap(spec.hive_id, self._endpoint, self._clock)
        # Registered before any infrastructure exists (ADR-0027): the Queen must be able to verify
        # this Cell's very first signed frame the instant the VM's Warden dials out.
        await self._gate.expect(bootstrap.cell_id, bootstrap.public_key_hex)
        try:
            cell = await self._provision_resources(spec, bootstrap)
        except (QemuRunnerError, TimeoutError) as exc:
            await self._gate.forget(bootstrap.cell_id)
            await self._runner.remove_vm_dir(bootstrap.cell_id)
            raise CellProvisionError(self.name, spec.image, str(exc)) from exc
        self._active_ids.add(bootstrap.cell_id)
        return cell

    async def _provision_resources(self, spec: VirtualCellSpec, bootstrap: CellBootstrap) -> Cell:
        """Create the overlay disk, seed image and VM in order; wait for both readiness signals."""
        cell_id = bootstrap.cell_id
        vm_dir = vm_dir_for(self._config.vm_root, cell_id)
        overlay_path = await self._runner.create_overlay_disk(
            cell_id, vm_dir, self._config.base_image, spec.disk_bytes
        )
        plan = plan_network(spec, self._endpoint)
        user_data = render_user_data(bootstrap, queen_waggle_url_override=plan.queen_waggle_url)
        meta_data = render_meta_data(bootstrap)
        seed_path = await self._runner.write_seed_image(cell_id, vm_dir, user_data, meta_data)
        vm_spec = QemuVmSpec(
            cell_id=cell_id,
            hive_id=spec.hive_id,
            image=spec.image,
            vm_dir=vm_dir,
            overlay_disk_path=overlay_path,
            seed_image_path=seed_path,
            cpu_cores=math.ceil(spec.cpu_cores),
            memory_bytes=spec.memory_bytes,
            accelerator=await self._runner.accelerator(),
            netdev_arg=plan.netdev_arg,
            labels={**spec.labels, "hive_id": str(spec.hive_id)},
        )
        await self._runner.start_vm(vm_spec)
        await self._wait_for_serial_marker(cell_id, spec.ready_timeout_s)
        # No timeout wrapper here, matching DockerCellBackend: wait_ready's own timeout_s argument
        # is the deadline (its contract: raises TimeoutError past it).
        ready_info = await self._gate.wait_ready(cell_id, spec.ready_timeout_s)
        return _build_cell(spec, cell_id, self.name, ready_info)

    async def _wait_for_serial_marker(self, cell_id: CellId, timeout_s: float) -> None:
        """Poll the VM's serial console, via the Clock only, until READINESS_MARKER appears.

        Deadline math runs entirely on `self._clock.monotonic()`/`self._clock.sleep()`, never
        `asyncio.timeout`: the latter measures real wall-clock time, which would never elapse
        under a test's `FakeClock` (codingrules section 11: every external wait goes through the
        injected Clock).
        """
        deadline = self._clock.monotonic() + timeout_s
        while True:
            lines = await self._runner.read_serial_lines(cell_id)
            if any(READINESS_MARKER in line for line in lines):
                return
            if self._clock.monotonic() >= deadline:
                raise TimeoutError(
                    f"cell {cell_id!r} never printed the readiness marker to its serial console "
                    f"within {timeout_s}s"
                )
            await self._clock.sleep(_SERIAL_POLL_INTERVAL_S)

    def _check_headroom(self, spec: VirtualCellSpec) -> None:
        """Raise CellProvisionError before creating anything if this backend is already full."""
        max_cells = self._config.max_cells
        if max_cells is not None and len(self._active_ids) >= max_cells:
            raise CellProvisionError(self.name, spec.image, f"at its headroom of {max_cells} cells")

    async def destroy(self, cell_id: CellId) -> None:
        """See `CellBackend.destroy` (idempotent)."""
        try:
            await self._runner.stop_vm(cell_id)
            await self._runner.remove_vm_dir(cell_id)
        except QemuRunnerError as exc:
            raise CellDestroyError(self.name, cell_id, str(exc)) from exc
        self._active_ids.discard(cell_id)
        await self._gate.forget(cell_id)

    async def list_cells(self, hive_id: HiveId) -> Sequence[VirtualCellRecord]:
        """See `CellBackend.list_cells`: reads the runner's own on-disk state, nothing held here."""
        records = await self._runner.list_vms(hive_id)
        return tuple(
            VirtualCellRecord(
                cell_id=record.cell_id,
                status=VirtualCellStatus.DORMANT if record.paused else VirtualCellStatus.READY,
                image=record.image,
                labels=dict(record.labels),
                created_at=record.created_at,
            )
            for record in records
        )

    async def pause(self, cell_id: CellId) -> None:
        """See `CellBackend.pause`."""
        if not self.capabilities.can_pause:
            raise BackendCapabilityError(self.name, "pause", cell_id=cell_id)
        await self._runner.pause_vm(cell_id)

    async def resume(self, cell_id: CellId) -> None:
        """See `CellBackend.resume`."""
        if not self.capabilities.can_pause:
            raise BackendCapabilityError(self.name, "resume", cell_id=cell_id)
        await self._runner.resume_vm(cell_id)


def build_qemu_backend(
    runner: QemuRunnerPort,
    gate: ReadinessGate,
    endpoint: QueenEndpoint,
    clock: Clock,
    *,
    config: QemuBackendConfig,
) -> Callable[[], QemuCellBackend]:
    """Close over this backend's collaborators and return a zero-arg factory for the registry.

    Mirrors `hivemind.hive.backends.docker.backend.build_docker_backend`'s own reasoning:
    `hivemind.hive.registry.BackendRegistry.register` takes a zero-argument factory, because the
    registry itself never constructs collaborators (codingrules 8.2).

    Args:
        runner: How the backend talks to QEMU.
        gate: How the backend learns a Cell has become reachable.
        endpoint: Where and who the Queen is.
        clock: Source of every minted CellId.
        config: This backend's own base image, VM directory root and headroom cap.

    Returns:
        A callable that builds a fresh `QemuCellBackend` from the given collaborators each time it
        is called; `BackendRegistry.get` calls it at most once and caches the result.
    """

    def factory() -> QemuCellBackend:
        return QemuCellBackend(runner, gate, endpoint, clock, config=config)

    return factory


def _build_cell(
    spec: VirtualCellSpec, cell_id: CellId, source: str, ready_info: CellReadyInfo
) -> Cell:
    """Build the VIRTUAL, FULL-access Cell a successfully provisioned `spec` reports."""
    return Cell(
        id=cell_id,
        kind=CellKind.VIRTUAL,
        name=spec.image,
        source=source,
        capabilities=ready_info.capabilities,
        capacity=ready_info.capacity,
        access_level=AccessLevel.FULL,
        comb_shield=spec.comb_shield,
    )
