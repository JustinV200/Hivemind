"""Tests for the Royal Jelly Lab and Basket traffic through a running Warden's own tick.

Fits into the Hive:
    Exercises src/hivemind/wardens/warden.py's tick end to end for phase 9: a message arrives on a
    link, the Attendant orders it, autopilot decides it, `dispatch.act` routes it and the handler
    answers over the wire. The handlers themselves are tested one by one under
    tests/unit/wardens/ticks/tools and tests/unit/wardens/ticks/basket.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.warden.Warden for the tick under test.
    - hivemind.wardens.autopilot.table for the rows that route these messages.
"""

from __future__ import annotations

import asyncio

from builders.basket import make_blob, make_manifest
from builders.comb_tools import make_comb_package, make_promoted
from builders.royal_jelly import make_package, make_tool_request, make_tool_spec
from builders.wardens_tools import ToolRig, add_sub_bee, make_resident

from hivemind.basket import TransferSender, ack_from_wire, chunk_to_wire, offer_to_wire
from hivemind.basket.transfer import BytesChunkSource
from hivemind.royal_jelly import QuarantineLimits, sha256_of
from waggle.messages.basket import BasketAck
from waggle.messages.tool import ToolQuarantine, ToolQuarantineReport
from waggle.messages.tool.authoring import QuarantinePath


def _run(rig: ToolRig) -> asyncio.Task[None]:
    """Start the rig's Warden tick loop; call after every sub-bee is registered, as spawn does."""
    return asyncio.ensure_future(rig.warden.run())


async def _stop(rig: ToolRig, task: asyncio.Task[None]) -> None:
    """Stop the Warden and wait for its loop to end, bounded."""
    await rig.warden.stop()
    await asyncio.wait_for(task, timeout=5.0)


async def test_a_sub_bees_request_goes_up_and_the_promotion_comes_back_down_the_wire(
    hive_rig: ToolRig,
) -> None:
    rig = hive_rig
    sub_bee, worker = await add_sub_bee(rig.warden, rig.clock)
    rig.warden._sub_bee_iters[sub_bee.worker_id] = sub_bee.link.receive()
    cell = rig.warden._cell
    assert cell is not None
    request = make_tool_request(
        rig.clock,
        warden_id=rig.warden._warden_id,
        worker_id=sub_bee.worker_id,
        task_id=sub_bee.task_id,
        cell_id=cell.id,
    )
    package = make_comb_package(name=request.name)
    await make_resident(rig, package.package)
    promoted = make_promoted(package).model_copy(update={"tool_id": request.tool_id})
    task = _run(rig)

    asked = await worker.send(request)
    forwarded = await rig.queen.next()
    await rig.queen.send(promoted, correlation_id=forwarded.id)
    answer = await worker.next()

    assert forwarded.payload == request
    assert answer.payload == promoted and answer.correlation_id == asked.id
    await _stop(rig, task)


async def test_a_queen_sent_order_runs_the_comb_and_the_report_comes_back_correlated(
    hive_rig: ToolRig,
) -> None:
    rig = hive_rig
    spec = make_tool_spec(required_capabilities=("fs:read:*",))
    package = make_package(spec=spec)
    await make_resident(rig, package)
    order = ToolQuarantine(
        tool_id=spec.tool_id,
        name=spec.name,
        version=spec.version,
        scope=spec.scope,
        cell_id=None,
        spec_json=spec.to_json(),
        package_sha256=sha256_of(package),
        package_size_bytes=len(package),
        path=QuarantinePath.SANDBOX_CELL,
        limits_json=QuarantineLimits(wall_s=20, fuzz_cases=4).to_json(),
        requested_by_warden=rig.warden._warden_id,
        reason="Prove it.",
    )

    task = _run(rig)

    sent = await rig.queen.send(order)
    reply = await rig.queen.next(timeout_s=60.0)
    await _stop(rig, task)

    assert isinstance(reply.payload, ToolQuarantineReport) and reply.payload.passed
    assert reply.correlation_id == sent.id


async def test_a_cells_warden_takes_a_shipment_over_the_wire(cell_rig: ToolRig) -> None:
    content = b"a wheel set, more or less" * 100
    task = _run(cell_rig)
    cell = cell_rig.warden._cell
    assert cell is not None
    manifest = make_manifest(
        make_blob(content, origin_cell_id=cell.id),
        signer=cell_rig.hive_signer,
        signer_id=cell_rig.hive_signer_id,
    )
    sender = TransferSender(BytesChunkSource(content))

    first = await cell_rig.queen.send(offer_to_wire(sender.offer(manifest, cell.id, "ship")))
    ack = await cell_rig.queen.next()
    assert isinstance(ack.payload, BasketAck) and ack.correlation_id == first.id
    chunks = sender.on_ack(ack_from_wire(ack.payload)) or ()
    await cell_rig.queen.send(chunk_to_wire(chunks[0]))
    done = await cell_rig.queen.next()

    assert isinstance(done.payload, BasketAck) and done.payload.complete
    resident = cell_rig.warden._deps.resident_basket
    assert resident is not None and await resident.bytes_for(manifest.blob.sha256) == content
    await _stop(cell_rig, task)
