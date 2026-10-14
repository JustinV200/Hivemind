"""Build `dist/pollen`: the Pollen Packet and the Python it needs, frozen into one executable file.

The Pollen Packet (the thin gateway program an operator installs on a device the Hive will borrow)
must run on a Raspberry Pi OS or a bare Windows machine that has no Python 3.12 and no `pip`, so
roadmap step 11.7 ships it as a single-file binary. This script is the one job that makes it:
it runs PyInstaller over `packages/pollen/src/pollen/__main__.py`, the entry point `python -m
pollen` and the service units `pollen install` writes both use, with the three options a frozen
build needs and a plain import analysis would miss:

    --copy-metadata pollen        so `pollen version` reads the installed version, not a fallback;
    --collect-submodules pollen   (and waggle) so no module loaded by name is left out;
    --exclude-module hivemind     so the binary never carries the Hive, even when the build
                                  environment has it installed (pollen imports waggle and
                                  nothing else from the workspace).

Then it runs the binary it built with `version` and refuses to report success unless that prints
the packet's version line: a binary that cannot start is a failed build, whatever PyInstaller said.
Run it from the repository root in an environment that has the `pollen-build` dependency group:

    uv sync --frozen --group pollen-build
    uv run --frozen python scripts/build_pollen.py [--output-dir DIR] [--name NAME] [--no-smoke]

A binary links the C library of the machine that built it, so the Linux binaries are built inside
a Debian 12 (bookworm) container, whose glibc is the one Raspberry Pi OS ships and the oldest the
packet supports (`.github/workflows/pollen.yml`). On Linux PyInstaller also needs `objdump`
(Debian's `binutils`), which the `python:3.12-bookworm` image has and its `-slim` variant does
not. PyInstaller cannot cross-compile, so each platform is built on that platform.

Codingrules section 4 confines `subprocess` to `scripts/` among other places; this script is one
that legitimately needs it, to run PyInstaller and then the binary it produced.

Fits into the Hive:
    Layer: none (a dev/build-time script, not shipped code). Produces `dist/pollen` (`pollen.exe`
    on Windows), which `.github/workflows/pollen.yml` uploads and which an operator installs on a
    device (docs/runbooks/enrolling-a-device.md).

Key invariants:
    - The build exits 0 only when the binary exists and printed `pollen <version> (waggle
      <protocol>)` for `version`; otherwise it exits 1 with a sentence saying which step failed.
    - Nothing is written outside `--output-dir`: PyInstaller's work files go to a hidden
      directory inside it, so a repository that ignores `dist/` ignores them too.
    - Degrades with an actionable message, never a traceback, when PyInstaller is not installed.

See Also:
    - packages/pollen/src/pollen/__main__.py, the entry point that is frozen.
    - packages/pollen/src/pollen/platform/base.py (`launch_command`) for how the frozen binary
      starts itself as a service.
    - .claude/roadmap.md step 11.7 and .claude/phase-11-handoff.md section 2.6.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = REPO_ROOT / "packages" / "pollen" / "src" / "pollen" / "__main__.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "dist"
DEFAULT_NAME = "pollen"  # The binary's name, the command an operator types.
WORK_DIRECTORY = ".pyinstaller"  # Inside the output directory; hidden so `dist/pollen*` skips it.
PYINSTALLER_LOG_LEVEL = "WARN"  # PyInstaller is chatty at INFO; a build's log should show trouble.
SMOKE_TIMEOUT_S = 60.0  # A cold onefile start unpacks itself first: seconds on a Raspberry Pi.
# What `pollen version` prints (pollen.cli._version): the packet's version and its Waggle protocol.
VERSION_LINE = re.compile(r"^pollen \S+ \(waggle \d+\.\d+\)$")
# The options a frozen build needs beyond the entry point; the module docstring says why.
FREEZE_ARGUMENTS = (
    "--copy-metadata",
    "pollen",
    "--collect-submodules",
    "pollen",
    "--collect-submodules",
    "waggle",
    "--exclude-module",
    "hivemind",
)

# Runs one command to completion within a time limit (None: no limit); returns what it did.
type Runner = Callable[[Sequence[str], float | None], subprocess.CompletedProcess[str]]

__all__ = [
    "BuildOptions",
    "binary_path",
    "build",
    "main",
    "parse_arguments",
    "pyinstaller_command",
]


@dataclass(frozen=True, slots=True)
class BuildOptions:
    """What one build was asked for."""

    output_dir: Path  # Where the binary is written.
    work_dir: Path  # PyInstaller's scratch: its analysis, spec file and build tree.
    name: str  # The binary's name, without the `.exe` Windows adds.
    is_smoke_tested: bool  # Whether the binary is run with `version` once it is built.


def parse_arguments(argv: Sequence[str] | None = None) -> BuildOptions:
    """Read the command line into a `BuildOptions`.

    Args:
        argv: The arguments after the program name; `sys.argv[1:]` when None.

    Returns:
        The options, with the work directory defaulted to a hidden one inside the output
        directory.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Where to write the binary (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument("--name", default=DEFAULT_NAME, help="The binary's name.")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help=f"PyInstaller's scratch directory (default: <output-dir>/{WORK_DIRECTORY}).",
    )
    parser.add_argument(
        "--no-smoke",
        action="store_true",
        help="Do not run the built binary with `version` (a binary built for another machine).",
    )
    args = parser.parse_args(argv)
    work_dir = args.work_dir if args.work_dir is not None else args.output_dir / WORK_DIRECTORY
    return BuildOptions(
        output_dir=args.output_dir,
        work_dir=work_dir,
        name=args.name,
        is_smoke_tested=not args.no_smoke,
    )


def pyinstaller_command(options: BuildOptions, python: str = sys.executable) -> list[str]:
    """Return the argument list that runs PyInstaller for `options`.

    Args:
        options: What to build and where.
        python: The interpreter whose PyInstaller runs; the one running this script by default.

    Returns:
        `python -m PyInstaller` with a single-file build of the packet's entry point.
    """
    return [
        python,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        options.name,
        "--distpath",
        str(options.output_dir),
        "--workpath",
        str(options.work_dir),
        "--specpath",
        str(options.work_dir),
        # --noconfirm: a rebuild replaces the last output; --clean: never reuse a stale analysis.
        "--noconfirm",
        "--clean",
        "--log-level",
        PYINSTALLER_LOG_LEVEL,
        *FREEZE_ARGUMENTS,
        str(ENTRY_POINT),
    ]


def binary_path(options: BuildOptions, platform: str = sys.platform) -> Path:
    """Return where PyInstaller writes the binary: the name, plus `.exe` on Windows.

    Args:
        options: What was built and where.
        platform: A `sys.platform` value; the running one by default.

    Returns:
        The path of the single file.
    """
    suffix = ".exe" if platform == "win32" else ""
    return options.output_dir / f"{options.name}{suffix}"


def build(options: BuildOptions, runner: Runner | None = None) -> int:
    """Build the binary, then (unless told not to) run it once with `version`.

    Args:
        options: What to build and where.
        runner: Runs one command within a time limit and returns its completed process;
            `subprocess.run` with the output captured by default. Tests pass a recorder.

    Returns:
        0 when the binary exists and, if smoke-tested, printed the packet's version line;
        1 otherwise, after printing which step failed.
    """
    run = runner if runner is not None else _run
    options.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Building {binary_path(options)} with PyInstaller ...")
    # No time limit on the freezer itself: a cold build on a Raspberry Pi takes minutes.
    built = run(pyinstaller_command(options), None)
    if built.returncode != 0:
        print(f"PyInstaller failed (exit {built.returncode}):\n{_tail(built)}")
        return 1
    binary = binary_path(options)
    if not binary.is_file():
        print(f"PyInstaller reported success but {binary} does not exist.")
        return 1
    if options.is_smoke_tested:
        return _smoke(binary, run)
    print(f"Built {binary} (not run: --no-smoke).")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the build the command line asks for.

    Args:
        argv: Command-line arguments, excluding the program name. `None` means `sys.argv[1:]`.

    Returns:
        0 on success, 1 on any failed step, matching every other script's exit-code convention.
    """
    options = parse_arguments(argv)
    if importlib.util.find_spec("PyInstaller") is None:
        # Clear, actionable degrade rather than a bare "No module named PyInstaller" traceback.
        print(
            "PyInstaller is not installed in this environment: run "
            "`uv sync --frozen --group pollen-build` first."
        )
        return 1
    return build(options)


def _smoke(binary: Path, run: Runner) -> int:
    """Run the binary with `version` and check it printed the packet's version line."""
    try:
        ran = run([str(binary), "version"], SMOKE_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as error:
        # A binary the machine cannot execute, or one that hangs, is as broken as one that errs.
        print(f"{binary} was built but could not be run: {type(error).__name__}: {error}")
        return 1
    printed = ran.stdout.strip()
    if ran.returncode != 0 or not VERSION_LINE.match(printed):
        print(f"{binary} was built but `version` failed (exit {ran.returncode}):\n{_tail(ran)}")
        return 1
    print(f"Built {binary}: {printed}")
    return 0


def _run(command: Sequence[str], timeout_s: float | None) -> subprocess.CompletedProcess[str]:
    """Run one command to completion with its output captured, never through a shell."""
    # SAFETY: the argument list is this script's own (the interpreter, PyInstaller's options, a
    # path under the output directory); no shell and no caller-supplied string.
    return subprocess.run(  # noqa: S603
        list(command), capture_output=True, text=True, check=False, timeout=timeout_s
    )


def _tail(completed: subprocess.CompletedProcess[str], lines: int = 20) -> str:
    """Return the last lines of what a command wrote, so a failure shows why."""
    text = (completed.stderr or "") + (completed.stdout or "")
    return "\n".join(text.strip().splitlines()[-lines:]) or "(no output)"


if __name__ == "__main__":
    sys.exit(main())
