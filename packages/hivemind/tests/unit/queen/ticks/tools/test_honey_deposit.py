"""Tests for hivemind.queen.ticks.tools.honey: the Nectar that describes a promoted tool.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/honey.py (codingrules section 3), against a real Honey
    Store on a temp file.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools.honey for the module under test.
    - hivemind.royal_jelly.honey_link for the submission it builds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.queen_tools import make_tool_rig
from builders.royal_jelly import make_offer, make_tool_spec
from structlog.testing import capture_logs

from hivemind.cell import CombShieldLevel, HoneyClearance, hive_stand_cell_id
from hivemind.queen.ticks.tools.honey import QueenHoneyDeposit
from hivemind.royal_jelly.honey_link import PromotionSite
from waggle.messages.tool import ToolScope


async def test_a_hive_tool_is_deposited_as_shared_knowledge_from_the_hive_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, honey=True)
    spec = make_tool_spec(rig.deps.clock)
    report, offer = make_offer(spec)

    await QueenHoneyDeposit(rig.deps).deposit_promotion(spec, report, offer.package_sha256)

    assert rig.honey_store is not None
    (nectar,) = await rig.honey_store.pending_nectar(10)
    assert nectar.title.startswith(f"Tool {spec.name} {spec.version}")
    # The Hive Stand is borrowed, so intake raises the C0 declaration to its floor.
    assert nectar.clearance is HoneyClearance.C2
    assert nectar.cell_id == hive_stand_cell_id(rig.deps.identity.node_id)
    await rig.aclose()


async def test_a_cell_tool_is_deposited_at_the_cell_it_was_promoted_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, honey=True)
    spec = make_tool_spec(rig.deps.clock, scope=ToolScope.CELL)
    report, offer = make_offer(spec)
    site = PromotionSite(cell_id=rig.link.cell.id, tier=CombShieldLevel.MEADOW)

    await QueenHoneyDeposit(rig.deps).deposit_at(spec, report, offer.package_sha256, site)

    assert rig.honey_store is not None
    (nectar,) = await rig.honey_store.pending_nectar(10)
    assert nectar.cell_id == rig.link.cell.id
    await rig.aclose()


async def test_with_no_honey_store_nothing_is_deposited_and_it_is_logged_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    spec = make_tool_spec(rig.deps.clock)
    report, offer = make_offer(spec)
    deposit = QueenHoneyDeposit(rig.deps)

    with capture_logs() as logs:
        await deposit.deposit_promotion(spec, report, offer.package_sha256)
        await deposit.deposit_promotion(spec, report, offer.package_sha256)

    assert [entry["event"] for entry in logs] == ["queen.tools.honey_unavailable"]
    await rig.aclose()
