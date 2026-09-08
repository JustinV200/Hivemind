"""Report `hive`'s own version and the Python interpreter it is running under.

The `hive` CLI (the command-line interface that talks to the Queen, the Hive's central
orchestrator) needs a stable, testable way to answer "what build and interpreter am I running?"
without hard-coding a version string that would drift from the installed package. This module
collects that snapshot from the standard library and formats it as the single line `--version`
prints, so the collection logic and the formatting logic can each be tested without a subprocess.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by hivemind.cli.app's `--version` option,
    the CLI's composition root. Calls into the standard library only (importlib.metadata,
    platform); nothing here reads Hive state.

Key invariants:
    - collect_version_info() never raises when `hive` is actually running, because the `hivemind`
      distribution that provides this module is always installed by the time it can be imported.
    - format_version() always returns exactly one line, with no trailing newline.

See Also:
    - hivemind.cli.app for the composition root that prints this.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from importlib.metadata import version

__all__ = ["VersionInfo", "collect_version_info", "format_version"]


@dataclass(frozen=True)
class VersionInfo:
    """Immutable snapshot of the running `hive` build and interpreter.

    Attributes:
        hivemind_version: The installed `hivemind` distribution version, e.g. "0.1.0.dev0".
        python_version: The running interpreter's version, e.g. "3.12.14".
        platform_name: A human-readable platform string, e.g. "Windows-11-10.0.26100-SP0".
    """

    hivemind_version: str
    python_version: str
    platform_name: str


def collect_version_info() -> VersionInfo:
    """Collect the installed hivemind version and the running interpreter's details.

    Returns:
        A VersionInfo built from the installed `hivemind` distribution's metadata and the
        currently running Python interpreter.

    Raises:
        importlib.metadata.PackageNotFoundError: `hivemind` is not installed in the current
            environment. This should never surface through `uv run hive`, which always runs
            inside the workspace venv where the `hivemind` distribution is installed.
    """
    # platform.platform() (not sys.platform) is used for platform_name because it names the OS
    # release, not just the family ("Windows-11-10.0.26100-SP0" vs. bare "win32"), which is what
    # an operator actually wants to see in a bug report.
    return VersionInfo(
        hivemind_version=version("hivemind"),
        python_version=platform.python_version(),
        platform_name=platform.platform(),
    )


def format_version(info: VersionInfo) -> str:
    """Format a VersionInfo as the single line `hive --version` prints.

    Args:
        info: The version snapshot to format.

    Returns:
        Exactly one line, shaped `hive <version> (Python <x.y.z> on <platform>)`, with no
        trailing newline.

    Example:
        >>> format_version(VersionInfo("0.1.0.dev0", "3.12.14", "Linux-6.8.0"))
        'hive 0.1.0.dev0 (Python 3.12.14 on Linux-6.8.0)'
    """
    return f"hive {info.hivemind_version} (Python {info.python_version} on {info.platform_name})"
