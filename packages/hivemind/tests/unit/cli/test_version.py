"""Test version collection and formatting for the `hive` CLI's `--version` option.

Fits into the Hive:
    Mirrors src/hivemind/cli/version.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.version for the module under test.
"""

from __future__ import annotations

import platform
from importlib.metadata import version

from hivemind.cli.version import VersionInfo, collect_version_info, format_version


def test_collect_version_info_returns_installed_hivemind_version() -> None:
    # Arrange: the expected value comes straight from the same stdlib call the module wraps, so
    # this checks the wiring rather than duplicating importlib's own behaviour.
    expected = version("hivemind")

    # Act
    info = collect_version_info()

    # Assert
    assert info.hivemind_version == expected


def test_collect_version_info_returns_running_python_version() -> None:
    # Arrange
    expected = platform.python_version()

    # Act
    info = collect_version_info()

    # Assert
    assert info.python_version == expected


def test_format_version_returns_single_line_in_expected_shape() -> None:
    # Arrange: a fixed VersionInfo so the exact output string is deterministic.
    info = VersionInfo(
        hivemind_version="0.1.0.dev0",
        python_version="3.12.14",
        platform_name="Linux-6.8.0",
    )

    # Act
    line = format_version(info)

    # Assert: exact shape, and no trailing newline sneaking in.
    assert line == "hive 0.1.0.dev0 (Python 3.12.14 on Linux-6.8.0)"
    assert "\n" not in line
