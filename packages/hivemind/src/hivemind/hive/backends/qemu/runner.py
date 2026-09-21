"""Define QemuRunnerPort: the narrow slice of QEMU QemuCellBackend actually needs.

`QemuCellBackend` (`hivemind.hive.backends.qemu.backend`) never shells out to `qemu-img` or
`qemu-system-x86_64` directly, and never sees a subprocess handle: it calls this Protocol instead,
which two modules implement -- `hivemind.hive.backends.qemu.process_runner.ProcessQemuRunner` (the
real thing, driven by `asyncio` subprocesses, the only module that may launch a `qemu-*` binary)
and `hivemind.hive.backends.qemu.fake.FakeQemuRunner` (in-memory, for tests and `hive doctor`).
Mirrors `hivemind.hive.backends.docker.client.DockerClientPort`'s own shape and reasoning almost
exactly: a narrow Protocol (create an overlay disk, write a NoCloud seed image, start/stop a VM,
pause/resume it, list VMs by label, remove a VM's directory, read its serial console) rather than
wrapping the whole `qemu-img`/`qemu-system-x86_64` command surface, so a fake can implement it
honestly in a few hundred lines and this module documents exactly what `QemuCellBackend` relies on
QEMU for.

The value types below (`QemuVmSpec`, `QemuVmHandle`, `QemuVmRecord`) are this Protocol's own
request/response shapes: frozen dataclasses (codingrules 8.5), not pydantic models, because they
never cross a process, network or file boundary -- they are how
`hivemind.hive.backends.qemu.backend` talks to whichever `QemuRunnerPort` it was given, all inside
one Python process, mirroring `DockerClientPort`'s own choice for the same reason. A real VM's own
on-disk state (per the roadmap: "state persisted per VM in a directory under a configured
`vm_root` with a `cell.json` metadata file") is `ProcessQemuRunner`'s own concern, not this
Protocol's: `QemuVmRecord` is simply what `list_vms` hands back once that file has been read.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.qemu`. Called by
    `hivemind.hive.backends.qemu.backend.QemuCellBackend`; implemented by
    `hivemind.hive.backends.qemu.process_runner.ProcessQemuRunner` and
    `hivemind.hive.backends.qemu.fake.FakeQemuRunner`. Calls into nothing of its own: a plain
    Protocol and its value types carry no logic.

Key invariants:
    - No subprocess handle, file descriptor or `qemu-img`/`qemu-system-x86_64` CLI detail appears
      in this module's signatures: every return value is a plain `str`/`int`/`Path` this backend
      chose, or one of this module's own dataclasses.
    - `remove_vm_dir` is idempotent: removing a VM directory that does not exist returns normally,
      never raises `QemuRunnerError` -- `QemuCellBackend.destroy`'s own idempotency (codingrules
      Appendix A.1) depends on that, and both implementations honour it.
    - `stop_vm`, `pause_vm` and `resume_vm` are idempotent for a VM this runner no longer tracks
      (already stopped, or never started): they return normally rather than raising, mirroring
      `DockerClientPort.remove_container`'s own idempotency for the same reason (the Undertaker
      retries a partial teardown with no way to know which step it reached).
    - `QemuRunnerError` is the only exception any method raises for an operation that genuinely
      failed; `hivemind.hive.backends.qemu.backend` is what translates it into
      `hivemind.hive.errors.CellProvisionError`/`CellDestroyError`, so this Protocol itself stays
      independent of the `hive` package's own error tree.

See Also:
    - hivemind.hive.backends.qemu.process_runner for ProcessQemuRunner, the real implementation.
    - hivemind.hive.backends.qemu.fake for FakeQemuRunner, the in-memory implementation.
    - hivemind.hive.backends.qemu.backend for QemuCellBackend, this Protocol's one caller.
    - hivemind.hive.backends.docker.client for DockerClientPort, the pattern this module mirrors.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from waggle.ids import CellId, HiveId

__all__ = [
    "QemuRunnerError",
    "QemuRunnerPort",
    "QemuVmHandle",
    "QemuVmRecord",
    "QemuVmSpec",
    "vm_dir_for",
]


class QemuRunnerError(Exception):
    """Raised by a QemuRunnerPort implementation when a QEMU operation genuinely fails.

    Never raised for "the VM I was asked to stop/remove does not exist" (see the module
    docstring's idempotency invariant); always raised with a full sentence naming what failed, so
    `hivemind.hive.backends.qemu.backend` can fold it straight into a typed `hive` error message
    without needing to inspect a wrapped subprocess exit code.
    """


@dataclass(frozen=True, slots=True)
class QemuVmSpec:
    """Everything needed to start one VM; QemuCellBackend's own resource-limit mapping.

    Attributes:
        cell_id: This VM's Cell id, chosen by QemuCellBackend so every path below it
            (`vm_dir`, the overlay disk, the seed image) can be recomputed from a `CellId` alone
            with no other state (codingrules Appendix A.1: `destroy` must be idempotent even after
            a process restart).
        hive_id: Which Hive this VM belongs to; stamped into `cell.json` so `list_vms` can filter.
        image: The `VirtualCellSpec.image` this VM was provisioned from, for `cell.json` and
            `QemuVmRecord.image`.
        vm_dir: This VM's own directory under the configured `vm_root`; every file below (the
            overlay disk, the seed image, the serial log, `cell.json`) lives here.
        overlay_disk_path: The per-Cell qcow2 overlay `create_overlay_disk` already wrote.
        seed_image_path: The NoCloud seed image `write_seed_image` already wrote.
        cpu_cores: Logical cores to reserve, mapped onto `-smp` (whole vCPUs: QEMU has no
            fractional-core concept, so QemuCellBackend rounds up before building this spec).
        memory_bytes: Memory to reserve, mapped onto `-m`.
        accelerator: The accelerator name (`"kvm"`, `"whpx"`, `"hvf"` or `"tcg"`) `-accel` should
            use, chosen once by `ProcessQemuRunner`'s own capability probe.
        netdev_arg: The full `-netdev` option value (e.g. `"user,id=net0"`), already built by
            `hivemind.hive.backends.qemu.network.plan_network` for this Cell's `network_policy`.
        labels: Free-form tags to stamp into `cell.json`, hive_id and cell_id included.
    """

    cell_id: CellId
    hive_id: HiveId
    image: str
    vm_dir: Path
    overlay_disk_path: Path
    seed_image_path: Path
    cpu_cores: int
    memory_bytes: int
    accelerator: str
    netdev_arg: str
    labels: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class QemuVmHandle:
    """What `start_vm` hands back once the VM process exists.

    Attributes:
        cell_id: The VM this handle belongs to.
        pid: The `qemu-system-x86_64` process id, read back from `-pidfile` once the (daemonized)
            process has forked; used only for a real implementation's own bookkeeping -- callers
            never send signals to it directly (stop/pause/resume go through this Protocol).
        vm_dir: Where this VM's own files live.
    """

    cell_id: CellId
    pid: int
    vm_dir: Path


@dataclass(frozen=True, slots=True)
class QemuVmRecord:
    """One VM as `list_vms` reports it, read from its own `cell.json`, no other state.

    Mirrors `hivemind.hive.backends.docker.client.ContainerInfo`'s role for Docker, and, once
    handed to `hivemind.hive.backends.qemu.backend`, is turned into a
    `hivemind.hive.backends.base.VirtualCellRecord` the same way `ContainerInfo` is.

    Attributes:
        cell_id: This VM's Cell id.
        hive_id: Which Hive this VM belongs to.
        image: The image it was provisioned from.
        labels: Every label `cell.json` recorded, hive_id and cell_id included.
        created_at: When `cell.json` was first written.
        pid: The `qemu-system-x86_64` process id `cell.json` last recorded.
        paused: Whether `pause_vm` was the last pause/resume call made for this Cell; the roadmap's
            own "no database" design means this is read back from `cell.json`, not asked of QEMU.
    """

    cell_id: CellId
    hive_id: HiveId
    image: str
    labels: Mapping[str, str]
    created_at: datetime
    pid: int
    paused: bool


class QemuRunnerPort(Protocol):
    """The slice of the `qemu-img`/`qemu-system-x86_64` surface QemuCellBackend needs.

    Implementations must be safe to call concurrently: `QemuCellBackend.provision` may run several
    times at once (the Queen may provision several Cells during Swarming).
    """

    async def create_overlay_disk(
        self, cell_id: CellId, vm_dir: Path, base_image: Path, disk_bytes: int
    ) -> Path:
        """Create a copy-on-write overlay disk backed by `base_image` (`qemu-img create -b`).

        Args:
            cell_id: The Cell this overlay belongs to.
            vm_dir: This VM's own directory; the overlay is written somewhere under it.
            base_image: The prebuilt qcow2 (`images/base-ubuntu/vm/base-ubuntu.qcow2`) to back the
                overlay with; never modified.
            disk_bytes: The overlay's own logical size (it starts at near-zero actual disk use;
                this is a ceiling, matching `VirtualCellSpec.disk_bytes`).

        Returns:
            The overlay disk's path.

        Raises:
            QemuRunnerError: `qemu-img` refused or failed, or `base_image` does not exist.
        """
        ...

    async def write_seed_image(
        self, cell_id: CellId, vm_dir: Path, user_data: str, meta_data: str
    ) -> Path:
        """Write a cloud-init NoCloud seed image (labelled `cidata`) holding user-data/meta-data.

        Args:
            cell_id: The Cell this seed belongs to.
            vm_dir: This VM's own directory; the seed image is written somewhere under it.
            user_data: The rendered `user-data` document
                (`hivemind.hive.backends.qemu.cloud_init.render_user_data`).
            meta_data: The rendered `meta-data` document
                (`hivemind.hive.backends.qemu.cloud_init.render_meta_data`).

        Returns:
            The seed image's path.

        Raises:
            QemuRunnerError: The host has no ISO-building tool available (see
                `ProcessQemuRunner`'s own docstring for exactly which ones it tries).
        """
        ...

    async def start_vm(self, spec: QemuVmSpec) -> QemuVmHandle:
        """Start `qemu-system-x86_64` for `spec` and write its own `cell.json`.

        Args:
            spec: Everything the VM needs; every path in it must already exist.

        Returns:
            A handle carrying the VM's own process id.

        Raises:
            QemuRunnerError: The binary is missing, or the process failed to start.
        """
        ...

    async def stop_vm(self, cell_id: CellId) -> None:
        """Stop the VM for `cell_id`. Idempotent: an unknown or already-stopped VM is a no-op.

        Args:
            cell_id: The VM to stop.

        Raises:
            QemuRunnerError: The VM is known to be running but could not be stopped.
        """
        ...

    async def pause_vm(self, cell_id: CellId) -> None:
        """Suspend the VM for `cell_id` via QMP `stop` (Overwintering).

        Args:
            cell_id: The VM to pause.

        Raises:
            QemuRunnerError: The VM is known to be running but could not be paused.
        """
        ...

    async def resume_vm(self, cell_id: CellId) -> None:
        """Wake the VM for `cell_id` via QMP `cont`.

        Args:
            cell_id: The VM to resume.

        Raises:
            QemuRunnerError: The VM is known to be paused but could not be resumed.
        """
        ...

    async def list_vms(self, hive_id: HiveId) -> Sequence[QemuVmRecord]:
        """Return every VM whose `cell.json` carries `hive_id`, read from disk alone.

        Args:
            hive_id: Which Hive to list; matched against each VM's own stamped `hive_id`, never
                against any process-local table (ADR-0026: orphans are recoverable from the
                infrastructure's own state, with no database).

        Returns:
            One QemuVmRecord per VM directory whose `cell.json` exists and matches, in no
            particular order.
        """
        ...

    async def remove_vm_dir(self, cell_id: CellId) -> None:
        """Remove this Cell's whole `vm_dir`. Idempotent: a missing directory is a no-op.

        Args:
            cell_id: The VM whose directory to remove.

        Raises:
            QemuRunnerError: The directory exists but could not be removed.
        """
        ...

    async def accelerator(self) -> str:
        """Return the `-accel` value every VM this runner starts should use.

        Chosen once by probing capability (never by branching on the host OS name, roadmap step
        5.11's own requirement): a real implementation asks `qemu-system-x86_64 -accel help` and
        picks the strongest of `"kvm"`, `"whpx"`, `"hvf"` it actually lists, falling back to
        `"tcg"` (software emulation, always available).

        Returns:
            The accelerator name to pass to `-accel`.
        """
        ...

    async def read_serial_lines(self, cell_id: CellId) -> Sequence[str]:
        """Return every line the VM's serial console has printed so far.

        Args:
            cell_id: The VM whose serial console to read.

        Returns:
            Every line written to the serial log so far, oldest first; an empty sequence for a VM
            that has not printed anything yet (or does not exist).
        """
        ...


def vm_dir_for(vm_root: Path, cell_id: CellId) -> Path:
    """Return this Cell's deterministic VM directory, recomputable with no other state.

    Args:
        vm_root: The configured root every VM directory lives under (`QemuCellBackend`'s own
            `vm_root` constructor argument).
        cell_id: The Cell the directory belongs to.

    Returns:
        A path `destroy()` can recompute from `cell_id` alone (codingrules Appendix A.1:
        `destroy` must be idempotent even after a process restart with no in-memory table left),
        mirroring `hivemind.hive.backends.docker.network.network_name`'s same reasoning.
    """
    return vm_root / str(cell_id)
