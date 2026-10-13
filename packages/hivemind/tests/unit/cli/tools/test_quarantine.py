"""Tests for hivemind.cli.tools.quarantine: `hive tools quarantine PATH` in the dev sandbox.

Fits into the Hive:
    Mirrors src/hivemind/cli/tools/quarantine.py (codingrules section 3). Drives the real typer app
    over packages built from the Royal Jelly builders and written to a directory (or zipped), so
    the real Quarantine Comb runs its real gates in the real subprocess sandbox. The Comb package
    is another wave's, so the module skips, with the reason, if it cannot be imported.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.tools.quarantine for the module under test.
    - hivemind.royal_jelly.quarantine_comb for the Comb the command drives.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from builders.royal_jelly import make_malicious_files, make_package, make_package_files
from structlog.testing import capture_logs
from typer.testing import CliRunner, Result
from unit.cli.tools.harness import invoke, make_hive

from hivemind.cli.app import app
from hivemind.royal_jelly import SPEC_FILE

pytest.importorskip(
    "hivemind.royal_jelly.quarantine_comb",
    reason="the Quarantine Comb (wave 2D) is not importable in this checkout",
)

_runner = CliRunner()


def _write(directory: Path, files: Mapping[str, bytes]) -> Path:
    """Write a package's files under `directory` and return it."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (directory / name).write_bytes(content)
    return directory


def _quarantine(*args: str) -> Result:
    """Run `hive tools quarantine <args...>` with no manifest and structlog's lines captured."""
    with capture_logs():
        return _runner.invoke(app, ["tools", "quarantine", *args])


def test_a_good_package_directory_passes_every_gate_and_exits_0(tmp_path: Path) -> None:
    package = _write(tmp_path / "echo_tool", make_package_files())

    result = _quarantine(str(package))

    assert result.exit_code == 0, result.output
    assert "development subprocess sandbox" in result.stdout
    assert "quarantine echo_tool 1.0.0: PASSED" in result.stdout
    gates = [line.split() for line in result.stdout.splitlines() if line.startswith("  ")]
    assert [(g[0], g[1]) for g in gates] == [
        ("static", "pass"),
        ("tests", "pass"),
        ("schema", "pass"),
        ("fuzz", "pass"),
        ("resources", "pass"),
        ("capabilities", "pass"),
    ]
    assert "usage: cpu" in result.stdout


def test_a_tool_importing_socket_fails_the_static_gate_and_exits_1(tmp_path: Path) -> None:
    package = _write(tmp_path / "bad_tool", make_malicious_files("socket_import"))

    result = _quarantine(str(package))

    assert result.exit_code == 1
    assert "FAILED" in result.stdout
    (failing,) = [line for line in result.stdout.splitlines() if "FAIL  " in line]
    assert failing.split()[0] == "static" and "socket" in failing
    # The first failing gate ends the run: nothing after `static` is listed.
    assert "tests" not in result.stdout.split("FAILED", 1)[1].split("usage:")[0]


def test_a_zip_package_is_read_as_it_is(tmp_path: Path) -> None:
    archive = tmp_path / "echo_tool.zip"
    archive.write_bytes(make_package())

    result = _quarantine(str(archive), "--limits-json", '{"fuzz_cases": 2}')

    assert result.exit_code == 0, result.output
    assert "PASSED" in result.stdout


def test_a_limit_override_the_comb_would_refuse_is_bad_input(tmp_path: Path) -> None:
    package = _write(tmp_path / "echo_tool", make_package_files())

    result = _quarantine(str(package), "--limits-json", '{"wall_s": 0}')

    assert result.exit_code == 2
    assert "wall_s" in result.stderr
    assert "PASSED" not in result.stdout


@pytest.mark.parametrize("limits", ["not json", "[1, 2]"])
def test_limits_that_are_not_a_json_object_are_bad_input(tmp_path: Path, limits: str) -> None:
    package = _write(tmp_path / "echo_tool", make_package_files())

    result = _quarantine(str(package), "--limits-json", limits)

    assert result.exit_code == 2


def test_a_directory_without_a_spec_is_bad_input_and_never_reaches_the_comb(
    tmp_path: Path,
) -> None:
    files = {name: data for name, data in make_package_files().items() if name != SPEC_FILE}
    package = _write(tmp_path / "no_spec", files)

    result = _quarantine(str(package))

    assert result.exit_code == 2
    assert f"holds no {SPEC_FILE}" in result.stderr
    assert "development subprocess sandbox" not in result.stdout


def test_a_path_that_is_neither_a_directory_nor_a_zip_is_bad_input(tmp_path: Path) -> None:
    result = _quarantine(str(tmp_path / "missing"))

    assert result.exit_code == 2
    assert "neither a directory nor a .zip file" in result.stderr


def test_a_spec_that_is_not_a_tool_spec_is_bad_input(tmp_path: Path) -> None:
    files = {**make_package_files(), SPEC_FILE: b'{"name": "echo_tool"}'}
    package = _write(tmp_path / "bad_spec", files)

    result = _quarantine(str(package))

    assert result.exit_code == 2
    assert "cannot read the package" in result.stderr


def test_a_manifest_supplies_the_limits_and_a_missing_one_is_bad_input(tmp_path: Path) -> None:
    package = _write(tmp_path / "echo_tool", make_package_files())

    with_manifest = invoke(make_hive(tmp_path), "quarantine", str(package))
    with capture_logs():
        missing = _runner.invoke(
            app, ["tools", "--manifest", str(tmp_path / "nope.toml"), "quarantine", str(package)]
        )

    assert with_manifest.exit_code == 0, with_manifest.output
    assert missing.exit_code == 2
    assert "PASSED" not in missing.stdout
