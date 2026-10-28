"""Name the platforms a Pollen Packet is built for, and find the cached pollen build for each.

The pollen binary is one file per platform, built by `scripts/build_pollen.py` on that platform
(PyInstaller cannot cross-compile) or downloaded from the Pollen workflow's artifact of the same
name. `hive swarm packet` does not build it: building needs a subprocess and the platform itself,
neither of which the Hive's CLI may assume (codingrules section 4). It looks for a cached build at
`<[swarm.packet] pollen_dir>/<platform>/<binary>`, and when there is none it says exactly which
command puts one there. Each platform also names the installer script its packet carries: a
PowerShell script on Windows, a POSIX shell script elsewhere.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.packet`.
    Called by `hive swarm packet` (to parse `--platform` and find the binary) and by
    `hivemind.swarm.packet.writer` (for the file names). Calls into the standard library only.

Key invariants:
    - The platform names are the Pollen workflow's artifact suffixes, so a downloaded artifact
      unzips into the folder of the same name.
    - A missing build is a `PacketError` before anything is minted.

See Also:
    - scripts/build_pollen.py for the build, and .github/workflows/pollen.yml for the artifacts.
    - docs/runbooks/enrolling-a-device.md section 1 for which artifact fits which device.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from hivemind.swarm.errors import PacketError

__all__ = ["PacketPlatform", "find_pollen_build"]


class PacketPlatform(StrEnum):
    """A platform a packet can be built for; the value is the artifact's suffix."""

    WINDOWS_X86_64 = "windows-x86_64"
    LINUX_X86_64 = "linux-x86_64"
    LINUX_ARM64 = "linux-arm64"
    MACOS_ARM64 = "macos-arm64"
    MACOS_X86_64 = "macos-x86_64"

    @property
    def is_windows(self) -> bool:
        """Whether the packet runs on Windows: `pollen.exe` and a PowerShell installer."""
        return self is PacketPlatform.WINDOWS_X86_64

    @property
    def binary_name(self) -> str:
        """The pollen binary's file name on this platform."""
        return "pollen.exe" if self.is_windows else "pollen"

    @property
    def installer_name(self) -> str:
        """The installer script the packet carries for this platform."""
        return "pollinate.ps1" if self.is_windows else "pollinate.sh"


def find_pollen_build(pollen_dir: Path, platform: PacketPlatform) -> Path:
    """Return the cached pollen build for `platform` under `pollen_dir`.

    Args:
        pollen_dir: `[swarm.packet] pollen_dir`, already resolved against the manifest's folder.
        platform: The platform the packet is for.

    Returns:
        The binary's path.

    Raises:
        PacketError: There is no build there; the message names the command that makes one.
    """
    binary = pollen_dir / platform.value / platform.binary_name
    if binary.is_file():
        return binary
    raise PacketError(
        f"No pollen build for {platform.value} at {binary}. On a {platform.value} machine run "
        f"`uv run --frozen python scripts/build_pollen.py --output-dir {binary.parent}`, or unzip "
        f"the Pollen workflow's pollen-{platform.value} artifact there, or pass --pollen FILE."
    )
