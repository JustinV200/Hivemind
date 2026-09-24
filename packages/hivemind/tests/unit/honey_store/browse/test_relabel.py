"""Tests for hivemind.honey_store.browse.relabel: the human's recorded raise or lowering.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/relabel.py (codingrules section 3). Runs over a real
    SQLite Honey Store (`harness`), reading the changed row and its trail event back.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.relabel for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unit.honey_store.browse.harness import BrowseHive, open_browse_hive, reader

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.errors import (
    BrowseInputError,
    BrowseNotFoundError,
    BrowsePathError,
)
from hivemind.honey_store.browse.relabel import (
    LABEL_LOWERED_KIND,
    LABEL_RAISED_KIND,
    MAX_RELABEL_REASON_CHARS,
    HoneyRelabeller,
    RelabelDirection,
    RelabelRequest,
)
from waggle.ids import new_cell_id


@pytest.fixture
async def hive(tmp_path: Path) -> BrowseHive:
    """Open a fresh temp-file Honey Store for one test."""
    return await open_browse_hive(tmp_path)


def _relabeller(hive: BrowseHive) -> HoneyRelabeller:
    """Build the relabeller over this test's store, as the human."""
    return HoneyRelabeller(hive.store, hive.identity, hive.clock)


async def test_relabel_raises_a_label_and_records_it(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("Names an internal host.", clearance=HoneyClearance.C1)

    outcome = await _relabeller(hive).relabel(
        RelabelRequest(path=row.path, target=HoneyClearance.C2, reason="Internal host."), reader()
    )

    assert outcome.direction is RelabelDirection.RAISED
    assert (outcome.before, outcome.honey.clearance) == (HoneyClearance.C1, HoneyClearance.C2)
    assert (await hive.store.get_honey(row.id)).clearance is HoneyClearance.C2
    (event,) = await hive.events(LABEL_RAISED_KIND)
    assert (event.subject_id, event.actor) == (row.id, "human")
    assert event.payload == {
        "from": "C1",
        "to": "C2",
        "approver": "HUMAN",
        "reason": "Internal host.",
    }


async def test_relabel_lowers_a_label_with_a_human_approver(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("Reviewed: nothing personal.", clearance=HoneyClearance.C2)

    outcome = await _relabeller(hive).relabel(
        RelabelRequest(path=row.path, target=HoneyClearance.C0, reason=" Reviewed. "), reader()
    )

    assert outcome.direction is RelabelDirection.LOWERED
    assert (await hive.store.get_honey(row.id)).clearance is HoneyClearance.C0
    (event,) = await hive.events(LABEL_LOWERED_KIND)
    assert event.payload == {"from": "C2", "to": "C0", "approver": "HUMAN", "reason": "Reviewed."}


async def test_relabel_to_the_same_label_writes_and_records_nothing(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("Already right.", clearance=HoneyClearance.C1)

    outcome = await _relabeller(hive).relabel(
        RelabelRequest(path=row.path, target=HoneyClearance.C1, reason="Checked."), reader()
    )

    assert outcome.direction is RelabelDirection.UNCHANGED
    assert await hive.events(LABEL_RAISED_KIND) == []
    assert await hive.events(LABEL_LOWERED_KIND) == []


@pytest.mark.parametrize("reason", ["", "   ", "x" * (MAX_RELABEL_REASON_CHARS + 1)])
async def test_relabel_refuses_a_missing_or_overlong_reason(hive: BrowseHive, reason: str) -> None:
    (row,) = await hive.ripen("Some row.")

    with pytest.raises(BrowseInputError):
        await _relabeller(hive).relabel(
            RelabelRequest(path=row.path, target=HoneyClearance.C0, reason=reason), reader()
        )


async def test_relabel_takes_only_a_honey_rows_path(hive: BrowseHive) -> None:
    cell = new_cell_id(hive.clock)

    for path in ("/hive", f"/cells/{cell}/wax/wax_01ARZ3NDEKTSV4RRFFQ69G5FAV", "/bee-bread/x"):
        with pytest.raises(BrowsePathError):
            await _relabeller(hive).relabel(
                RelabelRequest(path=path, target=HoneyClearance.C0, reason="r"), reader()
            )


async def test_relabel_cannot_touch_a_row_the_reader_cannot_see(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("Royal.", clearance=HoneyClearance.C2)
    relabeller = _relabeller(hive)

    with pytest.raises(BrowseNotFoundError):
        await relabeller.relabel(
            RelabelRequest(path=row.path, target=HoneyClearance.C0, reason="r"),
            reader(ceiling=HoneyClearance.C1),
        )
    with pytest.raises(BrowseNotFoundError):
        await relabeller.relabel(
            RelabelRequest(
                path="/hive/honey_01ARZ3NDEKTSV4RRFFQ69G5FAV", target=HoneyClearance.C0, reason="r"
            ),
            reader(),
        )
    assert (await hive.store.get_honey(row.id)).clearance is HoneyClearance.C2
