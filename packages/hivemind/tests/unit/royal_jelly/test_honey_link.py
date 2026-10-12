"""Tests for hivemind.royal_jelly.honey_link: the Nectar a promotion deposits (roadmap 9.8).

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/honey_link.py (codingrules section 3). The submission is
    also run through the Honey Store's own scope and clearance functions, so the claims the
    module's docstring makes about where a tool lands are checked against the real rules.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.honey_link for the module under test.
"""

from __future__ import annotations

import pytest
from builders.royal_jelly import make_offer, make_tool_spec

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store import NectarOrigin, NectarSubmission
from hivemind.honey_store.clearance import intake_floor
from hivemind.honey_store.scope import NectarProvenance, scope_for_nectar
from hivemind.royal_jelly.honey_link import (
    TOOL_SOURCE_KEY_PREFIX,
    PromotionSite,
    promotion_nectar_text,
    promotion_submission,
)
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id
from waggle.messages.honey import NectarKind
from waggle.messages.tool.authoring import ToolScope


def _site(cell_id: CellId | None = None, *, from_borrowed_cell: bool = False) -> PromotionSite:
    return PromotionSite(
        cell_id=cell_id or new_cell_id(FakeClock()),
        tier=CombShieldLevel.MEADOW,
        from_borrowed_cell=from_borrowed_cell,
    )


def test_the_text_describes_the_tool_and_names_its_package_but_never_carries_it() -> None:
    spec = make_tool_spec(name="disk_report", description="Report free disk space.")
    report, _offer = make_offer(spec)

    text = promotion_nectar_text(spec, report)

    assert "# Tool disk_report 1.0.0" in text
    assert "Report free disk space." in text
    assert "Platforms: LINUX" in text and "net:example.org" in text
    assert report.package_sha256 in text
    assert "async def run" not in text  # a tool's source stays in the Basket


def test_a_hive_tool_is_shared_knowledge_declared_c0_and_keyed_by_name_and_version() -> None:
    spec = make_tool_spec(name="disk_report")
    report, offer = make_offer(spec)
    clock = FakeClock()

    submission = promotion_submission(spec, report, offer.package_sha256, _site(), clock)

    assert isinstance(submission, NectarSubmission)
    assert submission.source_key == f"{TOOL_SOURCE_KEY_PREFIX}disk_report:1.0.0"
    assert (submission.kind, submission.origin) == (NectarKind.FINDING, NectarOrigin.TASK_OUTCOME)
    assert submission.declared is HoneyClearance.C0
    assert submission.observed_at == clock.now()
    assert submission.task_id is None and submission.bee is None
    assert submission.content.decode().startswith("# Tool disk_report")
    assert scope_for_nectar(_provenance(submission), None) == "hive"


def test_a_cell_tool_stays_in_its_own_cells_honey_scope_and_is_declared_c1() -> None:
    spec = make_tool_spec(scope=ToolScope.CELL)
    report, offer = make_offer(spec)
    site = _site(spec.cell_id)

    submission = promotion_submission(spec, report, offer.package_sha256, site, FakeClock())

    assert submission.declared is HoneyClearance.C1
    assert scope_for_nectar(_provenance(submission), None) == f"cell:{spec.cell_id}"


def test_a_borrowed_cell_raises_the_label_through_the_intake_floor() -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec)

    submission = promotion_submission(
        spec, report, offer.package_sha256, _site(from_borrowed_cell=True), FakeClock()
    )

    assert submission.from_borrowed_cell is True
    assert intake_floor(submission.origin, submission.from_borrowed_cell) is HoneyClearance.C2


def test_the_title_is_bounded() -> None:
    spec = make_tool_spec(description="d" * 4_000)
    report, offer = make_offer(spec)

    submission = promotion_submission(spec, report, offer.package_sha256, _site(), FakeClock())

    assert 0 < len(submission.title) <= 200


def test_a_package_hash_the_report_did_not_prove_is_refused() -> None:
    spec = make_tool_spec()
    report, _offer = make_offer(spec)

    with pytest.raises(ValueError, match="proved"):
        promotion_submission(spec, report, "f" * 64, _site(), FakeClock())


def _provenance(submission: NectarSubmission) -> NectarProvenance:
    return NectarProvenance(
        kind=submission.kind,
        origin=submission.origin,
        task_id=submission.task_id,
        cell_id=submission.cell_id,
        bee=submission.bee,
    )
