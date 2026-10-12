"""Share the two Warden rigs the Royal Jelly and Basket tick tests are driven against.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Provides `hive_rig` (the Hive
    Stand's Warden, a REAL Cell with the Lab's handles) and `cell_rig` (a Virtual Cell's Warden,
    its own store and node keys), both started and both stopped after the test.

Key invariants:
    - A rig is built over a test's own `tmp_path`, so no test shares a store or a scratch.

See Also:
    - builders.wardens.make_tool_rig for what a rig holds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.wardens_tools import ToolRig, make_tool_rig


@pytest.fixture
async def hive_rig(tmp_path: Path) -> AsyncIterator[ToolRig]:
    """A started Hive Stand Warden: a REAL Cell, `royal_jelly` set, the subprocess sandbox."""
    rig = await make_tool_rig(tmp_path, hive_stand=True)
    yield rig
    await rig.warden.stop()


@pytest.fixture
async def cell_rig(tmp_path: Path) -> AsyncIterator[ToolRig]:
    """A started Virtual Cell Warden: its own store and node keys, nothing in `royal_jelly`."""
    rig = await make_tool_rig(tmp_path, hive_stand=False)
    yield rig
    await rig.warden.stop()
