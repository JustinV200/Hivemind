"""Tests for hivemind.queen.ticks.tools.runner: the Comb in a sandbox Cell the Queen provisions.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/runner.py (codingrules section 3). The Virtual backend
    is a `FakeSandboxProvider` whose Cell's Warden is a `FakeWarden`: it keeps what the Queen ships
    with the real receiver and answers the order with a canned report, each reply delivered
    through the Queen's own handlers, so the offer, chunk, ack, order and report really cross.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools.runner for the module under test.
    - builders.queen_tools for the rig.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from builders.queen_tools import (
    FakeSandboxProvider,
    FakeWarden,
    ToolRig,
    WardenScript,
    make_tool_rig,
)
from builders.royal_jelly import make_passing_report, make_tool_spec

from hivemind.cell import CombShieldLevel
from hivemind.hive import NetworkPolicy
from hivemind.manifest import SandboxKind
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.deps import QuarantineRecord
from hivemind.queen.ticks.tools import replies
from hivemind.queen.ticks.tools.runner import MAX_RECORDS, keep_record, sandbox_cell_spec
from waggle.ids import new_warden_id
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.tool import ToolPromoted


async def _virtual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: WardenScript | None = None,
    request_timeout_s: float = 20.0,
) -> tuple[ToolRig, FakeSandboxProvider]:
    provider = FakeSandboxProvider(script)
    rig = await make_tool_rig(
        tmp_path,
        monkeypatch,
        sandbox=SandboxKind.VIRTUAL,
        provider=provider,
        request_timeout_s=request_timeout_s,
    )
    return rig, provider


async def _events(rig: ToolRig, kind: str) -> int:
    return len(await rig.deps.trail.query(TrailQuery(kind=kind)))


async def test_the_package_is_shipped_to_the_sandbox_then_the_comb_ordered_and_its_report_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, provider = await _virtual(tmp_path, monkeypatch)

    item = await rig.send(rig.request())
    await rig.settle()

    # Offer, then the chunk(s), then the order: the order is sent after the blob is resident.
    (sandbox,) = provider.sandbox_wardens
    assert sandbox.seen == ["basket.offer", "basket.chunk", "tool.quarantine"]
    (order,) = sandbox.orders
    assert order.package_sha256 in sandbox.blobs and order.requested_by_warden == rig.link.warden_id
    # The sandbox is provisioned with no network and released once the report arrived.
    (spec,) = provider.specs
    assert spec.network_policy is NetworkPolicy.NONE and spec.comb_shield is CombShieldLevel.MEADOW
    assert spec.labels == {"hivemind.purpose": "comb-sandbox"}
    assert provider.released == [sandbox.link.cell.id] and rig.deps.tool_lane.sandbox_cells == set()
    # Marked a sandbox the moment its Cell existed, so the attach's catch-up skipped it.
    assert provider.marked_before_attach == [True]
    assert await _events(rig, "tool.sandbox_provisioned") == 1
    assert await _events(rig, "tool.sandbox_released") == 1
    # The sandbox's report was the verdict: the tool is promoted, and the Queen kept the run.
    announced, answer = await rig.reply(), await rig.reply()
    promoted = answer.payload
    assert isinstance(announced.payload, ToolPromoted) and answer.correlation_id == item.id
    assert isinstance(promoted, ToolPromoted) and promoted.quarantined_on == sandbox.link.cell.id
    ((tool_id, version), record) = next(iter(rig.deps.tool_lane.records.items()))
    assert (tool_id, version) == (promoted.tool_id, "1.0.0")
    assert record.report.passed and record.requested_by == rig.link.warden_id
    assert rig.deps.tool_lane.pending_reports == {}
    await rig.aclose()


async def test_a_failing_report_releases_the_sandbox_each_time_and_rejects_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, provider = await _virtual(tmp_path, monkeypatch, WardenScript(verdict="tests"))

    await rig.send(rig.request())
    await rig.settle()

    error = await rig.reply()
    assert isinstance(error.payload, ErrorMessage)
    assert error.payload.code == replies.REJECTED and "tests" in error.payload.message
    # Two scaffolds, so two sandboxes, each released.
    assert len(provider.specs) == len(provider.released) == 2
    assert await _events(rig, "tool.sandbox_released") == 2
    assert await rig.registry.list_tools() == ()
    await rig.aclose()


@pytest.mark.parametrize("failure", ["provision", "refused", "silent"])
async def test_a_sandbox_that_cannot_be_used_is_unavailable_not_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    script = WardenScript(refuse_offer=failure == "refused", silent=failure == "silent")
    rig, provider = await _virtual(tmp_path, monkeypatch, script, request_timeout_s=0.5)
    provider.fail = "no capacity" if failure == "provision" else None

    await rig.send(rig.request())
    await rig.settle()

    error = await rig.reply()
    assert isinstance(error.payload, ErrorMessage)
    assert error.payload.code == replies.SANDBOX_UNAVAILABLE
    assert await rig.registry.list_tools() == ()
    # Whatever was provisioned was released; a failed provision left nothing to release.
    assert len(provider.released) == len(provider.sandbox_wardens)
    assert await _events(rig, "tool.sandbox_released") == len(provider.released)
    await rig.aclose()


async def test_a_hive_with_no_sandbox_provider_cannot_prove_a_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, provider = await _virtual(tmp_path, monkeypatch)
    rig.deps = dataclasses.replace(rig.deps, sandboxes=None)

    await rig.send(rig.request())
    await rig.settle()

    error = await rig.reply()
    assert isinstance(error.payload, ErrorMessage)
    assert error.payload.code == replies.SANDBOX_UNAVAILABLE and provider.specs == []
    await rig.aclose()


async def test_a_report_that_proves_another_package_is_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = FakeWarden._report
    monkeypatch.setattr(
        FakeWarden,
        "_report",
        lambda self, order: original(self, order.model_copy(update={"package_sha256": "0" * 64})),
    )
    rig, provider = await _virtual(tmp_path, monkeypatch)

    await rig.send(rig.request())
    await rig.settle()

    error = await rig.reply()
    assert isinstance(error.payload, ErrorMessage)
    assert error.payload.code == replies.SANDBOX_UNAVAILABLE
    assert provider.released and await rig.registry.list_tools() == ()
    await rig.aclose()


def test_the_sandbox_spec_comes_from_the_manifest_with_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path, monkeypatch
    from builders.queen import make_queen_deps

    from hivemind.manifest import RoyalJellySection

    deps, _link, _end = make_queen_deps()
    config = RoyalJellySection(sandbox_cpu_cores=2.0, sandbox_lifetime_s=120.0)

    spec = sandbox_cell_spec(deps, config)

    assert spec.image == config.sandbox_image and spec.cpu_cores == 2.0
    assert spec.lifetime_s == 120 and spec.hive_id == deps.identity.hive_id
    assert spec.network_policy is NetworkPolicy.NONE


def test_only_the_newest_runs_are_kept() -> None:
    from builders.queen import make_queen_deps

    deps, _link, _end = make_queen_deps()
    spec = make_tool_spec()
    report = make_passing_report(spec, "a" * 64)
    first = QuarantineRecord(spec, report, "a" * 64, new_warden_id(deps.clock), None)

    keep_record(deps, first)
    for number in range(MAX_RECORDS):
        newer = make_tool_spec(deps.clock, version=f"1.0.{number + 1}")
        record = QuarantineRecord(newer, report, "a" * 64, first.requested_by, None)
        keep_record(deps, record)

    records = deps.tool_lane.records
    assert len(records) == MAX_RECORDS and (spec.tool_id, spec.version) not in records
