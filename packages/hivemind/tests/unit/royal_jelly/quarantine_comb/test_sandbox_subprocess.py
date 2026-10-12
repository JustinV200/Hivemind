"""Test SandboxSubprocess: its environment is scrubbed and a runaway command is killed.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/sandbox_subprocess.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/sandbox_subprocess.py for the module under test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hivemind.royal_jelly.quarantine_comb import SandboxSubprocess
from hivemind.royal_jelly.quarantine_comb.sandbox import TIMED_OUT_EXIT_CODE
from waggle.clock import SystemClock

_PRINT_ENV = "import json, os; print(json.dumps(dict(os.environ)))"


async def test_the_child_sees_a_scrubbed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEMIND_SECRET_FOR_TEST", "do-not-leak")
    sandbox = SandboxSubprocess(SystemClock())

    try:
        result = await sandbox.exec((sys.executable, "-c", _PRINT_ENV), 30.0, {"EXTRA": "yes"})
    finally:
        await sandbox.cleanup()

    seen = json.loads(result.stdout)
    assert "HIVEMIND_SECRET_FOR_TEST" not in seen
    assert seen["HOME"] == str(sandbox.root)
    assert seen["PYTHONNOUSERSITE"] == "1"
    assert seen["PYTHONDONTWRITEBYTECODE"] == "1"
    assert seen["EXTRA"] == "yes"


async def test_a_command_past_its_timeout_is_killed_with_its_children() -> None:
    sandbox = SandboxSubprocess(SystemClock())
    # The child starts a grandchild that would outlive it; the group kill must take both.
    script = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)"
    )

    try:
        result = await sandbox.exec((sys.executable, "-c", script), 1.0)
    finally:
        await sandbox.cleanup()

    assert result.exit_code == TIMED_OUT_EXIT_CODE
    assert result.duration_s < 20


async def test_cleanup_removes_the_temporary_directory() -> None:
    sandbox = SandboxSubprocess(SystemClock())
    await sandbox.put(Path("a/b.txt"), b"x")
    root = sandbox.root

    await sandbox.cleanup()

    assert not root.exists()


def test_the_sandbox_has_no_cell_and_reports_its_interpreter() -> None:
    sandbox = SandboxSubprocess(SystemClock(), python="/usr/bin/python3")

    assert sandbox.cell_id is None
    assert sandbox.interpreter == "/usr/bin/python3"
    assert sandbox.root.exists()
    sandbox.root.rmdir()
