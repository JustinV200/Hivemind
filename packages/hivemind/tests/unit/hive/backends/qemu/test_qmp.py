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

import json
import sys
from pathlib import Path

import pytest

from hivemind.hive.backends.qemu.qmp import _build_payload, qmp_execute
from hivemind.hive.backends.qemu.runner import QemuRunnerError


@pytest.mark.skipif(sys.platform != "win32", reason="exercises the Windows-only guard branch")
async def test_qmp_execute_raises_a_clear_error_on_windows(tmp_path: Path) -> None:
    with pytest.raises(QemuRunnerError, match="not supported on Windows"):
        await qmp_execute(tmp_path / "qmp.sock", "quit", subject="cell cell_test")


# ──────────────────────────────────────────────────────────────────────────────
# _build_payload: the wire shape, testable with no socket at all (module docstring).
# ──────────────────────────────────────────────────────────────────────────────


def test_build_payload_omits_arguments_when_none() -> None:
    payload = _build_payload("quit", None)

    assert json.loads(payload) == {"execute": "quit"}
    assert payload.endswith(b"\n")


def test_build_payload_includes_arguments_when_given() -> None:
    payload = _build_payload("human-monitor-command", {"command-line": "savevm snap-1"})

    assert json.loads(payload) == {
        "execute": "human-monitor-command",
        "arguments": {"command-line": "savevm snap-1"},
    }
