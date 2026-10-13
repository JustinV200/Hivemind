"""Tests for the Royal Jelly Lab and Basket rows of hivemind.wardens.autopilot.table (phase 9).

Fits into the Hive:
    Mirrors the tool and Basket branches of src/hivemind/wardens/autopilot/table.py
    (codingrules section 3); the older rows are tested in test_table.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.autopilot.table for the module under test.
    - hivemind.wardens.ticks.dispatch for where each action is carried out.
"""

from __future__ import annotations

import pytest
from builders.basket import make_manifest
from builders.comb_tools import make_comb_package, make_promoted
from builders.royal_jelly import make_failing_report, make_tool_request, make_tool_spec
from builders.supervision import make_inbox_item, make_policy

from hivemind.basket import Offer, offer_to_wire
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens.autopilot import WardenAction, decide
from waggle.clock import FakeClock
from waggle.messages.base import WaggleMessage
from waggle.messages.basket import BasketAck, BasketChunk, BasketRefusal, BasketRefuse
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.tool import ToolPromoted, ToolQuarantine, ToolQuarantineReport
from waggle.messages.tool.authoring import QuarantinePath, ToolScope

_CLOCK = FakeClock()
_SHA = "a" * 64


def _item(payload: WaggleMessage) -> InboxItem:
    return make_inbox_item(clock=_CLOCK, payload=payload, payload_kind="test.payload")


def _error(failed_kind: str | None) -> ErrorMessage:
    return ErrorMessage(
        code="hive.tool.rejected",
        message="The Queen refused something about a tool.",
        failed_kind=failed_kind,
        is_retryable=False,
    )


def _report() -> ToolQuarantineReport:
    spec = make_tool_spec(_CLOCK)
    failing = make_failing_report(spec, _SHA, 10)
    return ToolQuarantineReport(
        tool_id=spec.tool_id,
        version=spec.version,
        passed=False,
        path=QuarantinePath.SANDBOX_CELL,
        sandbox_cell_id=failing.sandbox_cell_id,
        failing_gate="tests",
        report_json=failing.to_json(),
        reason="a gate failed",
    )


def test_a_tool_request_a_promotion_and_a_report_are_forwarded() -> None:
    spec = make_tool_spec(_CLOCK)
    order = ToolQuarantine(
        tool_id=spec.tool_id,
        name=spec.name,
        version=spec.version,
        scope=ToolScope.HIVE,
        cell_id=None,
        spec_json=spec.to_json(),
        package_sha256=_SHA,
        package_size_bytes=10,
        path=QuarantinePath.SANDBOX_CELL,
        limits_json="{}",
        requested_by_warden=spec.requested_by,
        reason="prove it",
    )

    assert (
        decide(_item(make_tool_request(_CLOCK)), None, make_policy()) is WardenAction.FORWARD_TOOL
    )
    assert decide(_item(_report()), None, make_policy()) is WardenAction.FORWARD_TOOL
    assert decide(_item(order), None, make_policy()) is WardenAction.RUN_QUARANTINE


@pytest.mark.parametrize("failed_kind", ["tool.request", "tool.quarantine", "tool.promoted"])
def test_an_error_about_a_tool_message_is_forwarded_not_judged(failed_kind: str) -> None:
    assert decide(_item(_error(failed_kind)), None, make_policy()) is WardenAction.FORWARD_TOOL


@pytest.mark.parametrize("failed_kind", ["task.result", "honey.query", None])
def test_any_other_error_still_reaches_judgement(failed_kind: str | None) -> None:
    assert decide(_item(_error(failed_kind)), None, make_policy()) is WardenAction.NEEDS_JUDGEMENT


def test_every_basket_payload_maps_to_basket() -> None:
    ack = BasketAck(sha256=_SHA, received_bytes=0, complete=False, reason="")
    refuse = BasketRefuse(sha256=_SHA, code=BasketRefusal.NO_ROOM, reason="full")
    chunk = BasketChunk(sha256=_SHA, offset=0, data="eA==", is_last=True)
    manifest = make_manifest()
    offer = offer_to_wire(
        Offer(manifest=manifest, destination_cell_id=manifest.blob.origin_cell_id)
    )

    for payload in (offer, ack, refuse, chunk):
        assert decide(_item(payload), None, make_policy()) is WardenAction.BASKET


def test_a_promotion_is_forwarded_for_install() -> None:
    promoted: ToolPromoted = make_promoted(make_comb_package(), _CLOCK)

    assert decide(_item(promoted), None, make_policy()) is WardenAction.FORWARD_TOOL
