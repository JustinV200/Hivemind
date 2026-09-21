"""Unit tests for hivemind.hive.backends.qemu.qmp: the QMP command sender.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/qemu/qmp.py (codingrules section 3). QMP over a Unix domain socket
    only exists off Windows (that module's own docstring); this dev host is Windows (ADR-0026), so
    the one behaviour actually exercisable here is the platform guard itself -- the POSIX path
    (a real socket, a scripted QMP server) is exercised by
    packages/hivemind/tests/integration/test_qemu_backend.py on a Linux/macOS runner instead.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.qemu.qmp for qmp_execute, under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hivemind.hive.backends.qemu.qmp import qmp_execute
from hivemind.hive.backends.qemu.runner import QemuRunnerError


@pytest.mark.skipif(sys.platform != "win32", reason="exercises the Windows-only guard branch")
async def test_qmp_execute_raises_a_clear_error_on_windows(tmp_path: Path) -> None:
    with pytest.raises(QemuRunnerError, match="not supported on Windows"):
        await qmp_execute(tmp_path / "qmp.sock", "quit", subject="cell cell_test")
