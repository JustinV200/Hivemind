"""Unit tests for hivemind.hive.backends.qemu.process_runner: probe_accelerator and disk-free bits.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/qemu/process_runner.py (codingrules section 3). `ProcessQemuRunner`
    itself needs a real `qemu-img`/`qemu-system-x86_64` for most of its own methods, which this
    dev host has none of (ADR-0026); this module covers what is testable with no real QEMU:
    `probe_accelerator`'s pure parsing logic (dependency-injected, per that function's own
    docstring) and `ProcessQemuRunner.list_vms`/`read_serial_lines` against a real temp directory,
    which need only the filesystem, not QEMU itself. The rest is exercised by
    packages/hivemind/tests/integration/test_qemu_backend.py, marked integration and skipped here.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.qemu.process_runner for probe_accelerator and ProcessQemuRunner.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from hivemind.hive.backends.qemu.process_runner import (
    OVERLAY_DISK_NAME,
    ProcessQemuRunner,
    _overlay_size_bytes,
    _read_qmp_port,
    _write_metadata,
    probe_accelerator,
)
from hivemind.hive.backends.qemu.runner import QemuRunnerError, QemuVmSpec
from waggle.ids import CellId, HiveId


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ("Accelerators supported in QEMU binary:\nkvm\ntcg\n", "kvm"),
        ("whpx\ntcg\n", "whpx"),
        ("hvf\ntcg\n", "hvf"),
        ("tcg\n", "tcg"),
        ("", "tcg"),  # No listing at all (the binary could not run): the always-available fallback.
    ],
)
async def test_probe_accelerator_prefers_the_strongest_one_listed(
    listing: str, expected: str
) -> None:
    async def list_accelerators() -> str:
        return listing

    assert await probe_accelerator(list_accelerators) == expected


async def test_probe_accelerator_prefers_kvm_over_a_weaker_one_also_listed() -> None:
    async def list_accelerators() -> str:
        return "tcg\nkvm\nwhpx\n"  # Listed weakest-first; the function must not just take index 0.

    assert await probe_accelerator(list_accelerators) == "kvm"


async def test_list_vms_reads_cell_json_from_a_real_directory(tmp_path: Path) -> None:
    hive_id = HiveId("hive_test")
    vm_dir = tmp_path / "cell_test"
    vm_dir.mkdir()
    (vm_dir / "cell.json").write_text(
        json.dumps(
            {
                "cell_id": "cell_test",
                "hive_id": str(hive_id),
                "image": "base-ubuntu",
                "labels": {"hive_id": str(hive_id)},
                "created_at": "2024-01-01T00:00:00+00:00",
                "pid": 1234,
                "paused": False,
            }
        ),
        encoding="utf-8",
    )
    runner = ProcessQemuRunner(tmp_path)

    records = await runner.list_vms(hive_id)

    assert len(records) == 1
    assert records[0].cell_id == "cell_test"
    assert records[0].pid == 1234


async def test_list_vms_skips_a_directory_with_no_cell_json(tmp_path: Path) -> None:
    (tmp_path / "not_a_vm").mkdir()
    runner = ProcessQemuRunner(tmp_path)

    assert await runner.list_vms(HiveId("hive_test")) == ()


async def test_list_vms_skips_a_directory_with_malformed_cell_json(tmp_path: Path) -> None:
    vm_dir = tmp_path / "cell_broken"
    vm_dir.mkdir()
    (vm_dir / "cell.json").write_text("not valid json", encoding="utf-8")
    runner = ProcessQemuRunner(tmp_path)

    # A half-written cell.json from a crashed provision must not break the whole listing.
    assert await runner.list_vms(HiveId("hive_test")) == ()


async def test_list_vms_on_a_missing_vm_root_returns_empty(tmp_path: Path) -> None:
    runner = ProcessQemuRunner(tmp_path / "never_created")

    assert await runner.list_vms(HiveId("hive_test")) == ()


async def test_read_serial_lines_on_a_missing_log_returns_empty(tmp_path: Path) -> None:
    runner = ProcessQemuRunner(tmp_path)

    assert await runner.read_serial_lines(CellId("cell_never_started")) == ()


async def test_read_serial_lines_splits_the_log_file_into_lines(tmp_path: Path) -> None:
    cell_id = CellId("cell_test")
    vm_dir = tmp_path / str(cell_id)
    vm_dir.mkdir()
    (vm_dir / "serial.log").write_text("line one\nline two\n", encoding="utf-8")
    runner = ProcessQemuRunner(tmp_path)

    assert await runner.read_serial_lines(cell_id) == ("line one", "line two")


async def test_remove_vm_dir_on_a_missing_directory_is_a_silent_no_op(tmp_path: Path) -> None:
    runner = ProcessQemuRunner(tmp_path)

    await runner.remove_vm_dir(CellId("cell_never_started"))  # Must not raise.


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 5.10: savevm / loadvm's own disk-estimate helper, and the Windows QMP guard.
# ──────────────────────────────────────────────────────────────────────────────


def test_overlay_size_bytes_on_a_missing_file_is_zero(tmp_path: Path) -> None:
    assert _overlay_size_bytes(tmp_path / "never_created.qcow2") == 0


def test_overlay_size_bytes_reads_the_real_file_size(tmp_path: Path) -> None:
    overlay = tmp_path / OVERLAY_DISK_NAME
    overlay.write_bytes(b"x" * 1234)

    assert _overlay_size_bytes(overlay) == 1234


async def test_savevm_on_a_never_started_vm_raises_no_transport_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A never-started VM has no persisted qmp_port, so this falls back to a Unix address.

    On a host with no Unix-socket transport (forced here via monkeypatch so the assertion holds on
    every platform this suite runs on, mirroring hivemind.hive.backends.qemu.qmp's own probe-based
    tests), that has nothing left to try and raises instead of silently no-op-ing.
    """
    monkeypatch.delattr(asyncio, "open_unix_connection", raising=False)
    runner = ProcessQemuRunner(tmp_path)

    with pytest.raises(QemuRunnerError, match="no Unix-socket"):
        await runner.savevm(CellId("cell_test"), "snap-1")


async def test_loadvm_on_a_never_started_vm_raises_no_transport_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(asyncio, "open_unix_connection", raising=False)
    runner = ProcessQemuRunner(tmp_path)

    with pytest.raises(QemuRunnerError, match="no Unix-socket"):
        await runner.loadvm(CellId("cell_test"), "snap-1")


async def test_start_vm_in_tcp_qmp_mode_persists_the_port_for_a_later_send(tmp_path: Path) -> None:
    """A runner forced into TCP-QMP mode writes a real qmp_port a later call can read back.

    Exercises _write_metadata/_read_qmp_port's own round trip with no real qemu-system-x86_64
    needed: start_vm's own subprocess launch is what actually needs the real binary, so this test
    calls the persistence helpers directly instead, mirroring how this module's other tests avoid
    needing a real QEMU install (module docstring).
    """
    vm_dir = tmp_path / "cell_test"
    vm_dir.mkdir()
    spec = QemuVmSpec(
        cell_id=CellId("cell_test"),
        hive_id=HiveId("hive_test"),
        image="base-ubuntu",
        vm_dir=vm_dir,
        overlay_disk_path=vm_dir / "overlay.qcow2",
        seed_image_path=vm_dir / "seed.iso",
        cpu_cores=1,
        memory_bytes=512 * 1024 * 1024,
        accelerator="tcg",
        netdev_arg="user,id=net0",
        labels={},
    )

    _write_metadata(spec, pid=4321, qmp_port=55123)

    assert _read_qmp_port(vm_dir) == 55123


def test_read_qmp_port_on_a_missing_metadata_file_is_none(tmp_path: Path) -> None:
    assert _read_qmp_port(tmp_path / "never_created") is None
