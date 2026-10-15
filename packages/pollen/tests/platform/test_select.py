"""Test the one place the packet looks at the host: choosing the platform shim.

Everything else in pollen holds a ``PlatformShim`` and never chooses by ``sys.platform``;
``select_shim`` is the single branch on the operating system (gateway-contract section 3). It
takes the platform name as an argument so every choice can be tested on every host.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.select.

Key invariants:
    - ``win32`` selects the Windows shim, ``darwin`` the macOS one and anything else the Linux one.

See Also:
    - pollen.platform for the shims.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pollen.platform import LinuxShim, MacOsShim, PlatformShim, WindowsShim, select_shim


@pytest.mark.parametrize(
    ("name", "shim_type"),
    [("win32", WindowsShim), ("darwin", MacOsShim), ("linux", LinuxShim), ("freebsd14", LinuxShim)],
)
def test_the_platform_name_picks_the_shim(
    name: str, shim_type: type[PlatformShim], tmp_path: Path
) -> None:
    assert isinstance(select_shim(name, tmp_path, {}), shim_type)


def test_by_default_the_running_host_is_asked() -> None:
    shim = select_shim()
    expected = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    assert shim.name == expected
