"""Tests for hivemind.wardens.ticks.tools.quarantine_run: running the Comb on the Queen's order.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/tools/quarantine_run.py. The development subprocess sandbox
    runs a real Comb over the echo tool, so the report in the first test is a real verdict; the
    refusal tests answer without starting a sandbox at all.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.tools.quarantine_run for the module under test.
    - tests/unit/royal_jelly/quarantine_comb/test_comb_end_to_end.py for the Comb itself.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.royal_jelly import make_package, make_tool_files, make_tool_spec
from builders.wardens_tools import ToolRig, add_sub_bee, make_resident, make_tool_rig

from hivemind.manifest.schema.royal_jelly import SandboxKind
from hivemind.pheromone import TrailQuery
from hivemind.royal_jelly import QuarantineLimits, QuarantineReport, ToolSpec, sha256_of
from hivemind.wardens.inbox import to_inbox_item
from hivemind.wardens.links import QUEEN_LINK
from hivemind.wardens.ticks.tools import handle_quarantine_order
from hivemind.wardens.ticks.tools.messages import (
    BAD_ORDER,
    PACKAGE_UNAVAILABLE,
    SANDBOX_REFUSED,
)
from waggle.envelope import Envelope
from waggle.messages.control import ErrorMessage
from waggle.messages.tool import ToolQuarantine, ToolQuarantineReport
from waggle.messages.tool.authoring import QuarantinePath

_LIMITS = QuarantineLimits(wall_s=20, fuzz_cases=4)  # The end-to-end suite's own fast limits.


def _order(rig: ToolRig, spec: ToolSpec, package: bytes, **overrides: object) -> ToolQuarantine:
    """Build the Queen's order to prove `package` against `spec` on this Warden's Cell."""
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
        "limits_json": _LIMITS.to_json(),
        "requested_by_warden": rig.warden._warden_id,
        "reason": "A Cell-scope tool needs a sandbox.",
    }
    fields.update(overrides)
    return ToolQuarantine.model_validate(fields)


async def _send(rig: ToolRig, order: ToolQuarantine) -> Envelope:
    """Deliver `order` to the Warden as the Queen would, returning the envelope sent."""
    envelope = rig.queen.envelope_for(order)
    await handle_quarantine_order(rig.warden, to_inbox_item(envelope, QUEEN_LINK))
    return envelope


async def test_an_order_runs_a_real_comb_and_the_report_is_relayed_correlated_to_it(
    hive_rig: ToolRig,
) -> None:
    spec = make_tool_spec(required_capabilities=("fs:read:*",))
    package = make_package(spec=spec)
    await make_resident(hive_rig, package)

    order_env = await _send(hive_rig, _order(hive_rig, spec, package))
    await asyncio.gather(*hive_rig.warden._tool_jobs)  # The Comb runs in a background job.

    reply = await hive_rig.queen.next()
    assert isinstance(reply.payload, ToolQuarantineReport)
    assert reply.correlation_id == order_env.id
    assert reply.payload.passed and reply.payload.failing_gate is None
    assert reply.payload.tool_id == spec.tool_id
    # The development sandbox has no Cell of its own, so the report names this Warden's.
    assert hive_rig.warden._cell is not None
    assert reply.payload.sandbox_cell_id == hive_rig.warden._cell.id
    report = QuarantineReport.from_json(reply.payload.report_json)
    assert report.package_sha256 == sha256_of(package)
    [event] = await hive_rig.warden._deps.trail.query(TrailQuery(kind="tool.quarantined"))
    assert event.subject_id == spec.tool_id and event.payload["passed"] is True


async def test_a_failing_tool_is_reported_failed_with_its_gate(hive_rig: ToolRig) -> None:
    spec = make_tool_spec(required_capabilities=("fs:read:*",))
    bad = "import socket\n\nasync def run(session, arguments):\n    return {}\n"
    package = make_package(make_tool_files(bad, None, spec))
    await make_resident(hive_rig, package)

    order_env = await _send(hive_rig, _order(hive_rig, spec, package))
    await asyncio.gather(*hive_rig.warden._tool_jobs)

    reply = await hive_rig.queen.next()
    assert isinstance(reply.payload, ToolQuarantineReport)
    assert reply.correlation_id == order_env.id
    assert not reply.payload.passed and reply.payload.failing_gate == "static"


async def test_a_virtual_cell_hosts_the_comb_through_its_own_session(cell_rig: ToolRig) -> None:
    spec = make_tool_spec(required_capabilities=("fs:read:*",))
    package = make_package(spec=spec)
    await make_resident(cell_rig, package)

    await _send(cell_rig, _order(cell_rig, spec, package))
    await asyncio.gather(*cell_rig.warden._tool_jobs)

    reply = await cell_rig.queen.next()
    assert isinstance(reply.payload, ToolQuarantineReport)
    # The fake session cannot run Python, so the verdict is a failure; the point is whose Cell.
    assert cell_rig.warden._cell is not None
    assert reply.payload.sandbox_cell_id == cell_rig.warden._cell.id


async def test_a_real_cell_never_hosts_the_comb_unless_the_manifest_names_the_subprocess_sandbox(
    tmp_path: Path,
) -> None:
    rig = await make_tool_rig(tmp_path, hive_stand=True, sandbox=SandboxKind.VIRTUAL)
    spec = make_tool_spec()
    package = make_package(spec=spec)
    await make_resident(rig, package)

    order_env = await _send(rig, _order(rig, spec, package))

    reply = await rig.queen.next()
    assert isinstance(reply.payload, ErrorMessage)
    assert (reply.payload.code, reply.payload.failed_kind) == (SANDBOX_REFUSED, "tool.quarantine")
    assert reply.correlation_id == order_env.id
    assert not rig.warden._tool_jobs
    await rig.warden.stop()


async def test_a_package_that_is_not_resident_is_refused_not_awaited(hive_rig: ToolRig) -> None:
    spec = make_tool_spec()
    package = make_package(spec=spec)  # Never stored.

    order_env = await _send(hive_rig, _order(hive_rig, spec, package))

    reply = await hive_rig.queen.next()
    assert isinstance(reply.payload, ErrorMessage)
    assert reply.payload.code == PACKAGE_UNAVAILABLE
    assert reply.correlation_id == order_env.id


@pytest.mark.parametrize("field", ["name", "version", "tool_id"])
async def test_an_order_that_contradicts_its_own_spec_is_refused(
    hive_rig: ToolRig, field: str
) -> None:
    spec = make_tool_spec()
    package = make_package(spec=spec)
    await make_resident(hive_rig, package)
    other = {"name": "other_tool", "version": "9.9.9", "tool_id": make_tool_spec().tool_id}[field]

    await _send(hive_rig, _order(hive_rig, spec, package, **{field: other}))

    reply = await hive_rig.queen.next()
    assert isinstance(reply.payload, ErrorMessage) and reply.payload.code == BAD_ORDER


async def test_an_order_from_anyone_but_the_queen_is_dropped(hive_rig: ToolRig) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    spec = make_tool_spec()
    package = make_package(spec=spec)
    await make_resident(hive_rig, package)
    forged = worker.envelope_for(_order(hive_rig, spec, package))

    await handle_quarantine_order(hive_rig.warden, to_inbox_item(forged, sub_bee.worker_id))

    assert not hive_rig.warden._tool_jobs
    await hive_rig.warden._deps.queen_link.close()
    with pytest.raises(StopAsyncIteration):
        await hive_rig.queen.next()
