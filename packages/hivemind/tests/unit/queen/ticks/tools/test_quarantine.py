"""Tests for hivemind.queen.ticks.tools.quarantine: a Warden's sandbox request, brokered.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/quarantine.py (codingrules section 3). The rig's Warden
    plays a Warden that scaffolded a cell-scope tool, shipped the package up to the Basket and now
    asks the Queen for a sandbox with `tool.quarantine`; the sandbox Cell is a fake whose Warden
    answers the Queen's order with a canned report.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools.quarantine for the module under test.
    - builders.queen_tools for the rig.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.basket import make_deposit_request
from builders.queen_tools import FakeSandboxProvider, ToolRig, WardenScript, make_tool_rig
from builders.royal_jelly import make_package, make_passing_report, make_tool_spec

from hivemind.basket import MediaKind
from hivemind.manifest import SandboxKind
from hivemind.queen.deps import PendingReport
from hivemind.queen.ticks.tools import handle_tool_item, replies
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec, sha256_of
from waggle.ids import MessageId, new_warden_id
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.tool import QuarantinePath, ToolQuarantine, ToolQuarantineReport, ToolScope


async def _rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: WardenScript | None = None
) -> tuple[ToolRig, FakeSandboxProvider]:
    provider = FakeSandboxProvider(script)
    rig = await make_tool_rig(
        tmp_path, monkeypatch, sandbox=SandboxKind.VIRTUAL, provider=provider, request_timeout_s=5
    )
    return rig, provider


async def _upload(rig: ToolRig, package: bytes) -> None:
    """Put `package` in the Basket, as the Warden's own upward shipment would have."""
    request = make_deposit_request(
        package, media_kind=MediaKind.TOOL_PACKAGE, media_type="application/zip"
    )
    await rig.access.deposits.deposit(request)


def _cell_tool(rig: ToolRig, **overrides: object) -> tuple[ToolSpec, bytes]:
    fields: dict[str, object] = {
        "scope": ToolScope.CELL,
        "cell_id": rig.link.cell.id,
        "requested_by": rig.link.warden_id,
        "required_capabilities": (),
    }
    fields.update(overrides)
    spec = make_tool_spec(rig.deps.clock, **fields)
    return spec, make_package(spec=spec)


def _ask(rig: ToolRig, spec: ToolSpec, package: bytes, **overrides: object) -> ToolQuarantine:
    fields: dict[str, object] = {
        "tool_id": spec.tool_id,
        "name": spec.name,
        "version": spec.version,
        "scope": spec.scope,
        "cell_id": spec.cell_id,
        "spec_json": spec.to_json(),
        "package_sha256": sha256_of(package),
        "package_size_bytes": len(package),
        "path": QuarantinePath.SANDBOX_CELL,
        "limits_json": rig.access.limits.to_json(),
        "requested_by_warden": rig.link.warden_id,
        "reason": "A cell-scope tool needs a sandbox.",
    }
    fields.update(overrides)
    return ToolQuarantine(**fields)


async def test_a_wardens_sandbox_request_is_run_in_a_sandbox_and_the_report_relayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, provider = await _rig(tmp_path, monkeypatch)
    spec, package = _cell_tool(rig)
    await _upload(rig, package)

    item = await rig.send(_ask(rig, spec, package))
    await rig.settle()

    answer = await rig.reply()
    assert answer.correlation_id == item.id
    assert isinstance(answer.payload, ToolQuarantineReport)
    assert answer.payload.passed and answer.payload.failing_gate is None
    report = QuarantineReport.from_json(answer.payload.report_json)
    assert report.package_sha256 == sha256_of(package) and report.tool_id == spec.tool_id
    (sandbox,) = provider.sandbox_wardens
    assert answer.payload.sandbox_cell_id == sandbox.link.cell.id
    assert sandbox.seen[-1] == "tool.quarantine" and provider.released == [sandbox.link.cell.id]
    # The Queen keeps the run, so a later promotion by this Warden rests on her record.
    assert rig.deps.tool_lane.records[(spec.tool_id, spec.version)].report == report
    await rig.aclose()


async def test_a_failing_verdict_is_relayed_with_its_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _provider = await _rig(tmp_path, monkeypatch, WardenScript(verdict="fuzz"))
    spec, package = _cell_tool(rig)
    await _upload(rig, package)

    await rig.send(_ask(rig, spec, package))
    await rig.settle()

    answer = await rig.reply()
    assert isinstance(answer.payload, ToolQuarantineReport)
    assert not answer.payload.passed and answer.payload.failing_gate == "fuzz"
    await rig.aclose()


async def test_a_package_the_basket_does_not_hold_is_reported_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, provider = await _rig(tmp_path, monkeypatch)
    spec, package = _cell_tool(rig)

    await rig.send(_ask(rig, spec, package))

    answer = await rig.reply()
    assert isinstance(answer.payload, ErrorMessage)
    assert answer.payload.code == replies.PACKAGE_MISSING and provider.specs == []
    await rig.aclose()


async def test_an_unreachable_sandbox_is_answered_with_sandbox_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, provider = await _rig(tmp_path, monkeypatch)
    provider.fail = "no capacity"
    spec, package = _cell_tool(rig)
    await _upload(rig, package)

    item = await rig.send(_ask(rig, spec, package))
    await rig.settle()

    answer = await rig.reply()
    assert isinstance(answer.payload, ErrorMessage) and answer.correlation_id == item.id
    assert answer.payload.code == replies.SANDBOX_UNAVAILABLE
    assert answer.payload.failed_kind == "tool.quarantine"
    await rig.aclose()


@pytest.mark.parametrize(
    ("case", "fragment"),
    [
        ("other_warden", "must name the Warden"),
        ("hive_scope", "only for a cell-scope tool"),
        ("other_cell", "on its own Cell"),
        ("spec_mismatch", "does not match"),
        ("capability", "outside the requester's own set"),
        ("bad_spec", "not a tool spec"),
    ],
)
async def test_a_request_the_warden_may_not_make_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, fragment: str
) -> None:
    rig, provider = await _rig(tmp_path, monkeypatch)
    spec, package = _cell_tool(rig)
    await _upload(rig, package)
    ask = _ask(rig, spec, package)
    if case == "other_warden":
        ask = ask.model_copy(update={"requested_by_warden": new_warden_id(rig.deps.clock)})
    elif case == "hive_scope":
        spec, package = _cell_tool(rig, scope=ToolScope.HIVE, cell_id=None)
        await _upload(rig, package)
        ask = _ask(rig, spec, package)
    elif case == "other_cell":
        ask = ask.model_copy(
            update={"cell_id": make_tool_spec(rig.deps.clock, scope=ToolScope.CELL).cell_id}
        )
    elif case == "spec_mismatch":
        ask = ask.model_copy(update={"name": "another_tool"})
    elif case == "capability":
        spec, package = _cell_tool(rig, required_capabilities=("net:example.org",))
        await _upload(rig, package)
        ask = _ask(rig, spec, package)
    else:
        ask = ask.model_copy(update={"spec_json": "{}"})

    await rig.send(ask)

    answer = await rig.reply()
    assert isinstance(answer.payload, ErrorMessage)
    assert answer.payload.code == replies.REQUEST_REFUSED and fragment in answer.payload.message
    assert provider.specs == [] and rig.deps.tool_lane.jobs == {}
    await rig.aclose()


async def test_a_request_with_no_lab_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, lab=False)
    spec, package = _cell_tool(rig)

    await rig.send(_ask(rig, spec, package))

    answer = await rig.reply()
    assert isinstance(answer.payload, ErrorMessage)
    assert answer.payload.code == replies.SANDBOX_UNAVAILABLE
    await rig.aclose()


def _report_for(rig: ToolRig) -> ToolQuarantineReport:
    spec = make_tool_spec(rig.deps.clock)
    report = make_passing_report(spec, "a" * 64)
    return ToolQuarantineReport(
        tool_id=spec.tool_id,
        version=spec.version,
        passed=True,
        path=QuarantinePath.SANDBOX_CELL,
        sandbox_cell_id=rig.link.cell.id,
        failing_gate=None,
        report_json=report.to_json(),
        reason="canned",
    )


async def test_a_report_for_an_order_the_queen_never_sent_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    stranger = MessageId("msg_01DXF6DT00AAAAAAAAAAAAAAAA")

    handled = await handle_tool_item(
        rig.deps, rig.wardens, rig.item(_report_for(rig), correlation_id=stranger)
    )

    assert handled and rig.deps.tool_lane.pending_reports == {}
    await rig.aclose()


async def test_a_report_resolves_only_for_the_warden_it_was_ordered_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    order_id = MessageId("msg_01DXF6DT00BBBBBBBBBBBBBBBB")
    future: asyncio.Future[ToolQuarantineReport] = asyncio.get_running_loop().create_future()
    other = new_warden_id(rig.deps.clock)
    rig.deps.tool_lane.pending_reports[order_id] = PendingReport(other, future)

    await handle_tool_item(
        rig.deps, rig.wardens, rig.item(_report_for(rig), correlation_id=order_id)
    )
    assert not future.done()  # The rig's Warden is not the one the order went to.

    rig.deps.tool_lane.pending_reports[order_id] = PendingReport(rig.link.warden_id, future)
    await handle_tool_item(
        rig.deps, rig.wardens, rig.item(_report_for(rig), correlation_id=order_id)
    )
    assert future.done()
    await rig.aclose()
