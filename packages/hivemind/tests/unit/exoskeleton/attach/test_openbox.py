"""Unit tests for hivemind.exoskeleton.attach.openbox: a window manager that launches nothing.

Fits into the Hive:
    Mirrors src/hivemind/exoskeleton/attach/openbox.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.exoskeleton.attach.openbox for the module under test.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from pathlib import Path

from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.attach.openbox import (
    MENU_FILE,
    RC_FILE,
    openbox_rc,
    write_openbox_config,
)
from hivemind.exoskeleton.scratch import ScratchLayout
from waggle.clock import FakeClock

_NS = "{http://openbox.org/3.4/rc}"  # Openbox's configuration namespace.


def _parsed() -> ElementTree.Element:
    """Parse the configuration this module generates (trusted text, never outside input)."""
    return ElementTree.fromstring(openbox_rc(Path("/scratch/x11/menu.xml")))  # noqa: S314


def test_the_configuration_binds_no_key_no_desktop_and_no_program() -> None:
    root = _parsed()

    actions = {action.get("name") for action in root.iter(f"{_NS}action")}
    contexts = {context.get("name") for context in root.iter(f"{_NS}context")}
    keyboard = root.find(f"{_NS}keyboard")

    assert actions == {"Focus", "Raise"}  # Nothing that executes, and no ShowMenu.
    assert contexts == {"Frame", "Client"}  # The desktop ("Root") answers no click.
    assert keyboard is not None
    assert len(keyboard) == 0


def test_the_configuration_names_only_the_empty_menu_it_is_given() -> None:
    root = _parsed()

    files = [element.text for element in root.iter(f"{_NS}file")]

    assert files == ["/scratch/x11/menu.xml"]


async def test_write_puts_both_files_in_the_lease_scratch(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    layout = ScratchLayout.under(tmp_path)

    rc = await write_openbox_config(session, layout)

    assert rc == layout.x11_dir / RC_FILE
    menu = await session.get_file(layout.x11_dir / MENU_FILE)
    assert b"<item" not in menu  # The root menu has no entries to run.
    assert str(layout.x11_dir / MENU_FILE).encode() in await session.get_file(rc)
