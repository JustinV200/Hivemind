"""Tests for hivemind.wardens.ticks.tools.relay: tool requests and answers through a Warden.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/tools/relay.py (codingrules section 3). Drives the relay
    directly against a started Warden, reading both of its links raw (the Queen's end of the
    queen link and a sub-bee's end of its own), so the envelope ids, addresses and correlations of
    every hop are asserted, not just the payloads.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.tools.relay for the module under test.
    - test_honey.py (the parent directory) for the Honey relay this one is shaped like.
"""

from __future__ import annotations

import pytest
from builders.comb_tools import make_comb_package, make_promoted
from builders.royal_jelly import make_tool_request
from builders.wardens_tools import ToolRig, add_sub_bee, make_resident

from hivemind.pheromone import TrailQuery
from hivemind.wardens.inbox import to_inbox_item
from hivemind.wardens.links import QUEEN_LINK
from hivemind.wardens.spawn import SubBee
from hivemind.wardens.ticks.tools import handle_tool_item
from hivemind.wardens.ticks.tools.messages import LOAD_REFUSED, REQUEST_REFUSED
from hivemind.wardens.ticks.tools.relay import QUEEN_UNREACHABLE_REASON
from waggle.ids import new_cell_id, new_message_id, new_task_id, new_warden_id, new_worker_id
from waggle.messages.control import ErrorMessage
from waggle.messages.tool import ToolPromoted, ToolRequest
from waggle.messages.tool.authoring import ToolScope


def _request(hive_rig: ToolRig, sub_bee: SubBee, **overrides: object) -> ToolRequest:
    """Build an echo-tool request that names this rig's Warden, Cell and the given sub-bee."""
    cell = hive_rig.warden._cell
    assert cell is not None
    fields: dict[str, object] = {
        "warden_id": hive_rig.warden._warden_id,
        "worker_id": sub_bee.worker_id,
        "task_id": sub_bee.task_id,
        "cell_id": cell.id,
    }
    fields.update(overrides)
    return make_tool_request(hive_rig.clock, **fields)


def _promoted_for(request: ToolRequest) -> tuple[ToolPromoted, bytes]:
    """Build the announcement answering `request` and the real package it names."""
    package = make_comb_package(name=request.name)
    promoted = make_promoted(package).model_copy(update={"tool_id": request.tool_id})
    return promoted, package.package


async def _assert_queen_got_nothing(hive_rig: ToolRig) -> None:
    """Close the Warden's queen link: anything it sent arrives first, then the stream ends."""
    await hive_rig.warden._deps.queen_link.close()
    with pytest.raises(StopAsyncIteration):
        await hive_rig.queen.next()


async def test_a_hive_request_goes_up_unchanged_and_its_installed_promotion_comes_back(
    hive_rig: ToolRig,
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    request = _request(hive_rig, sub_bee)
    promoted, package = _promoted_for(request)
    await make_resident(hive_rig, package)
    asked = worker.envelope_for(request)

    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)
    forwarded = await hive_rig.queen.next()
    answer = hive_rig.queen.envelope_for(promoted, correlation_id=forwarded.id)
    await handle_tool_item(hive_rig.warden, to_inbox_item(answer, QUEEN_LINK), None)
    relayed = await worker.next()

    assert forwarded.payload == request
    assert (forwarded.id, forwarded.sender) != (asked.id, asked.sender)
    assert forwarded.correlation_id is None
    assert relayed.payload == promoted
    assert relayed.correlation_id == asked.id
    assert relayed.recipient == sub_bee.worker_id
    # Installed before it was forwarded: the asker's bundle holds the tool when it hears of it.
    assert sub_bee.comb_tools is not None
    assert [entry.name for entry in sub_bee.comb_tools.entries()] == [promoted.name]
    assert hive_rig.warden._comb_entries[promoted.name].package_sha256 == promoted.package_sha256
    assert len(hive_rig.warden._tool_relay) == 0


@pytest.mark.parametrize("field", ["worker_id", "task_id", "warden_id", "cell_id"])
async def test_a_request_with_another_identity_is_refused_here_and_never_forwarded(
    hive_rig: ToolRig, field: str
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    other = {
        "worker_id": new_worker_id(hive_rig.clock),
        "task_id": new_task_id(hive_rig.clock),
        "warden_id": new_warden_id(hive_rig.clock),
        "cell_id": new_cell_id(hive_rig.clock),
    }[field]
    request = _request(hive_rig, sub_bee, **{field: other})
    asked = worker.envelope_for(request)

    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)

    refused = await worker.next()
    assert isinstance(refused.payload, ErrorMessage)
    assert refused.payload.code == REQUEST_REFUSED
    assert refused.payload.failed_kind == "tool.request"
    assert refused.correlation_id == asked.id
    await _assert_queen_got_nothing(hive_rig)


async def test_a_queen_error_about_a_forwarded_request_reaches_its_asker(hive_rig: ToolRig) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    request = _request(hive_rig, sub_bee)
    asked = worker.envelope_for(request)
    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)
    forwarded = await hive_rig.queen.next()
    error = ErrorMessage(
        code="hive.tool.rejected",
        message="The tool failed the static gate of the Quarantine Comb.",
        failed_kind="tool.request",
        is_retryable=False,
    )

    answer = hive_rig.queen.envelope_for(error, correlation_id=forwarded.id)
    await handle_tool_item(hive_rig.warden, to_inbox_item(answer, QUEEN_LINK), None)

    relayed = await worker.next()
    assert relayed.payload == error
    assert relayed.correlation_id == asked.id
    assert len(hive_rig.warden._tool_relay) == 0


async def test_an_unreachable_queen_answers_the_asker_at_once_and_forgets_the_request(
    hive_rig: ToolRig,
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    await hive_rig.queen.transport.close()
    asked = worker.envelope_for(_request(hive_rig, sub_bee))

    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)

    answered = await worker.next()
    assert isinstance(answered.payload, ErrorMessage)
    assert answered.payload.message == QUEEN_UNREACHABLE_REASON
    assert answered.payload.is_retryable
    assert answered.correlation_id == asked.id
    assert len(hive_rig.warden._tool_relay) == 0


async def test_a_promotion_whose_package_is_not_resident_is_never_forwarded(
    hive_rig: ToolRig,
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    request = _request(hive_rig, sub_bee)
    promoted, _package = _promoted_for(request)  # Its package is never made resident.
    asked = worker.envelope_for(request)
    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)
    forwarded = await hive_rig.queen.next()

    answer = hive_rig.queen.envelope_for(promoted, correlation_id=forwarded.id)
    await handle_tool_item(hive_rig.warden, to_inbox_item(answer, QUEEN_LINK), None)

    relayed = await worker.next()
    assert isinstance(relayed.payload, ErrorMessage)
    assert relayed.payload.code == LOAD_REFUSED
    assert relayed.correlation_id == asked.id
    assert sub_bee.comb_tools is not None and sub_bee.comb_tools.entries() == ()
    [refusal] = await hive_rig.warden._deps.trail.query(TrailQuery(kind="tool.load_refused"))
    assert refusal.payload["package_sha256"] == promoted.package_sha256


async def test_an_announcement_nobody_asked_for_is_still_installed(hive_rig: ToolRig) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    promoted, package = _promoted_for(_request(hive_rig, sub_bee))
    await make_resident(hive_rig, package)

    await handle_tool_item(
        hive_rig.warden,
        to_inbox_item(
            hive_rig.queen.envelope_for(promoted, correlation_id=new_message_id(hive_rig.clock)),
            QUEEN_LINK,
        ),
        None,
    )

    assert sub_bee.comb_tools is not None
    assert [entry.name for entry in sub_bee.comb_tools.entries()] == [promoted.name]
    await sub_bee.link.close()
    with pytest.raises(StopAsyncIteration):
        await worker.next()  # Nothing was forwarded: nobody had asked.


async def test_traffic_arriving_the_wrong_way_is_dropped(hive_rig: ToolRig) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    request = _request(hive_rig, sub_bee)
    promoted, _package = _promoted_for(request)
    from_queen = hive_rig.queen.envelope_for(request)
    from_worker = worker.envelope_for(promoted, correlation_id=new_message_id(hive_rig.clock))

    await handle_tool_item(hive_rig.warden, to_inbox_item(from_queen, QUEEN_LINK), None)
    await handle_tool_item(hive_rig.warden, to_inbox_item(from_worker, sub_bee.worker_id), sub_bee)

    await _assert_queen_got_nothing(hive_rig)
    assert hive_rig.warden._comb_entries == {}


async def test_a_reply_nobody_awaits_is_dropped_never_raised(hive_rig: ToolRig) -> None:
    error = ErrorMessage(
        code="hive.tool.sandbox_unavailable",
        message="No sandbox could be provisioned for the request.",
        failed_kind="tool.quarantine",
        is_retryable=True,
    )
    stray = hive_rig.queen.envelope_for(error, correlation_id=new_message_id(hive_rig.clock))

    await handle_tool_item(
        hive_rig.warden, to_inbox_item(stray, QUEEN_LINK), None
    )  # must not raise

    await _assert_queen_got_nothing(hive_rig)


async def test_a_cell_scope_request_is_never_forwarded_to_the_queen(hive_rig: ToolRig) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock, capabilities=("tool:*",))
    request = _request(hive_rig, sub_bee, scope=ToolScope.CELL)
    asked = worker.envelope_for(request)

    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)

    refused = await worker.next()  # The asker holds no tool:request, so the Warden refuses itself.
    assert isinstance(refused.payload, ErrorMessage)
    assert refused.payload.code == REQUEST_REFUSED
    await _assert_queen_got_nothing(hive_rig)


async def test_the_uncorrelated_announcement_and_the_answer_forward_exactly_one_promotion(
    hive_rig: ToolRig,
) -> None:
    sub_bee, worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    request = _request(hive_rig, sub_bee)
    promoted, package = _promoted_for(request)
    await make_resident(hive_rig, package)
    asked = worker.envelope_for(request)
    await handle_tool_item(hive_rig.warden, to_inbox_item(asked, sub_bee.worker_id), sub_bee)
    forwarded = await hive_rig.queen.next()
    announcement = hive_rig.queen.envelope_for(promoted)  # The Queen's event: no correlation.
    answer = hive_rig.queen.envelope_for(promoted, correlation_id=forwarded.id)

    await handle_tool_item(hive_rig.warden, to_inbox_item(announcement, QUEEN_LINK), None)
    await handle_tool_item(hive_rig.warden, to_inbox_item(answer, QUEEN_LINK), None)

    relayed = await worker.next()
    assert relayed.payload == promoted and relayed.correlation_id == asked.id
    await sub_bee.link.close()
    with pytest.raises(StopAsyncIteration):
        await worker.next()  # The second promotion was installed again (a no-op), not forwarded.
    assert sub_bee.comb_tools is not None and sub_bee.comb_tools.version == 1
