"""Provide ProcessQemuRunner: the real QemuRunnerPort, over `qemu-img`/`qemu-system-x86_64`.

`ProcessQemuRunner` is `QemuRunnerPort`'s real implementation, launching `qemu-img` and
`qemu-system-x86_64` as `asyncio` subprocesses (codingrules section 4: `subprocess` is confined to
`hive/backends/*`, and section 11: every blocking or external await runs through `asyncio`, never
a bare thread). It is the only module in this package that shells out to a `qemu-*` binary or an
ISO-building tool; every other module in `hivemind.hive.backends.qemu` sees only `QemuRunnerPort`'s
own value types. Every VM's own state lives in its `vm_dir` (`hivemind.hive.backends.qemu.runner.
vm_dir_for`) as a `cell.json` file -- cell id, hive id, image, labels, created_at, pid -- so
`list_vms` reads the infrastructure itself, never a table this process might not still hold
(ADR-0026: "orphans are recoverable from labels alone").

Scope, stated plainly rather than assumed (this dev host has no QEMU installed, ADR-0026, so this
module has been written carefully and reviewed by reading, never run against a real
`qemu-system-x86_64` -- the same caveat `images/base-ubuntu/Dockerfile` states for itself):

    - VM lifecycle is a plain child process, not `-daemonize`: this module spawns
      `qemu-system-x86_64` directly with `asyncio.create_subprocess_exec` and never awaits its
      exit, so its `pid` is known immediately with no pidfile to poll. `-daemonize` (a POSIX
      `fork()`) is not available on every QEMU build (Windows in particular), so it is deliberately
      not used.
    - Stopping a VM goes through QMP `quit` only (`stop_vm`); a VM whose QMP socket is already
      gone is treated as already stopped (`QemuRunnerPort`'s own idempotency invariant), mirroring
      how `FakeDockerClient.remove_container` treats a missing resource as success. There is no
      SIGTERM/SIGKILL fallback in this dispatch; a VM whose QMP endpoint has wedged needs an
      operator's own `qemu-system-x86_64` process inspection until a later step adds one.
    - QMP runs over a Unix domain socket (`asyncio.open_unix_connection`), which is Linux/macOS
      only -- `asyncio` has no Unix-socket support on Windows, in typeshed's own stub as well as
      at runtime. `stop_vm`/`pause_vm`/`resume_vm` raise `QemuRunnerError` on Windows instead of
      silently no-op-ing; a Windows host needs a TCP QMP endpoint instead, not built in this
      dispatch (see this package's own README's "Not yet built" section).
    - The NoCloud seed image is built by shelling out to `genisoimage` or `mkisofs`, whichever
      `shutil.which` finds first; neither installed raises `QemuRunnerError` with a clear
      install hint, mirroring `hivemind.hive.backends.docker.sdk_client`'s own
      "the tool is missing" message shape.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.qemu`. Implements
    `hivemind.hive.backends.qemu.runner.QemuRunnerPort`; constructed by the composition root (a
    later phase's `cli/`) when `[hive] backend = "qemu"`. Calls into `asyncio.subprocess`, `json`,
    `shutil`, `hivemind.hive.backends.qemu.qmp` (the QMP `stop`/`pause`/`resume` mechanism, split
    out for class size, codingrules 5.1) and `hivemind.hive.backends.qemu.runner` only.

Key invariants:
    - `import subprocess`/`asyncio.subprocess` calls never block the event loop directly: every
      external process is started and awaited through `asyncio.create_subprocess_exec`.
    - `probe_accelerator` never branches on the host OS name (roadmap step 5.11's own requirement):
      it only parses `qemu-system-x86_64 -accel help`'s own reported list against a fixed
      preference order, falling back to `"tcg"` (always available, software-only) when nothing
      stronger is listed.
    - Every `cell.json` this module writes is valid JSON with exactly the fields
      `hivemind.hive.backends.qemu.runner.QemuVmRecord` needs to reconstruct itself; `list_vms`
      never trusts a directory whose `cell.json` fails to parse (it is skipped, not raised on --
      a half-written file from a crashed provision should not break every other Cell's listing).

See Also:
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md for "orphans are recoverable from
      labels alone", the reason cell.json exists at all.
    - hivemind.hive.backends.qemu.runner for QemuRunnerPort, QemuVmSpec, QemuVmHandle,
      QemuVmRecord and QemuRunnerError.
    - hivemind.hive.backends.docker.sdk_client for the "confine the vendor tool to one module"
      pattern this module mirrors for a CLI tool instead of an SDK.
    - scripts/build_cell_image.py for how images/base-ubuntu/vm/base-ubuntu.qcow2, this module's
      own `base_image`, is built.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from hivemind.hive.backends.qemu.qmp import qmp_execute
from hivemind.hive.backends.qemu.runner import (
    QemuRunnerError,
    QemuVmHandle,
    QemuVmRecord,
    QemuVmSpec,
    vm_dir_for,
)
from waggle.ids import CellId, HiveId

# Every VM's own on-disk layout, under its vm_dir (hivemind.hive.backends.qemu.runner.vm_dir_for);
# fixed names so every method can recompute a path from cell_id alone (ADR-0026, mirroring
# hivemind.hive.backends.docker.backend's own container_name/_volume_name).
OVERLAY_DISK_NAME = "overlay.qcow2"
SEED_IMAGE_NAME = "seed.iso"
SERIAL_LOG_NAME = "serial.log"
QMP_SOCKET_NAME = "qmp.sock"
METADATA_NAME = "cell.json"

# In strongest-to-weakest order; probe_accelerator returns the first of these qemu-system-x86_64
# actually lists, or "tcg" (software emulation, always available) if none do.
_ACCELERATOR_PREFERENCE = ("kvm", "whpx", "hvf")
_FALLBACK_ACCELERATOR = "tcg"

# ISO-building tools this module tries, in order; both speak the same genisoimage-compatible flags.
_ISO_TOOL_CANDIDATES = ("genisoimage", "mkisofs")

__all__ = ["ProcessQemuRunner", "probe_accelerator"]


async def probe_accelerator(list_accelerators: Callable[[], Awaitable[str]]) -> str:
    """Return the strongest accelerator `list_accelerators` reports as available.

    Args:
        list_accelerators: Returns `qemu-system-x86_64 -accel help`'s own stdout (or an
            equivalent listing) in production; a test supplies a fake string directly, so this
            function is exercised with no real QEMU present.

    Returns:
        The first of `"kvm"`, `"whpx"`, `"hvf"` that appears in the listing, else `"tcg"`.
    """
    listed = (await list_accelerators()).lower()
    for name in _ACCELERATOR_PREFERENCE:
        if name in listed:
            return name
    return _FALLBACK_ACCELERATOR


class ProcessQemuRunner:
    """QemuRunnerPort over real `qemu-img`/`qemu-system-x86_64`; see the module docstring."""

    def __init__(
        self,
        vm_root: Path,
        *,
        qemu_img_bin: str = "qemu-img",
        qemu_system_bin: str = "qemu-system-x86_64",
    ) -> None:
        """Create a ProcessQemuRunner rooted at `vm_root`.

        Args:
            vm_root: Every VM's own directory (`hivemind.hive.backends.qemu.runner.vm_dir_for`)
                lives under this root; the same value must be given to the
                `QemuCellBackend(..., vm_root=...)` constructed alongside this runner, since
                `list_vms`/`stop_vm`/`pause_vm`/`resume_vm`/`remove_vm_dir`/`read_serial_lines`
                all resolve a VM's directory from `vm_root` and `cell_id` alone.
            qemu_img_bin: The `qemu-img` executable name or path; overridable for a non-PATH
                install.
            qemu_system_bin: The `qemu-system-x86_64` executable name or path.
        """
        self._vm_root = vm_root
        self._qemu_img_bin = qemu_img_bin
        self._qemu_system_bin = qemu_system_bin
        # Probed at most once per runner instance: the host's own accelerator support never
        # changes mid-process, so re-probing on every provision() would only add latency.
        self._accelerator_cache: str | None = None

    async def accelerator(self) -> str:
        """See `QemuRunnerPort.accelerator` (probed once via `qemu-system-x86_64 -accel help`)."""
        if self._accelerator_cache is None:
            self._accelerator_cache = await probe_accelerator(self._list_accelerators)
        return self._accelerator_cache

    async def _list_accelerators(self) -> str:
        """Return `qemu-system-x86_64 -accel help`'s own stdout, or `""` if it could not run."""
        try:
            process = await asyncio.create_subprocess_exec(
                self._qemu_system_bin,
                "-accel",
                "help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
        except OSError:
            return ""  # No accelerator info available: probe_accelerator falls back to "tcg".
        return stdout.decode("utf-8", errors="replace")

    async def create_overlay_disk(
        self, cell_id: CellId, vm_dir: Path, base_image: Path, disk_bytes: int
    ) -> Path:
        """See `QemuRunnerPort.create_overlay_disk` (backed by `qemu-img create -b`)."""
        # mkdir is a blocking filesystem call; codingrules section 11 confines it to a worker
        # thread rather than the event loop, even though it is typically fast.
        await asyncio.to_thread(vm_dir.mkdir, parents=True, exist_ok=True)
        overlay_path = vm_dir / OVERLAY_DISK_NAME
        # -F qcow2 names the backing file's own format explicitly: qemu-img refuses to guess it
        # from a version that started warning about exactly that ambiguity.
        args = (
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            str(base_image),
            str(overlay_path),
            str(disk_bytes),
        )
        await _run(self._qemu_img_bin, args, f"create overlay disk for cell {cell_id!r}")
        return overlay_path

    async def write_seed_image(
        self, cell_id: CellId, vm_dir: Path, user_data: str, meta_data: str
    ) -> Path:
        """See `QemuRunnerPort.write_seed_image` (an ISO9660 volume labelled `cidata`)."""
        # Blocking filesystem calls (codingrules section 11): run on a worker thread.
        await asyncio.to_thread(vm_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((vm_dir / "user-data").write_text, user_data, encoding="utf-8")
        await asyncio.to_thread((vm_dir / "meta-data").write_text, meta_data, encoding="utf-8")
        tool = next((name for name in _ISO_TOOL_CANDIDATES if shutil.which(name)), None)
        if tool is None:
            raise QemuRunnerError(
                f"cannot build a NoCloud seed image for cell {cell_id!r}: neither "
                f"{'nor '.join(_ISO_TOOL_CANDIDATES)} is on PATH; install one "
                "(e.g. `apt-get install genisoimage`)."
            )
        seed_path = vm_dir / SEED_IMAGE_NAME
        args = (
            "-output",
            str(seed_path),
            "-volid",
            "cidata",
            "-joliet",
            "-rock",
            str(vm_dir / "user-data"),
            str(vm_dir / "meta-data"),
        )
        await _run(tool, args, f"write NoCloud seed image for cell {cell_id!r}")
        return seed_path

    async def start_vm(self, spec: QemuVmSpec) -> QemuVmHandle:
        """See `QemuRunnerPort.start_vm` (a plain, non-daemonized child; see module docstring)."""
        args = await self._build_qemu_args(spec)
        try:
            process = await asyncio.create_subprocess_exec(
                self._qemu_system_bin,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise QemuRunnerError(f"could not start {self._qemu_system_bin!r}: {exc}") from exc
        await asyncio.to_thread(_write_metadata, spec, process.pid)
        return QemuVmHandle(cell_id=spec.cell_id, pid=process.pid, vm_dir=spec.vm_dir)

    async def stop_vm(self, cell_id: CellId) -> None:
        """See `QemuRunnerPort.stop_vm` (QMP `quit`; idempotent, see module docstring)."""
        await _send_qmp(self._vm_root, cell_id, "quit")

    async def pause_vm(self, cell_id: CellId) -> None:
        """See `QemuRunnerPort.pause_vm` (QMP `stop`)."""
        await _send_qmp(self._vm_root, cell_id, "stop")
        vm_dir = vm_dir_for(self._vm_root, cell_id)
        await asyncio.to_thread(_update_metadata_paused, vm_dir, paused=True)

    async def resume_vm(self, cell_id: CellId) -> None:
        """See `QemuRunnerPort.resume_vm` (QMP `cont`)."""
        await _send_qmp(self._vm_root, cell_id, "cont")
        vm_dir = vm_dir_for(self._vm_root, cell_id)
        await asyncio.to_thread(_update_metadata_paused, vm_dir, paused=False)

    async def savevm(self, cell_id: CellId, tag: str) -> int:
        """See `QemuRunnerPort.savevm` (QMP `human-monitor-command` running `savevm <tag>`)."""
        await _send_qmp(self._vm_root, cell_id, "human-monitor-command", f"savevm {tag}")
        overlay_path = vm_dir_for(self._vm_root, cell_id) / OVERLAY_DISK_NAME
        return await asyncio.to_thread(_overlay_size_bytes, overlay_path)

    async def loadvm(self, cell_id: CellId, tag: str) -> None:
        """See `QemuRunnerPort.loadvm` (QMP `human-monitor-command` running `loadvm <tag>`)."""
        await _send_qmp(self._vm_root, cell_id, "human-monitor-command", f"loadvm {tag}")

    async def list_vms(self, hive_id: HiveId) -> Sequence[QemuVmRecord]:
        """See `QemuRunnerPort.list_vms` (reads every vm_dir's own cell.json, nothing in memory)."""
        # Every step here is blocking filesystem I/O (codingrules section 11): one worker-thread
        # call reads the whole directory tree rather than hopping to a thread per file.
        return await asyncio.to_thread(self._sync_list_vms, hive_id)

    def _sync_list_vms(self, hive_id: HiveId) -> tuple[QemuVmRecord, ...]:
        """Blocking half of list_vms: runs on a worker thread, never the event loop."""
        if not self._vm_root.is_dir():
            return ()
        vm_dirs = sorted(p for p in self._vm_root.iterdir() if p.is_dir())
        records = (_read_metadata(vm_dir) for vm_dir in vm_dirs)
        return tuple(r for r in records if r is not None and r.hive_id == hive_id)

    async def remove_vm_dir(self, cell_id: CellId) -> None:
        """See `QemuRunnerPort.remove_vm_dir` (idempotent)."""
        # shutil.rmtree is blocking filesystem I/O (codingrules section 11).
        await asyncio.to_thread(
            shutil.rmtree, vm_dir_for(self._vm_root, cell_id), ignore_errors=True
        )

    async def read_serial_lines(self, cell_id: CellId) -> Sequence[str]:
        """See `QemuRunnerPort.read_serial_lines` (reads the -serial file: log in full)."""
        log_path = vm_dir_for(self._vm_root, cell_id) / SERIAL_LOG_NAME
        return await asyncio.to_thread(_sync_read_lines, log_path)

    async def _build_qemu_args(self, spec: QemuVmSpec) -> tuple[str, ...]:
        """Build the full qemu-system-x86_64 argument list for `spec`."""
        memory_mib = max(1, spec.memory_bytes // (1024 * 1024))
        return (
            "-name",
            f"hivemind-cell-{spec.cell_id}",
            "-m",
            str(memory_mib),
            "-smp",
            str(spec.cpu_cores),
            "-accel",
            spec.accelerator,
            "-drive",
            f"file={spec.overlay_disk_path},if=virtio,format=qcow2",
            "-drive",
            f"file={spec.seed_image_path},if=virtio,format=raw,media=cdrom",
            "-netdev",
            spec.netdev_arg,
            "-device",
            "virtio-net-pci,netdev=net0",
            "-serial",
            f"file:{spec.vm_dir / SERIAL_LOG_NAME}",
            "-qmp",
            f"unix:{spec.vm_dir / QMP_SOCKET_NAME},server,nowait",
            "-display",
            "none",
        )


async def _send_qmp(
    vm_root: Path, cell_id: CellId, command: str, command_line: str | None = None
) -> None:
    """Send one QMP `command` to `cell_id`'s own socket under `vm_root`.

    Module-level, not a `ProcessQemuRunner` method (codingrules section 5.1's class-size limit:
    this class was already close to its own 200-line ceiling before roadmap step 5.10 added
    `savevm`/`loadvm`), so every QMP-sending caller (`stop_vm`/`pause_vm`/`resume_vm`/`savevm`/
    `loadvm`) goes through this one function instead of a `self._qmp` method.

    Args:
        vm_root: `ProcessQemuRunner._vm_root`, passed explicitly since this is not a method.
        cell_id: The VM to send the command to.
        command: The QMP command name (`"quit"`, `"stop"`, `"cont"`, `"human-monitor-command"`).
        command_line: For `"human-monitor-command"` only: the HMP line to run (`"savevm <tag>"`);
            None for every fixed command, which takes no arguments.
    """
    socket_path = vm_dir_for(vm_root, cell_id) / QMP_SOCKET_NAME
    arguments = {"command-line": command_line} if command_line is not None else None
    await qmp_execute(socket_path, command, subject=f"cell {cell_id!r}", arguments=arguments)


def _sync_read_lines(log_path: Path) -> tuple[str, ...]:
    """Blocking half of read_serial_lines: a missing log file reads as no lines yet."""
    if not log_path.is_file():
        return ()
    return tuple(log_path.read_text(encoding="utf-8", errors="replace").splitlines())


def _overlay_size_bytes(overlay_path: Path) -> int:
    """Blocking half of savevm's own return value: the overlay disk's current file size.

    0 for a missing overlay (should not happen for a VM this runner started, but savevm's own
    disk-Forage estimate degrading to 0 is safer than raising over a bookkeeping read).
    """
    if not overlay_path.is_file():
        return 0
    return overlay_path.stat().st_size


async def _run(binary: str, args: Sequence[str], subject: str) -> None:
    """Run `binary args` to completion; raise QemuRunnerError naming `subject` on a bad exit."""
    try:
        process = await asyncio.create_subprocess_exec(
            binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
    except OSError as exc:
        raise QemuRunnerError(f"could not {subject}: {binary!r} is not runnable: {exc}") from exc
    if process.returncode != 0:
        raise QemuRunnerError(
            f"could not {subject}: {binary} exited {process.returncode}: "
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )


def _write_metadata(spec: QemuVmSpec, pid: int) -> None:
    """Write this VM's cell.json: the one source list_vms reads back (no in-memory table)."""
    payload = {
        "cell_id": str(spec.cell_id),
        "hive_id": str(spec.hive_id),
        "image": spec.image,
        "labels": dict(spec.labels),
        "created_at": datetime.now(UTC).isoformat(),
        "pid": pid,
        "paused": False,
    }
    (spec.vm_dir / METADATA_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _update_metadata_paused(vm_dir: Path, *, paused: bool) -> None:
    """Flip cell.json's own `paused` flag, if this VM's metadata file still exists."""
    metadata_path = vm_dir / METADATA_NAME
    if not metadata_path.is_file():
        return
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["paused"] = paused
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")


def _read_metadata(vm_dir: Path) -> QemuVmRecord | None:
    """Parse one VM directory's cell.json into a QemuVmRecord, or None if it is missing/invalid."""
    metadata_path = vm_dir / METADATA_NAME
    if not metadata_path.is_file():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return QemuVmRecord(
            cell_id=CellId(payload["cell_id"]),
            hive_id=HiveId(payload["hive_id"]),
            image=payload["image"],
            labels=dict(payload["labels"]),
            created_at=datetime.fromisoformat(payload["created_at"]),
            pid=payload["pid"],
            paused=payload["paused"],
        )
    except (json.JSONDecodeError, KeyError, ValueError):
        # A half-written cell.json from a crashed provision should not break every other Cell's
        # listing (module docstring's own key invariant): skip it, do not raise.
        return None
