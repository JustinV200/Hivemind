"""Tests for hivemind.queen.ticks.tools.promotions: a remote Warden promotes its Cell's tool.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/promotions.py (codingrules section 3). The Queen's
    record of a brokered Comb run is written by hand (the run itself is tested in test_quarantine),
    then the rig's Warden announces the promotion and the real in-memory registry decides.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools.promotions for the module under test.
    - builders.queen_tools for the rig.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.queen_tools import ToolRig, make_tool_rig
from builders.royal_jelly import make_failing_report, make_offer, make_tool_spec

from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.deps import QuarantineRecord
from hivemind.queen.ticks.tools import replies
from hivemind.royal_jelly.errors import VersionExistsError
from hivemind.royal_jelly.spec import ToolSpec
from waggle.ids import CellId, WardenId, new_cell_id, new_warden_id
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.tool import QuarantinePath, ToolPromoted, ToolScope


def _record(rig: ToolRig, **spec_fields: object) -> QuarantineRecord:
    """Keep a passing run of a cell-scope tool on the rig's Cell, as a brokered run would."""
    fields: dict[str, object] = {
        "scope": ToolScope.CELL,
        "cell_id": rig.link.cell.id,
        "requested_by": rig.link.warden_id,
        "required_capabilities": (),
    }
    fields.update(spec_fields)
    spec = make_tool_spec(rig.deps.clock, **fields)
    report, offer = make_offer(spec, sandbox_cell_id=new_cell_id(rig.deps.clock))
    record = QuarantineRecord(spec, report, offer.package_sha256, spec.requested_by, spec.cell_id)
    rig.deps.tool_lane.records[(spec.tool_id, spec.version)] = record
    return record


def _announce(rig: ToolRig, spec: ToolSpec, **overrides: object) -> ToolPromoted:
    record = rig.deps.tool_lane.records[(spec.tool_id, spec.version)]
    cell_id: CellId | None = spec.cell_id
    warden: WardenId | None = rig.link.warden_id
    fields: dict[str, object] = {
        "tool_id": spec.tool_id,
        "name": spec.name,
        "version": spec.version,
        "scope": spec.scope,
        "cell_id": cell_id,
        "description": spec.description,
        "input_schema_json": spec.input_schema_json,
        "required_capabilities": spec.required_capabilities,
        "supported_os": spec.supported_os,
        "quarantine_path": QuarantinePath.SANDBOX_CELL,
        "quarantined_on": record.report.sandbox_cell_id,
        "quarantine_passed_at": record.report.finished_at,
        "package_sha256": record.package_sha256,
        "package_size_bytes": record.report.package_size_bytes,
        "promoted_by_warden": warden,
        "supersedes_version": None,
        "reason": "Passed in the sandbox.",
    }
    fields.update(overrides)
    return ToolPromoted(**fields)


async def _refusal(rig: ToolRig) -> ErrorMessage:
    reply = await rig.reply()
    assert isinstance(reply.payload, ErrorMessage)
    return reply.payload


async def test_a_wardens_promotion_of_its_cells_tool_is_written_for_it_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, honey=True)
    record = _record(rig)

    await rig.send(_announce(rig, record.spec))

    (row,) = await rig.registry.list_tools()
    assert (row.name, row.scope, row.cell_id) == (
        record.spec.name,
        ToolScope.CELL,
        rig.link.cell.id,
    )
    assert (
        row.promoted_by == str(rig.link.warden_id) and row.package_sha256 == record.package_sha256
    )
    # Acceptance is silent: the trail event is the record, and Honey holds the description.
    assert rig.peer.inbox.empty()
    events = await rig.deps.trail.query(TrailQuery(kind="tool.promoted"))
    assert [event.payload["scope"] for event in events] == ["CELL"]
    assert rig.honey_store is not None
    titles = [nectar.title for nectar in await rig.honey_store.pending_nectar(20)]
    assert any(title.startswith(f"Tool {record.spec.name} 1.0.0") for title in titles)
    await rig.aclose()


async def test_a_second_promotion_of_the_same_version_is_refused_with_the_registrys_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    record = _record(rig)
    await rig.send(_announce(rig, record.spec))

    await rig.send(_announce(rig, record.spec))

    refusal = await _refusal(rig)
    assert refusal.code == VersionExistsError.code and refusal.failed_kind == "tool.promoted"
    assert len(await rig.registry.list_tools()) == 1
    await rig.aclose()


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("hive_scope", replies.WRONG_PRINCIPAL),
        ("other_cell", replies.WRONG_PRINCIPAL),
        ("other_warden", replies.WRONG_PRINCIPAL),
        ("no_record", replies.REQUEST_REFUSED),
        ("foreign_record", replies.REQUEST_REFUSED),
        ("failed_run", replies.REQUEST_REFUSED),
        ("other_package", replies.REQUEST_REFUSED),
    ],
)
async def test_a_promotion_the_warden_may_not_make_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, code: str
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    record = _record(rig)
    overrides: dict[str, object] = {}
    if case == "hive_scope":
        overrides = {"scope": ToolScope.HIVE, "cell_id": None, "promoted_by_warden": None}
    elif case == "other_cell":
        overrides = {"cell_id": new_cell_id(rig.deps.clock)}
    elif case == "other_warden":
        overrides = {"promoted_by_warden": new_warden_id(rig.deps.clock)}
    elif case == "foreign_record":
        other = _record(rig, requested_by=new_warden_id(rig.deps.clock))
        record = other
    elif case == "failed_run":
        failed = make_failing_report(record.spec, record.package_sha256, failing_gate="tests")
        rig.deps.tool_lane.records[(record.spec.tool_id, record.spec.version)] = QuarantineRecord(
            record.spec, failed, record.package_sha256, record.requested_by, record.cell_id
        )
    elif case == "other_package":
        overrides = {"package_sha256": "b" * 64}
    announcement = _announce(rig, record.spec, **overrides)
    if case == "no_record":
        rig.deps.tool_lane.records.clear()

    await rig.send(announcement)

    refusal = await _refusal(rig)
    assert refusal.code == code and await rig.registry.list_tools() == ()
    await rig.aclose()


async def test_a_promotion_with_no_lab_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, lab=False)
    record = _record(rig)

    await rig.send(_announce(rig, record.spec))

    assert (await _refusal(rig)).code == replies.SANDBOX_UNAVAILABLE
    await rig.aclose()
