"""Tests for the tools dispatch: `handle_tool_item`, and its place in `handle_infrastructure_item`.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/__init__.py and the routing point in src/hivemind/
    queen/ticks/liveness.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools for the module under test.
    - hivemind.queen.ticks.liveness for the routing point.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.queen_tools import make_tool_rig
from builders.supervision import make_inbox_item

from hivemind.queen.ticks.liveness import handle_infrastructure_item
from hivemind.queen.ticks.tools import handle_tool_item, replies
from waggle.messages.control.protocol import ErrorMessage


async def test_a_payload_that_is_not_a_tool_or_basket_message_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)

    handled = await handle_tool_item(rig.deps, rig.wardens, make_inbox_item(payload=None))

    assert handled is False
    await rig.aclose()


async def test_the_infrastructure_dispatch_hands_a_tool_request_to_the_tools_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, lab=False)
    item = rig.item(rig.request())

    handled = await handle_infrastructure_item(rig.deps, rig.wardens, item, {}, {})

    assert handled is True  # Never reaches the autopilot table, which would wake a model.
    error = (await rig.reply()).payload
    assert isinstance(error, ErrorMessage) and error.code == replies.SANDBOX_UNAVAILABLE
    await rig.aclose()


async def test_the_infrastructure_dispatch_leaves_other_items_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)

    handled = await handle_infrastructure_item(
        rig.deps, rig.wardens, make_inbox_item(payload=None), {}, {}
    )

    assert handled is False
    await rig.aclose()
