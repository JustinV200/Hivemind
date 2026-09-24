"""Define X11Display: everything an X11 peripheral needs to reach one display on its Cell.

The Exoskeleton's X11 backends (the CompoundEye's screen capture, the Antennae's pointer and
keyboard) run their commands on the Cell through its `CellSession`, so they reach the display the
way any X client does: through the `DISPLAY` and `XAUTHORITY` environment variables. `X11Display`
bundles those two with the display's size, and `environment` renders them for an `ExecSpec`. A
display attach started (Xvfb, owned by the lease) carries the authority file attach wrote into
scratch; the display already running on a Cell whose operator allowed it carries whatever authority
that Cell's environment already has (None: the session inherits it).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`. Built
    by `hivemind.exoskeleton.attach` and handed to `hivemind.exoskeleton.compound_eye.x11` and
    `hivemind.exoskeleton.antennae.xdotool`. Calls into `hivemind.exoskeleton.geometry` only.

Key invariants:
    - `name` is always an X display name of the ":N" form attach produced or the Cell reported;
      it is never built from model output.
    - `environment()` sets `XAUTHORITY` only when an authority file is known, so a real display's
      own environment is never overridden with a file that does not exist.
    - `authority_file` writes one entry that matches every display number, because a lease's
      Xvfb picks its own free number (`-displayfd`) only after the file must already exist.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for why displays are private.
    - hivemind.exoskeleton.attach for the code that starts a display and builds one of these.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from hivemind.exoskeleton.geometry import ScreenSize

_DISPLAY_NAME = re.compile(r"^:\d{1,4}(\.\d{1,2})?$")  # ":N" or ":N.S"; a local display only.
COOKIE_BYTES = 16  # An MIT-MAGIC-COOKIE-1 is 128 random bits.
_COOKIE_PROTOCOL = b"MIT-MAGIC-COOKIE-1"
_FAMILY_WILD = 0xFFFF  # Xauthority's "any address" family.

__all__ = ["COOKIE_BYTES", "X11Display", "authority_file"]


@dataclass(frozen=True, slots=True)
class X11Display:
    """One X display on a Cell: its name, the authority file that admits clients, its size."""

    name: str  # The DISPLAY value, ":N"; local only, never host:N.
    authority: Path | None  # The XAUTHORITY file for a display attach started; None inherits.
    size: ScreenSize  # The display's resolution, in pixels.

    def __post_init__(self) -> None:
        """Refuse anything but a local ":N" display name before a command is built from it."""
        if not _DISPLAY_NAME.match(self.name):
            raise ValueError(f"An X11Display name is ':N' (local only), got {self.name!r}.")

    def environment(self) -> dict[str, str]:
        """Return the environment an X client on the Cell needs to reach this display.

        Returns:
            `DISPLAY`, plus `XAUTHORITY` when this display has its own authority file.
        """
        env = {"DISPLAY": self.name}
        if self.authority is not None:
            # POSIX whatever this process runs on: the X clients reading it run on a Linux Cell.
            env["XAUTHORITY"] = self.authority.as_posix()
        return env


def authority_file(cookie: bytes) -> bytes:
    """Encode an Xauthority file holding `cookie` for any local display.

    The X server loads every cookie in the file it is given with `-auth`, whatever display each
    entry names; a client picks the entry matching its display, and an entry with the wildcard
    family and an empty display number matches any (libXau's own matching rule). One such entry
    therefore admits clients to whichever display number Xvfb chooses, and no `xauth` program is
    needed on the Cell.

    Args:
        cookie: COOKIE_BYTES random bytes.

    Returns:
        The file's bytes: one FamilyWild entry for MIT-MAGIC-COOKIE-1.

    Raises:
        ValueError: `cookie` is not COOKIE_BYTES long.
    """
    if len(cookie) != COOKIE_BYTES:
        raise ValueError(f"An X cookie is {COOKIE_BYTES} bytes, got {len(cookie)}.")
    fields = (b"", b"", _COOKIE_PROTOCOL, cookie)  # Address, display number, protocol, data.
    return struct.pack(">H", _FAMILY_WILD) + b"".join(
        struct.pack(">H", len(field)) + field for field in fields
    )
