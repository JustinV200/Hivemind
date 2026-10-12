"""Tests for hivemind.wardens.ticks.tools.cell_scope (with ports and remote_registry).

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/tools/cell_scope.py. Runs the whole CELL-scope path against
    a started Warden with a scripted scaffolder model and the Queen played by the test (and, for a
    Cell's Warden, by `builders.wardens.QueenDouble`, which runs the real Basket receiver): a
    request in, a package deposited and shipped, a `tool.quarantine` out, a passing report back,
    a promotion (in process on the Hive Stand, announced to the Queen from a Cell), the tool in the
    asker's bundle and the promotion forwarded to it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.tools.cell_scope, .ports and .remote_registry for the modules tested.
    - tests/unit/royal_jelly/promotion/test_flow.py for the flow itself.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from builders.honey_wire import WireEnd
from builders.royal_jelly import make_failing_report, make_passing_report, make_tool_request
from builders.wardens_tools import QueenDouble, ToolRig, add_sub_bee, make_tool_rig

from hivemind.llm import LLMResponse, text_response
from hivemind.royal_jelly import ToolSpec
from hivemind.wardens.inbox import to_inbox_item
from hivemind.wardens.links import QUEEN_LINK
from hivemind.wardens.spawn import SubBee
from hivemind.wardens.ticks.tools import handle_tool_item
from hivemind.wardens.ticks.tools.messages import NO_SCAFFOLDER, REJECTED, REQUEST_REFUSED
from waggle.envelope import Envelope
from waggle.ids import MessageId
from waggle.messages.control import ErrorMessage
from waggle.messages.honey import NectarDeposit
from waggle.messages.tool import ToolPromoted, ToolQuarantine, ToolQuarantineReport, ToolRequest
from waggle.messages.tool.authoring import ToolScope
from waggle.signing import Ed25519Verifier

_SCHEMA = json.dumps(
    {"type": "object", "properties": {"text": {"type": "string"}}, "additionalProperties": False}
)
_TOOL = "async def run(session, arguments):\n    return {'echo': arguments}\n"
_TESTS = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"


def _scaffold_reply() -> LLMResponse:
    """Script one valid scaffold reply: an echo tool that needs `net:example.org`."""
    return text_response(
        json.dumps(
            {
                "description": "Echo the text argument back.",
                "input_schema_json": _SCHEMA,
                "test_source": _TESTS,
                "tool_source": _TOOL,
                "version": "1.0.0",
                "required_capabilities": ["net:example.org"],
                "notes": "",
            }
        )
    )


def _cell_request(rig: ToolRig, sub_bee: SubBee) -> ToolRequest:
    """Build a CELL-scope request that names this rig's Warden, Cell and the given sub-bee."""
    cell = rig.warden._cell
    assert cell is not None
    return make_tool_request(
        rig.clock,
        warden_id=rig.warden._warden_id,
        worker_id=sub_bee.worker_id,
        task_id=sub_bee.task_id,
        cell_id=cell.id,
        scope=ToolScope.CELL,
    )


def _verdict(order: ToolQuarantine, failing_gate: str | None = None) -> ToolQuarantineReport:
    """Answer an order as a Queen whose sandbox passed every gate, or stopped at `failing_gate`."""
    spec = ToolSpec.from_json(order.spec_json)
    size = order.package_size_bytes
    if failing_gate is None:
        report = make_passing_report(spec, order.package_sha256, size, path=order.path)
    else:
        report = make_failing_report(
            spec, order.package_sha256, size, failing_gate=failing_gate, path=order.path
        )
    return ToolQuarantineReport(
        tool_id=spec.tool_id,
        version=spec.version,
        passed=failing_gate is None,
        path=report.path,
        sandbox_cell_id=report.sandbox_cell_id,
        failing_gate=failing_gate,
        report_json=report.to_json(),
        reason="every gate passed" if failing_gate is None else "a gate failed",
    )


async def _answer_from_queen(rig: ToolRig, payload: object, request_id: MessageId) -> None:
    """Deliver `payload` to the Warden as the Queen's reply to the envelope `request_id`."""
    envelope = rig.queen.envelope_for(payload, correlation_id=request_id)  # type: ignore[arg-type]
    await handle_tool_item(rig.warden, to_inbox_item(envelope, QUEEN_LINK), None)


async def _finish(rig: ToolRig) -> None:
    """Wait for every background job, advancing the fake clock so a short wait can lapse."""
    jobs = tuple(rig.warden._tool_jobs)
    pending = asyncio.gather(*jobs)
    while not pending.done():
        rig.clock.advance(2.0)  # Wakes a 2-second refusal wait; the 600-second ones stay asleep.
        await asyncio.sleep(0)
    await pending


async def _next_of(queen: WireEnd | QueenDouble, kind: type) -> Envelope:
    """Return the next envelope whose payload is a `kind`, skipping (and keeping) any others."""
    while True:
        envelope = await (queen.next_other() if isinstance(queen, QueenDouble) else queen.next())
        if isinstance(envelope.payload, kind):
            return envelope


async def test_a_hive_stand_warden_promotes_a_cell_tool_in_process_and_hands_it_to_its_asker(
    hive_rig: ToolRig,
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    hive_rig.provider.script(_scaffold_reply())
    asked = worker.envelope_for(_cell_request(hive_rig, sub_bee))

    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)
    order_env = await _next_of(hive_rig.queen, ToolQuarantine)
    order = order_env.payload
    assert isinstance(order, ToolQuarantine)
    await _answer_from_queen(hive_rig, _verdict(order), order_env.id)
    await _finish(hive_rig)

    promoted_env = await worker.next()
    promoted = promoted_env.payload
    assert isinstance(promoted, ToolPromoted)
    assert promoted_env.correlation_id == asked.id
    assert (promoted.scope, promoted.cell_id) == (ToolScope.CELL, hive_rig.warden._cell.id)  # type: ignore[union-attr]
    assert promoted.promoted_by_warden == hive_rig.warden._warden_id
    # The package was in the Hive's store before the Queen was asked to prove it (no shipping).
    assert await hive_rig.store.open_bytes(order.package_sha256)
    # Promoted in process by this Warden, as itself, through the Hive's own registry.
    assert hive_rig.access is not None
    row = await hive_rig.access.registry.get(promoted.name, ToolScope.CELL, promoted.cell_id)
    assert row.promoted_by == str(hive_rig.warden._warden_id)
    # The asker's bundle holds it, and the promotion was described to Honey as Nectar.
    assert sub_bee.comb_tools is not None
    assert [entry.name for entry in sub_bee.comb_tools.entries()] == [promoted.name]
    nectar = (await _next_of(hive_rig.queen, NectarDeposit)).payload
    assert isinstance(nectar, NectarDeposit) and nectar.worker_id is None


async def test_a_cells_warden_ships_the_package_up_and_announces_its_promotion_to_the_queen(
    cell_rig: ToolRig, tmp_path: Path
) -> None:
    sub_bee, worker = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    cell_rig.provider.script(_scaffold_reply())
    verifier = Ed25519Verifier({cell_rig.cell_signer_id: cell_rig.cell_signer.public_key_bytes})
    queen = QueenDouble(cell_rig, verifier, tmp_path / "queen-spool")
    asked = worker.envelope_for(_cell_request(cell_rig, sub_bee))

    await handle_tool_item(cell_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)
    order_env = await _next_of(queen, ToolQuarantine)
    order = order_env.payload
    assert isinstance(order, ToolQuarantine)
    # The Queen holds the package, verified byte for byte, before she is asked to prove it.
    assert order.package_sha256 in queen.received
    await _answer_from_queen(cell_rig, _verdict(order), order_env.id)
    announced = await _next_of(queen, ToolPromoted)
    await _finish(cell_rig)

    promoted = announced.payload
    assert isinstance(promoted, ToolPromoted)
    assert promoted.promoted_by_warden == cell_rig.warden._warden_id
    assert promoted.scope is ToolScope.CELL
    assert (await worker.next()).payload == promoted
    assert sub_bee.comb_tools is not None and len(sub_bee.comb_tools.entries()) == 1
    # The Queen describes a Cell's tool to Honey herself when she accepts the announcement.
    assert not any(isinstance(e.payload, NectarDeposit) for e in queen.other)


async def test_a_promotion_the_queen_refuses_is_a_rejection_and_installs_nothing(
    cell_rig: ToolRig, tmp_path: Path
) -> None:
    sub_bee, worker = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    cell_rig.provider.script(_scaffold_reply())
    verifier = Ed25519Verifier({cell_rig.cell_signer_id: cell_rig.cell_signer.public_key_bytes})
    queen = QueenDouble(cell_rig, verifier, tmp_path / "queen-spool")
    asked = worker.envelope_for(_cell_request(cell_rig, sub_bee))
    await handle_tool_item(cell_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)
    order_env = await _next_of(queen, ToolQuarantine)
    assert isinstance(order_env.payload, ToolQuarantine)
    await _answer_from_queen(cell_rig, _verdict(order_env.payload), order_env.id)
    announced = await _next_of(queen, ToolPromoted)
    refusal = ErrorMessage(
        code="hive.registry.wrong_cell",
        message="The promotion names a Cell this Warden does not supervise.",
        failed_kind="tool.promoted",
        is_retryable=False,
    )

    await _answer_from_queen(cell_rig, refusal, announced.id)  # Within the two-second wait.
    await _finish(cell_rig)

    answer = (await worker.next()).payload
    assert isinstance(answer, ErrorMessage) and answer.code == REJECTED
    assert sub_bee.comb_tools is not None and sub_bee.comb_tools.entries() == ()
    assert cell_rig.warden._comb_entries == {}


async def test_a_failing_report_buys_one_retry_then_rejects_the_request(
    hive_rig: ToolRig,
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    hive_rig.provider.script(_scaffold_reply(), _scaffold_reply())
    asked = worker.envelope_for(_cell_request(hive_rig, sub_bee))
    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)

    for _attempt in range(2):
        order_env = await _next_of(hive_rig.queen, ToolQuarantine)
        assert isinstance(order_env.payload, ToolQuarantine)
        await _answer_from_queen(hive_rig, _verdict(order_env.payload, "tests"), order_env.id)
    await _finish(hive_rig)

    answer = await worker.next()
    assert isinstance(answer.payload, ErrorMessage) and answer.payload.code == REJECTED
    assert answer.correlation_id == asked.id
    assert "tests" in answer.payload.message
    assert hive_rig.access is not None
    assert await hive_rig.access.registry.list_tools() == ()


async def test_a_queen_that_cannot_provide_a_sandbox_rejects_the_request(
    hive_rig: ToolRig,
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    hive_rig.provider.script(_scaffold_reply())
    asked = worker.envelope_for(_cell_request(hive_rig, sub_bee))
    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)
    order_env = await _next_of(hive_rig.queen, ToolQuarantine)
    error = ErrorMessage(
        code="hive.tool.sandbox_unavailable",
        message="No sandbox could be provisioned for tool echo_tool.",
        failed_kind="tool.quarantine",
        is_retryable=True,
    )

    await _answer_from_queen(hive_rig, error, order_env.id)
    await _finish(hive_rig)

    answer = (await worker.next()).payload
    assert isinstance(answer, ErrorMessage) and answer.code == REJECTED
    assert "sandbox_unavailable" in answer.message


async def test_a_queen_that_never_answers_the_order_ends_in_a_rejection(
    hive_rig: ToolRig,
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    hive_rig.provider.script(_scaffold_reply())
    asked = worker.envelope_for(_cell_request(hive_rig, sub_bee))
    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)
    await _next_of(hive_rig.queen, ToolQuarantine)

    await asyncio.sleep(0)  # Let the job reach its wait before time moves.
    hive_rig.clock.advance(601.0)  # Past [royal_jelly] request_timeout_s (600 s).
    await _finish(hive_rig)

    answer = (await worker.next()).payload
    assert isinstance(answer, ErrorMessage) and answer.code == REJECTED
    assert "sandbox_unavailable" in answer.message
    assert not hive_rig.warden._tool_relay._waits  # No future is left behind.


async def test_a_grant_that_allows_no_scaffolder_slot_answers_the_asker_no_scaffolder(
    hive_rig: ToolRig,
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    grant = hive_rig.warden._grants[sub_bee.assignment.grant_id]
    worker_only = tuple(b for b in grant.allowed if b.slot == "WORKER")
    hive_rig.warden._grants[grant.grant_id] = grant.model_copy(update={"allowed": worker_only})
    asked = worker.envelope_for(_cell_request(hive_rig, sub_bee))

    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)

    answer = await worker.next()
    assert isinstance(answer.payload, ErrorMessage)
    assert (answer.payload.code, answer.payload.failed_kind) == (NO_SCAFFOLDER, "tool.request")
    assert answer.correlation_id == asked.id
    assert not hive_rig.warden._tool_jobs  # Nothing was started.


async def test_a_bee_that_may_not_ask_or_asks_for_what_it_lacks_is_refused_by_its_warden(
    hive_rig: ToolRig,
) -> None:
    mute, mute_worker = await add_sub_bee(hive_rig.warden, hive_rig.clock, capabilities=("tool:*",))
    poor, poor_worker = await add_sub_bee(
        hive_rig.warden, hive_rig.clock, capabilities=("tool:*", "tool:request")
    )
    mute_ask = mute_worker.envelope_for(_cell_request(hive_rig, mute))
    poor_ask = poor_worker.envelope_for(_cell_request(hive_rig, poor))  # Needs net:example.org.

    await handle_tool_item(hive_rig.warden, to_inbox_item(mute_ask, mute.worker_id), mute)
    await handle_tool_item(hive_rig.warden, to_inbox_item(poor_ask, poor.worker_id), poor)

    for worker, asked in ((mute_worker, mute_ask), (poor_worker, poor_ask)):
        answer = await worker.next()
        assert isinstance(answer.payload, ErrorMessage)
        assert answer.payload.code == REQUEST_REFUSED
        assert answer.correlation_id == asked.id
    assert not hive_rig.warden._tool_jobs


async def test_stopping_the_warden_cancels_a_flow_still_waiting_for_the_queen(
    tmp_path: Path,
) -> None:
    rig = await make_tool_rig(tmp_path, hive_stand=True)
    sub_bee, worker = await add_sub_bee(rig.warden, rig.clock)
    rig.provider.script(_scaffold_reply())
    await handle_tool_item(
        rig.warden,
        to_inbox_item(worker.envelope_for(_cell_request(rig, sub_bee)), sub_bee.worker_id),
        sub_bee,
    )
    await _next_of(rig.queen, ToolQuarantine)
    assert rig.warden._tool_jobs

    await rig.warden.stop()

    assert not rig.warden._tool_jobs
    assert not rig.warden._tool_relay._waits
