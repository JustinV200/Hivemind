"""Unit tests for hivemind.hive.backends.qemu.runner: value types and vm_dir_for.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/qemu/runner.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.qemu.runner for vm_dir_for, under test.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.hive.backends.qemu.runner import vm_dir_for
from waggle.clock import FakeClock
from waggle.ids import new_cell_id


def test_vm_dir_for_is_deterministic_from_cell_id_alone() -> None:
    root = Path("/vm_root")
    cell_id = new_cell_id(FakeClock())

    assert vm_dir_for(root, cell_id) == vm_dir_for(root, cell_id)
    assert str(cell_id) in str(vm_dir_for(root, cell_id))


def test_vm_dir_for_nests_under_the_given_root() -> None:
    root = Path("/some/vm_root")
    cell_id = new_cell_id(FakeClock())

    assert vm_dir_for(root, cell_id).parent == root
