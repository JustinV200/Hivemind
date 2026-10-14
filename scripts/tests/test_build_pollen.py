"""Unit tests for scripts/build_pollen.py.

Fits into the Hive:
    Layer: none (tests for a dev-time build script). PyInstaller itself takes half a minute and
    belongs to the packaging workflow and to a real build run by hand, so what is tested here is
    everything around it: how the command line becomes options, how the options become the
    freezer's argument list (the three options a frozen build needs, the entry point, where the
    output goes), where the binary lands on each platform, and how `build` decides it succeeded,
    with a recording runner standing in for the freezer and for the binary it would have made.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/build_pollen.py, the module under test.
    - .github/workflows/pollen.yml, which runs the real build on every platform.
"""

import importlib.util
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import build_pollen
import pytest
from build_pollen import (
    ENTRY_POINT,
    FREEZE_ARGUMENTS,
    BuildOptions,
    binary_path,
    build,
    main,
    parse_arguments,
    pyinstaller_command,
)

_VERSION = "pollen 0.1.0.dev0 (waggle 1.13)"  # What `pollen version` prints.


@dataclass(frozen=True, slots=True)
class _Script:
    """How the freezer and the binary it builds answer; the defaults are a build that works."""

    freezer_exit: int = 0  # PyInstaller's exit code.
    makes_binary: bool = True  # Whether PyInstaller writes the file it was asked for.
    version_exit: int = 0  # The binary's exit code for `version`.
    version_output: str = _VERSION  # What the binary prints for `version`.
    version_error: Exception | None = None  # Raised instead of an answer, when set.


class _Runner:
    """A recording stand-in for the freezer and for the binary it builds.

    The first call is PyInstaller's: it answers `freezer_exit` and, when `makes_binary`, writes the
    file PyInstaller would have written. Any later call is the binary running: it answers
    `version_exit` and prints `version_output`, or raises `version_error`.
    """

    def __init__(self, options: BuildOptions, script: _Script = _Script()) -> None:  # noqa: B008
        """Script the two kinds of call; a frozen `_Script` is safe as a default."""
        self.options = options
        self.script = script
        self.calls: list[tuple[list[str], float | None]] = []

    def __call__(
        self, command: Sequence[str], timeout_s: float | None
    ) -> subprocess.CompletedProcess[str]:
        """Record the call and answer as scripted."""
        self.calls.append((list(command), timeout_s))
        script = self.script
        if len(self.calls) == 1:
            if script.makes_binary:
                binary_path(self.options).write_bytes(b"\x7fELF")
            return subprocess.CompletedProcess(
                command, script.freezer_exit, "", "freezer said this"
            )
        if script.version_error is not None:
            raise script.version_error
        return subprocess.CompletedProcess(command, script.version_exit, script.version_output, "")


def _options(tmp_path: Path, *, smoke: bool = True) -> BuildOptions:
    """Options for a build into a temporary directory."""
    return BuildOptions(
        output_dir=tmp_path / "dist",
        work_dir=tmp_path / "dist" / ".pyinstaller",
        name="pollen",
        is_smoke_tested=smoke,
    )


def test_parse_arguments_defaults_to_dist_a_binary_called_pollen_and_a_smoke_run() -> None:
    options = parse_arguments([])

    assert options.output_dir == build_pollen.DEFAULT_OUTPUT_DIR
    assert options.name == "pollen" and options.is_smoke_tested is True
    # The scratch is inside the output directory, which a repository ignores whole.
    assert options.work_dir == options.output_dir / ".pyinstaller"


def test_parse_arguments_takes_each_option_and_defaults_the_scratch_beside_the_output(
    tmp_path: Path,
) -> None:
    options = parse_arguments(["--output-dir", str(tmp_path), "--name", "pollen-x", "--no-smoke"])

    assert options.output_dir == tmp_path and options.name == "pollen-x"
    assert options.is_smoke_tested is False and options.work_dir == tmp_path / ".pyinstaller"
    elsewhere = parse_arguments(["--work-dir", str(tmp_path / "scratch")])
    assert elsewhere.work_dir == tmp_path / "scratch"


def test_parse_arguments_refuses_an_option_it_does_not_have() -> None:
    with pytest.raises(SystemExit) as refused:
        parse_arguments(["--onedir"])

    assert refused.value.code == 2


def test_the_freezer_command_builds_one_file_from_the_packets_own_entry_point(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path)

    command = pyinstaller_command(options, python="/usr/bin/python3")

    assert command[:3] == ["/usr/bin/python3", "-m", "PyInstaller"]
    assert "--onefile" in command and command[command.index("--name") + 1] == "pollen"
    assert command[command.index("--distpath") + 1] == str(options.output_dir)
    assert command[command.index("--workpath") + 1] == str(options.work_dir)
    assert command[command.index("--specpath") + 1] == str(options.work_dir)
    assert command[-1] == str(ENTRY_POINT) and ENTRY_POINT.is_file()
    assert ENTRY_POINT.name == "__main__.py" and ENTRY_POINT.parent.name == "pollen"


def test_the_freezer_command_carries_the_three_options_a_frozen_build_needs() -> None:
    command = pyinstaller_command(_options(Path("out")))

    pairs = list(pairwise(command))
    # Metadata so `pollen version` is real; both packages whole; and never the Hive.
    assert ("--copy-metadata", "pollen") in pairs
    assert ("--collect-submodules", "pollen") in pairs
    assert ("--collect-submodules", "waggle") in pairs
    assert ("--exclude-module", "hivemind") in pairs
    assert all(argument in command for argument in FREEZE_ARGUMENTS)
    assert "--noconfirm" in command and "--clean" in command


@pytest.mark.parametrize(
    ("platform", "file_name"),
    [("linux", "pollen"), ("darwin", "pollen"), ("win32", "pollen.exe")],
)
def test_the_binary_is_named_for_its_platform(
    tmp_path: Path, platform: str, file_name: str
) -> None:
    assert binary_path(_options(tmp_path), platform) == tmp_path / "dist" / file_name


def test_a_build_runs_the_freezer_then_the_binary_and_reports_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    options = _options(tmp_path)
    runner = _Runner(options)

    code = build(options, runner)

    assert code == 0
    freezer, smoke = runner.calls
    assert freezer[0][:3] == [sys.executable, "-m", "PyInstaller"]
    assert freezer[1] is None  # The freezer is not hurried: a Raspberry Pi takes minutes.
    assert smoke == ([str(binary_path(options)), "version"], build_pollen.SMOKE_TIMEOUT_S)
    assert _VERSION in capsys.readouterr().out


def test_a_build_without_the_smoke_run_stops_after_the_freezer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    options = _options(tmp_path, smoke=False)
    runner = _Runner(options)

    code = build(options, runner)

    assert code == 0 and len(runner.calls) == 1
    assert "not run: --no-smoke" in capsys.readouterr().out


def test_a_freezer_that_fails_fails_the_build_and_shows_what_it_said(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    options = _options(tmp_path)
    runner = _Runner(options, _Script(freezer_exit=1, makes_binary=False))

    code = build(options, runner)

    assert code == 1 and len(runner.calls) == 1
    shown = capsys.readouterr().out
    assert "PyInstaller failed (exit 1)" in shown and "freezer said this" in shown


def test_a_freezer_that_succeeds_without_writing_the_binary_fails_the_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    options = _options(tmp_path)

    code = build(options, _Runner(options, _Script(makes_binary=False)))

    assert code == 1 and "does not exist" in capsys.readouterr().out


@pytest.mark.parametrize(
    "script",
    [
        _Script(version_exit=1, version_output=""),
        _Script(version_output="pollen: something else"),
        _Script(version_output=""),
    ],
)
def test_a_binary_that_does_not_print_the_version_line_fails_the_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], script: _Script
) -> None:
    options = _options(tmp_path)

    code = build(options, _Runner(options, script))

    assert code == 1 and "`version` failed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired(["pollen", "version"], 60.0), OSError("Exec format error")]
)
def test_a_binary_that_cannot_be_run_or_hangs_fails_the_build_with_a_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    options = _options(tmp_path)

    code = build(options, _Runner(options, _Script(version_error=error)))

    assert code == 1 and "could not be run" in capsys.readouterr().out


def test_main_says_how_to_install_the_freezer_when_it_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)

    code = main(["--no-smoke"])

    assert code == 1
    assert "uv sync --frozen --group pollen-build" in capsys.readouterr().out
