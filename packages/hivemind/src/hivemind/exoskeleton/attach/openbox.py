"""Write the lease's own openbox configuration: a window manager that manages and launches nothing.

A lease display needs a window manager, because without one `xdotool mousemove` returns success
and the pointer never moves (ADR-0031), and openbox is the one proven to work. Started with its
system configuration, though, openbox is a launcher: a right-click on the desktop opens a root
menu whose entries run a terminal, a web browser and every installed application, and its default
keybindings run programs too (checked on this repository's openbox, 2026-09-24: a right-click at
the desktop rendered the menu). Desktop input on a lease display is tiered `scratch_write`, so a
bee could start a program outside the Capping gate (the quality gate every side effect passes) with
two clicks. This module writes a configuration into the lease's scratch that keeps only what input
needs: new windows take focus, a press on a window focuses and raises it; no key or desktop binding
does anything else, and the menu file it names is empty.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.attach`.
    Called by `attach.display` before it starts openbox with `--config-file`. Calls into
    `hivemind.cell` (CellSession) and `hivemind.exoskeleton.scratch` only.

Key invariants:
    - The configuration binds no Execute action, no ShowMenu action and no key at all.
    - Both files live in the lease's scratch, so release removes them with everything else.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for why openbox is there.
    - hivemind.exoskeleton.attach.display for the display this configures.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell import CellSession
from hivemind.exoskeleton.scratch import ScratchLayout

RC_FILE = "openbox-rc.xml"  # Inside the layout's x11 directory, beside the cookie and the logs.
MENU_FILE = "openbox-menu.xml"  # The one menu file the configuration names: empty.
# A press on a window's contents or frame focuses and raises it, the one binding input needs; the
# desktop ("Root") and the keyboard are left unbound, which is what keeps every menu closed.
_FOCUS_ON_PRESS = "".join(
    f'<mousebind button="{button}" action="Press"><action name="Focus"/>'
    f'<action name="Raise"/></mousebind>'
    for button in ("Left", "Middle", "Right")
)
_RC_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <focus><focusNew>yes</focusNew><followMouse>no</followMouse></focus>
  <desktops><number>1</number></desktops>
  <keyboard></keyboard>
  <mouse>
    <context name="Frame">{focus}</context>
    <context name="Client">{focus}</context>
  </mouse>
  <menu><file>{menu}</file></menu>
</openbox_config>
"""
_EMPTY_MENU = """<?xml version="1.0" encoding="UTF-8"?>
<openbox_menu xmlns="http://openbox.org/3.4/menu">
  <menu id="root-menu" label="Openbox"/>
</openbox_menu>
"""

__all__ = ["MENU_FILE", "RC_FILE", "openbox_rc", "write_openbox_config"]


def openbox_rc(menu: Path) -> str:
    """Return the lease's openbox configuration, naming `menu` as its only menu file.

    Args:
        menu: Where the empty menu file is written; absolute, inside the lease's scratch.

    Returns:
        The rc.xml text: focus on new windows and on a press, and nothing else bound.
    """
    return _RC_TEMPLATE.format(focus=_FOCUS_ON_PRESS, menu=menu.as_posix())


async def write_openbox_config(session: CellSession, layout: ScratchLayout) -> Path:
    """Write the configuration and its empty menu into the lease's scratch, through the session.

    Latency: two small file writes on the Cell.

    Args:
        session: The Cell's session; both files go through it, like every file attach writes.
        layout: Where the lease's X11 files live.

    Returns:
        The configuration's path, for openbox's `--config-file`.

    Raises:
        SessionClosedError: The session closed first.
        PathNotAllowedError: The layout is not inside the session's scratch.
    """
    menu = layout.x11_dir / MENU_FILE
    rc = layout.x11_dir / RC_FILE
    # External awaits: two small writes through the session, milliseconds each.
    await session.put_file(menu, _EMPTY_MENU.encode("utf-8"))
    await session.put_file(rc, openbox_rc(menu).encode("utf-8"))
    return rc
