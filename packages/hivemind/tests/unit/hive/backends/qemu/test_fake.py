"""Unit tests for hivemind.hive.backends.qemu.fake: FakeQemuRunner's own extra surface.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/qemu/fake.py (codingrules section 3). QemuCellBackend's own use of
    this fake is covered by test_backend.py; this module covers FakeQemuRunner's own switches,
    idempotency, call recording and readiness-marker seeding directly.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.qemu.fake for FakeQemuRunner, the class under test.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from hivemind.hive.backends.qemu.cloud_init import READINESS_MARKER
from hivemind.hive.backends.qemu.fake import FakeQemuRunner
from hivemind.hive.backends.qemu.runner import QemuRunnerError, QemuVmSpec
from waggle.ids import CellId, HiveId

_CELL_ID = CellId("cell_test")
_HIVE_ID = HiveId("hive_test")

_VM_SPEC = QemuVmSpec(
    cell_id=_CELL_ID,
    hive_id=_HIVE_ID,
    image="base-ubuntu",
    vm_dir=Path("/vm_root/cell_test"),
    overlay_disk_path=Path("/vm_root/cell_test/overlay.qcow2"),
    seed_image_path=Path("/vm_root/cell_test/seed.iso"),
    cpu_cores=2,
    memory_bytes=2 * 1024**3,
    accelerator="tcg",
    netdev_arg="user,id=net0",
    labels={"hive_id": str(_HIVE_ID)},
)


async def test_start_vm_seeds_the_readiness_marker_by_default() -> None:
    runner = FakeQemuRunner()

    await runner.start_vm(_VM_SPEC)

    assert await runner.read_serial_lines(_CELL_ID) == (READINESS_MARKER,)


async def test_set_next_marker_missing_leaves_serial_output_empty() -> None:
    runner = FakeQemuRunner()
    runner.set_next_marker_missing()

    await runner.start_vm(_VM_SPEC)

    assert await runner.read_serial_lines(_CELL_ID) == ()


async def test_set_next_marker_missing_is_one_shot() -> None:
    runner = FakeQemuRunner()
    runner.set_next_marker_missing()
    await runner.start_vm(_VM_SPEC)

    other_spec = replace(_VM_SPEC, cell_id=CellId("cell_other"))
    await runner.start_vm(other_spec)

    assert await runner.read_serial_lines(CellId("cell_other")) == (READINESS_MARKER,)


async def test_set_serial_lines_overrides_whatever_start_vm_seeded() -> None:
    runner = FakeQemuRunner()
    await runner.start_vm(_VM_SPEC)

    runner.set_serial_lines(_CELL_ID, ["booting...", READINESS_MARKER])

    assert await runner.read_serial_lines(_CELL_ID) == ("booting...", READINESS_MARKER)


async def test_create_overlay_disk_failure_is_one_shot() -> None:
    runner = FakeQemuRunner()
    runner.set_create_overlay_disk_failure("disk full")

    with pytest.raises(QemuRunnerError, match="disk full"):
        await runner.create_overlay_disk(
            _CELL_ID, Path("/vm_root/cell_test"), Path("base.qcow2"), 1024
        )

    # The switch cleared itself: a second call succeeds.
    await runner.create_overlay_disk(_CELL_ID, Path("/vm_root/cell_test"), Path("base.qcow2"), 1024)


async def test_stop_vm_is_idempotent_for_an_unknown_cell() -> None:
    runner = FakeQemuRunner()

    await runner.stop_vm(CellId("cell_never_started"))  # Must not raise.


async def test_remove_vm_dir_is_idempotent_and_clears_serial_output_too() -> None:
    runner = FakeQemuRunner()
    await runner.start_vm(_VM_SPEC)

    await runner.remove_vm_dir(_CELL_ID)
    await runner.remove_vm_dir(_CELL_ID)  # A second call is a silent no-op.

    assert await runner.read_serial_lines(_CELL_ID) == ()
    assert await runner.list_vms(_HIVE_ID) == ()


async def test_list_vms_filters_by_hive_id() -> None:
    runner = FakeQemuRunner()
    other_spec = replace(_VM_SPEC, cell_id=CellId("cell_other"), hive_id=HiveId("hive_other"))
    await runner.start_vm(_VM_SPEC)
    await runner.start_vm(other_spec)

    records = await runner.list_vms(_HIVE_ID)

    assert [record.cell_id for record in records] == [_CELL_ID]


async def test_pause_and_resume_toggle_the_paused_flag_list_vms_reports() -> None:
    runner = FakeQemuRunner()
    await runner.start_vm(_VM_SPEC)

    await runner.pause_vm(_CELL_ID)
    assert (await runner.list_vms(_HIVE_ID))[0].paused is True

    await runner.resume_vm(_CELL_ID)
    assert (await runner.list_vms(_HIVE_ID))[0].paused is False


async def test_accelerator_defaults_to_tcg_and_is_overridable() -> None:
    runner = FakeQemuRunner()

    assert await runner.accelerator() == "tcg"

    runner.set_accelerator("kvm")

    assert await runner.accelerator() == "kvm"


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 5.10: savevm / loadvm
# ──────────────────────────────────────────────────────────────────────────────


async def test_savevm_records_the_call_and_returns_the_arranged_size() -> None:
    runner = FakeQemuRunner()
    runner.set_savevm_size_bytes(2048)

    size = await runner.savevm(_CELL_ID, "snap-1")

    assert size == 2048
    assert runner.savevm_calls == [(_CELL_ID, "snap-1")]


async def test_savevm_then_loadvm_of_the_same_tag_does_not_raise() -> None:
    runner = FakeQemuRunner()

    await runner.savevm(_CELL_ID, "snap-1")
    await runner.loadvm(_CELL_ID, "snap-1")  # Must not raise.

    assert runner.loadvm_calls == [(_CELL_ID, "snap-1")]


async def test_loadvm_of_a_tag_never_saved_raises() -> None:
    runner = FakeQemuRunner()

    with pytest.raises(QemuRunnerError):
        await runner.loadvm(_CELL_ID, "never-saved")


async def test_savevm_failure_is_one_shot() -> None:
    runner = FakeQemuRunner()
    runner.set_savevm_failure("qmp timeout")

    with pytest.raises(QemuRunnerError, match="qmp timeout"):
        await runner.savevm(_CELL_ID, "snap-1")

    await runner.savevm(_CELL_ID, "snap-1")  # succeeds the second time


async def test_loadvm_failure_is_one_shot() -> None:
    runner = FakeQemuRunner()
    await runner.savevm(_CELL_ID, "snap-1")
    runner.set_loadvm_failure("qmp timeout")

    with pytest.raises(QemuRunnerError, match="qmp timeout"):
        await runner.loadvm(_CELL_ID, "snap-1")

    await runner.loadvm(_CELL_ID, "snap-1")  # succeeds the second time
