"""Provide FakeQemuRunner: an in-memory QemuRunnerPort for tests, demos and hive doctor.

Mirrors `hivemind.hive.backends.docker.fake.FakeDockerClient`'s own shape almost exactly: three
in-memory tables (VMs, their serial output, their pause state) stand in for real `qemu-img`/
`qemu-system-x86_64` processes. Every method records its call and every step has its own one-shot
failure switch (`set_create_overlay_disk_failure`, `set_start_vm_failure`, ...), so a test can
prove `QemuCellBackend.provision`'s all-or-nothing cleanup at each stage without ever touching real
QEMU (this dev host has none, ADR-0026). `start_vm` seeds a freshly started VM's serial console
with `hivemind.hive.backends.qemu.cloud_init.READINESS_MARKER` immediately, so
`QemuCellBackend.provision`'s readiness poll (Clock-only, never `asyncio.timeout`, per that
module's own docstring) finds it on its very first read and never has to await `clock.sleep` --
exactly why the shared contract suite's generic provisioning tests, which know nothing about a
serial marker and never advance a FakeClock themselves, do not deadlock.
`set_next_marker_missing`/`set_serial_lines` are how a QEMU-specific test arranges the opposite, to
prove the timeout path with a clock it controls. Shipped code, not test-only (codingrules 14.4:
"fakes live in src/ beside their Protocol"), because `hive doctor` and demo paths use it too,
exactly like `FakeCellBackend`/`FakeDockerClient`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.qemu`. Implements
    `hivemind.hive.backends.qemu.runner.QemuRunnerPort`; constructed directly by tests, demo
    scripts and `hive doctor`. Calls into hivemind.hive.backends.qemu.cloud_init (READINESS_MARKER
    only) and hivemind.hive.backends.qemu.runner.

Key invariants:
    - `remove_vm_dir`/`stop_vm`/`pause_vm`/`resume_vm` never raise for a `cell_id` not in this
      fake's own table: `QemuRunnerPort`'s own idempotency invariant, honoured here exactly as a
      real runner (an already-gone VM directory, an already-dead process) would be.
    - A `set_*_failure` switch is one-shot per call, not sticky: it fires on the very next matching
      call and is cleared immediately after, mirroring `FakeDockerClient`'s own convention.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code."
    - hivemind.hive.backends.qemu.runner for QemuRunnerPort, QemuVmSpec, QemuVmHandle,
      QemuVmRecord and QemuRunnerError, everything this class implements and raises.
    - hivemind.hive.backends.docker.fake for FakeDockerClient, the pattern this module mirrors.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hivemind.hive.backends.qemu.cloud_init import READINESS_MARKER
from hivemind.hive.backends.qemu.runner import (
    QemuRunnerError,
    QemuVmHandle,
    QemuVmRecord,
    QemuVmSpec,
)
from waggle.ids import CellId, HiveId

__all__ = ["FakeQemuRunner"]

_FAKE_PID_START = 10_000  # Arbitrary but distinct from any real pid this dev host might have.
_DEFAULT_SAVEVM_SIZE_BYTES = 2048  # An arbitrary but deterministic default snapshot size.


@dataclass(slots=True)
class _TrackedVm:
    """This fake's own bookkeeping for one started VM; never exposed outside this file."""

    spec: QemuVmSpec
    pid: int
    created_at: datetime
    paused: bool = False


class FakeQemuRunner:
    """An in-memory QemuRunnerPort: creates, starts, pauses and removes with no real QEMU."""

    def __init__(self) -> None:
        """Create a FakeQemuRunner with nothing created and no failure armed."""
        self._vms: dict[CellId, _TrackedVm] = {}
        self._serial_lines: dict[CellId, list[str]] = {}
        self._next_pid = _FAKE_PID_START
        self._accelerator = "tcg"  # A safe, always-available default; see set_accelerator.
        # Roadmap step 5.10: every tag this fake's savevm has recorded, per VM, so loadvm can
        # raise for one it never saw -- mirrors a real qcow2's own internal snapshot table.
        self._snapshots: dict[CellId, set[str]] = {}
        self._savevm_size_bytes = _DEFAULT_SAVEVM_SIZE_BYTES
        self._savevm_failure: str | None = None
        self._loadvm_failure: str | None = None
        self.savevm_calls: list[tuple[CellId, str]] = []
        self.loadvm_calls: list[tuple[CellId, str]] = []
        # One-shot switch: start_vm normally seeds READINESS_MARKER for whatever Cell it is about
        # to start, so the shared contract suite's own generic provisioning tests never have to
        # know a marker exists (see the module docstring); a test that does know arms this first.
        self._marker_missing_next = False
        # One-shot failure reasons: set_*_failure arms the next matching call, which then clears
        # it (see the module docstring's key invariant).
        self._create_overlay_disk_failure: str | None = None
        self._write_seed_image_failure: str | None = None
        self._start_vm_failure: str | None = None
        self._stop_vm_failure: str | None = None
        self.create_overlay_disk_calls: list[CellId] = []
        self.write_seed_image_calls: list[CellId] = []
        # Each Cell's rendered user-data, so a test can read exactly what the guest would boot.
        self.seed_user_data: dict[CellId, str] = {}
        self.start_vm_calls: list[QemuVmSpec] = []
        self.stop_vm_calls: list[CellId] = []
        self.pause_vm_calls: list[CellId] = []
        self.resume_vm_calls: list[CellId] = []
        self.remove_vm_dir_calls: list[CellId] = []

    def set_create_overlay_disk_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `create_overlay_disk` call to raise."""
        self._create_overlay_disk_failure = reason

    def set_write_seed_image_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `write_seed_image` call to raise."""
        self._write_seed_image_failure = reason

    def set_start_vm_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `start_vm` call to raise."""
        self._start_vm_failure = reason

    def set_stop_vm_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `stop_vm` call to raise."""
        self._stop_vm_failure = reason

    def set_savevm_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `savevm` call to raise."""
        self._savevm_failure = reason

    def set_loadvm_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `loadvm` call to raise."""
        self._loadvm_failure = reason

    def set_savevm_size_bytes(self, size: int) -> None:
        """Arrange what every following `savevm` call reports as its own size estimate."""
        self._savevm_size_bytes = size

    def set_serial_lines(self, cell_id: CellId, lines: Sequence[str]) -> None:
        """Replace whatever `cell_id`'s serial console has "printed" so far.

        Args:
            cell_id: Which VM's serial output to arrange.
            lines: The full line history `read_serial_lines` should now return; typically
                including `hivemind.hive.backends.qemu.cloud_init.READINESS_MARKER` so a
                readiness-wait test's happy path finds it on the very first poll.
        """
        self._serial_lines[cell_id] = list(lines)

    def set_next_marker_missing(self) -> None:
        """Make the next `start_vm` leave its Cell's serial console empty instead of ready.

        A caller (`QemuCellBackend.provision` under test) mints its own `CellId` internally, so a
        test cannot name it in advance the way `set_serial_lines` needs; this is the one-shot
        alternative for exactly that case, mirroring `FakeReadinessGate.set_next_never_ready`. A
        test using this must also drive the test's own `FakeClock.advance` forward, since
        `QemuCellBackend`'s readiness poll waits on the injected Clock, not real time.
        """
        self._marker_missing_next = True

    def set_accelerator(self, name: str) -> None:
        """Arrange what `accelerator()` reports, for a test that cares which value was chosen."""
        self._accelerator = name

    async def accelerator(self) -> str:
        """Return whatever `set_accelerator` last arranged (default `"tcg"`)."""
        return self._accelerator

    async def create_overlay_disk(
        self, cell_id: CellId, vm_dir: Path, base_image: Path, disk_bytes: int
    ) -> Path:
        """Record the call and return a made-up overlay path; see `QemuRunnerPort`."""
        self.create_overlay_disk_calls.append(cell_id)
        _fire(self, "_create_overlay_disk_failure", f"overlay disk for {cell_id!r}")
        return vm_dir / "overlay.qcow2"

    async def write_seed_image(
        self, cell_id: CellId, vm_dir: Path, user_data: str, meta_data: str
    ) -> Path:
        """Record the call and return a made-up seed path; see `QemuRunnerPort`."""
        self.write_seed_image_calls.append(cell_id)
        self.seed_user_data[cell_id] = user_data
        _fire(self, "_write_seed_image_failure", f"seed image for {cell_id!r}")
        return vm_dir / "seed.iso"

    async def start_vm(self, spec: QemuVmSpec) -> QemuVmHandle:
        """Record and "start" `spec`; see `QemuRunnerPort.start_vm`."""
        self.start_vm_calls.append(spec)
        _fire(self, "_start_vm_failure", f"VM for {spec.cell_id!r}")
        pid = self._next_pid
        self._next_pid += 1
        self._vms[spec.cell_id] = _TrackedVm(spec=spec, pid=pid, created_at=datetime.now(UTC))
        # Seed the readiness marker immediately unless a test armed the opposite (see the module
        # docstring: this is what keeps the shared contract suite's generic tests from deadlocking
        # on a FakeClock no one there knows to advance).
        if self._marker_missing_next:
            self._serial_lines[spec.cell_id] = []
            self._marker_missing_next = False
        else:
            self._serial_lines[spec.cell_id] = [READINESS_MARKER]
        return QemuVmHandle(cell_id=spec.cell_id, pid=pid, vm_dir=spec.vm_dir)

    async def stop_vm(self, cell_id: CellId) -> None:
        """Drop `cell_id` from this fake's table; see `QemuRunnerPort.stop_vm` (idempotent)."""
        self.stop_vm_calls.append(cell_id)
        _fire(self, "_stop_vm_failure", f"VM {cell_id!r}")
        self._vms.pop(cell_id, None)

    async def pause_vm(self, cell_id: CellId) -> None:
        """Mark `cell_id` paused; see `QemuRunnerPort.pause_vm` (idempotent)."""
        self.pause_vm_calls.append(cell_id)
        tracked = self._vms.get(cell_id)
        if tracked is not None:
            tracked.paused = True

    async def resume_vm(self, cell_id: CellId) -> None:
        """Mark `cell_id` running; see `QemuRunnerPort.resume_vm` (idempotent)."""
        self.resume_vm_calls.append(cell_id)
        tracked = self._vms.get(cell_id)
        if tracked is not None:
            tracked.paused = False

    async def list_vms(self, hive_id: HiveId) -> Sequence[QemuVmRecord]:
        """Return every tracked VM whose hive_id matches; see `QemuRunnerPort.list_vms`."""
        return tuple(
            QemuVmRecord(
                cell_id=tracked.spec.cell_id,
                hive_id=tracked.spec.hive_id,
                image=tracked.spec.image,
                labels=dict(tracked.spec.labels),
                created_at=tracked.created_at,
                pid=tracked.pid,
                paused=tracked.paused,
            )
            for tracked in self._vms.values()
            if tracked.spec.hive_id == hive_id
        )

    async def remove_vm_dir(self, cell_id: CellId) -> None:
        """Drop every trace of `cell_id`; see `QemuRunnerPort.remove_vm_dir` (idempotent)."""
        self.remove_vm_dir_calls.append(cell_id)
        self._vms.pop(cell_id, None)
        self._serial_lines.pop(cell_id, None)

    async def read_serial_lines(self, cell_id: CellId) -> Sequence[str]:
        """Return whatever `set_serial_lines` last arranged; see `QemuRunnerPort`."""
        return tuple(self._serial_lines.get(cell_id, ()))

    async def savevm(self, cell_id: CellId, tag: str) -> int:
        """Record `tag` as a known snapshot for `cell_id`; see `QemuRunnerPort.savevm`."""
        self.savevm_calls.append((cell_id, tag))
        _fire(self, "_savevm_failure", f"VM {cell_id!r}")
        self._snapshots.setdefault(cell_id, set()).add(tag)
        return self._savevm_size_bytes

    async def loadvm(self, cell_id: CellId, tag: str) -> None:
        """Restore `cell_id` to `tag`; raise if `savevm` never recorded it. See `QemuRunnerPort`."""
        self.loadvm_calls.append((cell_id, tag))
        _fire(self, "_loadvm_failure", f"VM {cell_id!r}")
        if tag not in self._snapshots.get(cell_id, set()):
            raise QemuRunnerError(f"VM {cell_id!r}: no snapshot tagged {tag!r}")


def _fire(runner: FakeQemuRunner, attr: str, subject: str) -> None:
    """Raise QemuRunnerError and clear the one-shot switch named `attr` if it is armed.

    A tiny shared helper so each create/start/stop method above stays a two-line "record, maybe
    fail" pair, mirroring `hivemind.hive.backends.docker.fake`'s own `_fire` helper.
    """
    reason = getattr(runner, attr)
    if reason is not None:
        setattr(runner, attr, None)
        raise QemuRunnerError(f"{subject}: {reason}")
