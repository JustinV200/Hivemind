"""Define ScratchLayout: where each lease-started Exoskeleton process keeps its files, in scratch.

Everything the Exoskeleton (the optional display, input, audio and browser attachment of a Cell)
starts for a lease writes somewhere: the display's authority cookie and logs, the window manager's
cache, the sound server's socket, cookie and state, the browser's profile. On a Real Cell every one
of those writes must land inside the lease's own scratch directory, so the Cell is left exactly as
found (codingrules section 8.7): a window manager or a browser run with the operator's own HOME
writes into their dotfiles, and a sound-server client creates a cookie in their home if nothing
tells it where else to look (both found on a real host while building phase 6). `ScratchLayout` is
the one place those paths are named, under `<scratch>/exoskeleton/`, and `home_environment` points
HOME and the XDG base directories of every lease-started process into it.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`. Built
    by `hivemind.exoskeleton.attach` from the session's scratch directory and handed to the display,
    sound server and browser launchers. Calls into the standard library only.

Key invariants:
    - Every path is inside `root`, which is inside the lease's scratch directory.
    - `pulse_socket` is short enough for a Unix socket address (`socket_fits`); attach refuses a
      scratch directory too deep for one rather than failing inside the sound server.
    - A path handed to the display or sound server (an argument, an environment variable, a
      script line) is rendered with `as_posix()`: those programs only run on a Linux Cell, and the
      process building the command need not (a Windows host running the unit tests, or one day a
      Hive Stand driving a Linux device), where `str()` would write backslashes.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for per-lease processes.
    - hivemind.cell.session for the scratch directory this layout lives under.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EXOSKELETON_DIR = "exoskeleton"  # The one scratch subdirectory every Exoskeleton file lives under.
# A Unix socket address holds 108 bytes including the terminating NUL (Linux sun_path).
MAX_SOCKET_PATH_BYTES = 107

__all__ = ["EXOSKELETON_DIR", "MAX_SOCKET_PATH_BYTES", "ScratchLayout"]


@dataclass(frozen=True, slots=True)
class ScratchLayout:
    """The paths of every file a lease-started Exoskeleton process writes, under one root."""

    root: Path  # `<scratch>/exoskeleton`; absolute, so a process's own cwd never matters.

    @classmethod
    def under(cls, scratch_dir: Path) -> ScratchLayout:
        """Lay the Exoskeleton's files out under one lease's scratch directory.

        Args:
            scratch_dir: The session's scratch directory (absolute).

        Returns:
            The layout rooted at `<scratch_dir>/exoskeleton`.
        """
        return cls(root=scratch_dir / EXOSKELETON_DIR)

    @property
    def home(self) -> Path:
        """HOME for every lease-started process: dotfiles and caches land here."""
        return self.root / "home"

    @property
    def runtime(self) -> Path:
        """XDG_RUNTIME_DIR for every lease-started process: sockets and locks land here."""
        return self.root / "run"

    @property
    def authority(self) -> Path:
        """The lease display's X authority file, where X clients look for it under HOME."""
        return self.home / ".Xauthority"

    @property
    def x11_dir(self) -> Path:
        """The display server's and window manager's logs."""
        return self.root / "x11"

    @property
    def pulse_dir(self) -> Path:
        """The sound server's runtime, state, script and log."""
        return self.root / "pulse"

    @property
    def pulse_socket(self) -> Path:
        """The sound server's native-protocol socket."""
        return self.pulse_dir / "native"

    @property
    def pulse_cookie(self) -> Path:
        """The sound server's client cookie, so no client creates one in a real HOME."""
        return self.pulse_dir / "cookie"

    @property
    def browser_dir(self) -> Path:
        """The browser's profile and log."""
        return self.root / "browser"

    def directories(self) -> tuple[Path, ...]:
        """Return every directory a process expects to exist before it starts.

        Returns:
            HOME, the runtime directory, and each process's own directory.
        """
        return (self.home, self.runtime, self.x11_dir, self.pulse_dir, self.browser_dir)

    def home_environment(self) -> dict[str, str]:
        """Return HOME and the XDG base directories, all pointed inside this layout.

        Returns:
            The variables a lease-started process needs so none of its files reach a real HOME.
        """
        home = self.home
        return {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "XDG_RUNTIME_DIR": str(self.runtime),
        }

    def socket_fits(self) -> bool:
        """Return whether `pulse_socket` fits a Unix socket address.

        Returns:
            True when its encoded path is at most MAX_SOCKET_PATH_BYTES long.
        """
        return len(self.pulse_socket.as_posix().encode()) <= MAX_SOCKET_PATH_BYTES
