"""Tests for hivemind.wardens.ticks.tools.remote_registry: promoting through the Queen.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/tools/remote_registry.py. The Queen is the test: it reads the
    `tool.promoted` the adapter announces and answers with silence (accepted) or a `control.error`
    (refused) inside the two-second wait the adapter allows.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.tools.remote_registry for the module under test.
    - hivemind.royal_jelly.registry for the CombRegistry protocol and its own refusals.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.royal_jelly import make_failing_report, make_offer, make_tool_spec
from builders.wardens_tools import ToolRig, add_sub_bee

from hivemind.pheromone import TrailQuery
from hivemind.royal_jelly import (
    ChangeKind,
    PromotionRefusedError,
    QueenPrincipal,
    RegistryChange,
    ReportFailedError,
    ToolKey,
    ToolNotFoundError,
    ToolSpec,
    WardenPrincipal,
    WrongCellError,
    WrongPrincipalError,
)
from hivemind.wardens.inbox import to_inbox_item
from hivemind.wardens.links import QUEEN_LINK
from hivemind.wardens.ticks.tools import RemoteCombRegistry, handle_tool_item
from hivemind.wardens.ticks.tools.install import RETRACTED, reattenuate
from hivemind.wardens.ticks.tools.remote_registry import REFUSAL_WAIT_S
from hivemind.workers.tools.comb import CombToolEntry
from waggle.envelope import Envelope
from waggle.messages.control import ErrorMessage
from waggle.messages.tool import ToolPromoted
from waggle.messages.tool.authoring import ToolScope


def _cell_spec(rig: ToolRig, **overrides: object) -> ToolSpec:
    """Build a CELL-scope spec for this rig's Cell."""
    cell = rig.warden._cell
    assert cell is not None
    return make_tool_spec(rig.clock, scope=ToolScope.CELL, cell_id=cell.id, **overrides)


def _principal(rig: ToolRig) -> WardenPrincipal:
    cell = rig.warden._cell
    assert cell is not None
    return WardenPrincipal(rig.warden._warden_id, cell.id)


async def _promote_and_lapse(
    rig: ToolRig, registry: RemoteCombRegistry, spec: ToolSpec
) -> tuple[Envelope, RegistryChange]:
    """Promote `spec`, let the two-second wait lapse in silence and return the outcome."""
    report, offer = make_offer(spec)
    job = asyncio.ensure_future(registry.promote(spec, report, offer, _principal(rig), "Passed."))
    announced = await rig.queen.next()
    while not job.done():
        rig.clock.advance(REFUSAL_WAIT_S)
        await asyncio.sleep(0)
    return announced, await job


async def test_a_promotion_is_announced_to_the_queen_and_accepted_by_her_silence(
    cell_rig: ToolRig,
) -> None:
    registry = RemoteCombRegistry(cell_rig.warden)
    spec = _cell_spec(cell_rig)

    announced, change = await _promote_and_lapse(cell_rig, registry, spec)

    message = announced.payload
    assert isinstance(message, ToolPromoted)
    assert (message.name, message.scope, message.cell_id) == (
        spec.name,
        ToolScope.CELL,
        spec.cell_id,
    )
    assert message.promoted_by_warden == cell_rig.warden._warden_id
    assert change.kind is ChangeKind.PROMOTED and change.row.is_current
    assert change.by == str(cell_rig.warden._warden_id)
    assert await registry.get(spec.name, ToolScope.CELL, spec.cell_id) == change.row
    assert await registry.visible_tools(spec.cell_id) == (change.row,)  # type: ignore[arg-type]
    assert not cell_rig.warden._tool_relay._waits


async def test_a_later_version_displaces_the_earlier_one_as_current(cell_rig: ToolRig) -> None:
    registry = RemoteCombRegistry(cell_rig.warden)
    first = _cell_spec(cell_rig, version="1.0.0")
    second = _cell_spec(cell_rig, version="1.1.0", tool_id=first.tool_id)

    await _promote_and_lapse(cell_rig, registry, first)
    _announced, change = await _promote_and_lapse(cell_rig, registry, second)

    assert change.supersedes_version == "1.0.0"
    current = await registry.list_tools(current_only=True)
    assert [row.version for row in current] == ["1.1.0"]
    assert [row.version for row in await registry.list_tools()] == ["1.0.0", "1.1.0"]
    assert await registry.get(first.name, ToolScope.CELL, first.cell_id, "1.0.0")


async def test_a_refusal_inside_the_wait_is_raised_and_nothing_is_kept(cell_rig: ToolRig) -> None:
    registry = RemoteCombRegistry(cell_rig.warden)
    spec = _cell_spec(cell_rig)
    report, offer = make_offer(spec)
    job = asyncio.ensure_future(registry.promote(spec, report, offer, _principal(cell_rig), "Ok."))
    announced = await cell_rig.queen.next()
    refusal = ErrorMessage(
        code="hive.registry.version_exists",
        message="That version already exists for this Cell.",
        failed_kind="tool.promoted",
        is_retryable=False,
    )
    envelope = cell_rig.queen.envelope_for(refusal, correlation_id=announced.id)

    await handle_tool_item(cell_rig.warden, to_inbox_item(envelope, QUEEN_LINK), None)

    with pytest.raises(PromotionRefusedError, match=r"hive\.registry\.version_exists"):
        await job
    assert await registry.list_tools() == ()


async def _installed_as_announced(rig: ToolRig, announced: Envelope) -> None:
    """Do what the announcer does on the Queen's silence: the tool enters the table and bundles."""
    message = announced.payload
    assert isinstance(message, ToolPromoted)
    rig.warden._comb_entries[message.name] = CombToolEntry.from_promoted(message)
    reattenuate(rig.warden)


async def test_a_refusal_after_the_wait_retracts_the_tool_from_the_table_and_the_bundles(
    cell_rig: ToolRig,
) -> None:
    bee, _ = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    registry = RemoteCombRegistry(cell_rig.warden)
    spec = _cell_spec(cell_rig)
    announced, _change = await _promote_and_lapse(cell_rig, registry, spec)
    await _installed_as_announced(cell_rig, announced)
    assert bee.comb_tools is not None and [e.name for e in bee.comb_tools.entries()] == [spec.name]
    late = ErrorMessage(
        code="hive.registry.version_exists",
        message="That version already exists for this Cell.",
        failed_kind="tool.promoted",
        is_retryable=False,
    )
    envelope = cell_rig.queen.envelope_for(late, correlation_id=announced.id)

    await handle_tool_item(cell_rig.warden, to_inbox_item(envelope, QUEEN_LINK), None)

    assert spec.name not in cell_rig.warden._comb_entries
    assert bee.comb_tools.entries() == ()  # Un-installed from the bee that could call it.
    [row] = await cell_rig.warden._deps.trail.query(TrailQuery(kind="tool.load_refused"))
    assert row.payload["code"] == RETRACTED and "version_exists" in str(row.payload["reason"])
    assert cell_rig.warden._tool_relay.take_announced(announced.id) is None  # Retracted once.


async def test_a_late_refusal_puts_back_the_version_the_promotion_displaced(
    cell_rig: ToolRig,
) -> None:
    bee, _ = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    registry = RemoteCombRegistry(cell_rig.warden)
    first = _cell_spec(cell_rig, version="1.0.0")
    second = _cell_spec(cell_rig, version="1.1.0", tool_id=first.tool_id)
    first_announced, _ = await _promote_and_lapse(cell_rig, registry, first)
    await _installed_as_announced(cell_rig, first_announced)
    second_announced, _ = await _promote_and_lapse(cell_rig, registry, second)
    await _installed_as_announced(cell_rig, second_announced)
    late = ErrorMessage(
        code="hive.registry.version_exists",
        message="No.",
        failed_kind="tool.promoted",
        is_retryable=False,
    )
    envelope = cell_rig.queen.envelope_for(late, correlation_id=second_announced.id)

    await handle_tool_item(cell_rig.warden, to_inbox_item(envelope, QUEEN_LINK), None)

    assert cell_rig.warden._comb_entries[first.name].version == "1.0.0"
    assert bee.comb_tools is not None
    assert [e.version for e in bee.comb_tools.entries()] == ["1.0.0"]


async def test_the_registrys_own_rules_stop_a_promotion_before_it_is_sent(
    cell_rig: ToolRig,
) -> None:
    registry = RemoteCombRegistry(cell_rig.warden)
    spec = _cell_spec(cell_rig)
    report, offer = make_offer(spec)
    failing = make_failing_report(spec, offer.package_sha256, offer.package_size_bytes)
    other_cell = make_tool_spec(cell_rig.clock, scope=ToolScope.CELL)

    with pytest.raises(ReportFailedError):
        await registry.promote(spec, failing, offer, _principal(cell_rig), "No.")
    with pytest.raises(WrongPrincipalError):
        await registry.promote(spec, report, offer, QueenPrincipal(), "No.")
    with pytest.raises(WrongCellError):
        other_report, other_offer = make_offer(other_cell)
        await registry.promote(other_cell, other_report, other_offer, _principal(cell_rig), "No.")

    await cell_rig.warden._deps.queen_link.close()
    with pytest.raises(StopAsyncIteration):
        await cell_rig.queen.next()  # Nothing was announced.


async def test_an_unreachable_queen_refuses_the_promotion(cell_rig: ToolRig) -> None:
    registry = RemoteCombRegistry(cell_rig.warden)
    spec = _cell_spec(cell_rig)
    report, offer = make_offer(spec)
    await cell_rig.queen.transport.close()

    with pytest.raises(PromotionRefusedError, match="unreachable"):
        await registry.promote(spec, report, offer, _principal(cell_rig), "Ok.")


async def test_a_cell_cannot_retire_or_roll_back_and_reads_only_what_it_promoted(
    cell_rig: ToolRig,
) -> None:
    registry = RemoteCombRegistry(cell_rig.warden)
    key = ToolKey("anything", ToolScope.CELL, _principal(cell_rig).cell_id)

    with pytest.raises(NotImplementedError, match="operator"):
        await registry.retire(key, "1.0.0", _principal(cell_rig), "Why.")
    with pytest.raises(NotImplementedError, match="operator"):
        await registry.rollback(key, "1.0.0", _principal(cell_rig), "Why.")
    with pytest.raises(ToolNotFoundError):
        await registry.get("anything", ToolScope.CELL, key.cell_id)
