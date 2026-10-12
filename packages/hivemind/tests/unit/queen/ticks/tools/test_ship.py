"""Tests for hivemind.queen.ticks.tools.transfer: shipping a Basket blob out to a Warden.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/transfer/ship.py and drain.py (codingrules section 3).
    The rig's Warden is a `FakeWarden` that verifies and keeps what is shipped with the real
    `TransferReceiver` and acknowledges through the Queen's own handlers, so the offer, the chunks
    and the acks really cross; a Warden that never answers, or refuses, is a script away.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools.transfer for the modules under test.
    - builders.queen_tools for the rig.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from builders.basket import make_deposit_request
from builders.queen_tools import ToolRig, WardenScript, make_tool_rig

from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.ticks.tools import handle_tool_item
from hivemind.queen.ticks.tools.transfer import (
    DRAIN_INTERVAL_S,
    ShipResult,
    drain_shipments,
    ship,
    ship_blob,
)
from waggle.ids import MessageId
from waggle.messages.basket import BasketAck

_BIG = bytes(range(256)) * 2_400  # 614_400 bytes: three 256 KiB chunks (the last one short).


async def _keep(rig: ToolRig, content: bytes = _BIG) -> str:
    """Deposit `content` in the Basket and return its hash."""
    await rig.access.deposits.deposit(make_deposit_request(content))
    return hashlib.sha256(content).hexdigest()


async def _count(rig: ToolRig, kind: str) -> int:
    return len(await rig.deps.trail.query(TrailQuery(kind=kind)))


async def test_a_blob_is_offered_then_sent_in_chunks_and_kept_by_the_warden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    digest = await _keep(rig)

    shipped = await ship_blob(rig.deps, rig.link, digest, "a test shipment")

    assert shipped is True
    assert rig.peer.blobs[digest] == _BIG
    assert rig.peer.seen == ["basket.offer", "basket.chunk", "basket.chunk", "basket.chunk"]
    assert await _count(rig, "basket.offered") == 1 and await _count(rig, "basket.shipped") == 1
    lane = rig.deps.tool_lane
    assert lane.transfers == {} and lane.shipped == {}
    await rig.aclose()


async def test_two_requests_for_one_blob_to_one_warden_share_a_single_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    digest = await _keep(rig, b"a small blob, twice requested")

    results = await asyncio.gather(
        ship_blob(rig.deps, rig.link, digest, "first"),
        ship_blob(rig.deps, rig.link, digest, "second"),
    )

    assert list(results) == [True, True] and rig.peer.seen.count("basket.offer") == 1
    await rig.aclose()


async def test_a_warden_that_refuses_the_blob_ends_the_shipment_and_it_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, script=WardenScript(refuse_offer=True))
    digest = await _keep(rig, b"a blob nobody wants")

    result = await ship(rig.deps, rig.link, digest, "a test shipment")

    assert result is ShipResult.REFUSED and digest not in rig.peer.blobs
    (event,) = await rig.deps.trail.query(TrailQuery(kind="basket.refused"))
    assert event.payload["code"] == "CLEARANCE"
    assert rig.deps.tool_lane.transfers == {}
    await rig.aclose()


async def test_a_warden_that_never_answers_times_the_shipment_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(
        tmp_path, monkeypatch, script=WardenScript(answers=False), request_timeout_s=0.3
    )
    digest = await _keep(rig, b"a blob sent into silence")

    result = await ship(rig.deps, rig.link, digest, "a test shipment")

    assert result is ShipResult.FAILED
    assert rig.deps.tool_lane.transfers == {} and rig.deps.tool_lane.shipped == {}
    await rig.aclose()


async def test_a_blob_the_basket_does_not_hold_is_refused_without_an_offer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)

    result = await ship(rig.deps, rig.link, "c" * 64, "a test shipment")

    assert result is ShipResult.REFUSED and rig.peer.seen == []
    await rig.aclose()


async def test_a_link_that_has_closed_fails_the_shipment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    digest = await _keep(rig, b"a blob for a Warden that left")
    await rig.link.transport.close()

    result = await ship(rig.deps, rig.link, digest, "a test shipment")

    assert result is ShipResult.FAILED and await _count(rig, "basket.offered") == 0
    await rig.aclose()


async def test_an_ack_that_contradicts_the_offer_ends_the_shipment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, script=WardenScript(answers=False))
    digest = await _keep(rig, b"a short blob")
    shipping = asyncio.ensure_future(ship(rig.deps, rig.link, digest, "a test shipment"))
    offer = await rig.peer.next()  # The offer.

    # Claims more bytes than the blob has: a protocol error, so the shipment is over.
    lying = BasketAck(sha256=digest, received_bytes=10_000, complete=False, reason="")
    await rig.send(lying, MessageId(offer.id))

    assert await shipping is ShipResult.FAILED
    await rig.aclose()


async def test_an_ack_for_no_shipment_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    late = BasketAck(sha256="d" * 64, received_bytes=0, complete=False, reason="")

    unknown = MessageId("msg_01DXF6DT00CCCCCCCCCCCCCCCC")
    handled = await handle_tool_item(rig.deps, rig.wardens, rig.item(late, unknown))

    assert handled is True and rig.peer.seen == []
    await rig.aclose()


async def test_a_queued_shipment_goes_to_its_cells_warden_and_is_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    digest = await _keep(rig, b"an installer an operator wants on that Cell")
    shipment = await rig.kit.store.queue_shipment(digest, rig.link.cell.id, "operator asked")

    await drain_shipments(rig.deps, rig.wardens.values())
    await rig.settle()

    assert rig.peer.blobs[digest] == b"an installer an operator wants on that Cell"
    assert shipment.id not in {row.id for row in await rig.kit.store.pending_shipments()}
    assert rig.deps.tool_lane.draining == set()
    await rig.aclose()


async def test_a_queued_shipment_waits_for_a_warden_that_is_not_here_and_for_the_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    digest = await _keep(rig, b"a blob for a Cell nobody is attached for")
    from builders.cells import make_cell

    await rig.kit.store.queue_shipment(digest, make_cell().id, "nobody home")

    await drain_shipments(rig.deps, rig.wardens.values())

    assert (
        rig.deps.tool_lane.background == set() and len(await rig.kit.store.pending_shipments()) == 1
    )
    # Within the interval the store is not even read again, so a new row waits for the next look.
    await rig.kit.store.queue_shipment(digest, rig.link.cell.id, "too soon")
    await drain_shipments(rig.deps, rig.wardens.values())
    assert rig.deps.tool_lane.background == set()
    rig.deps.clock.advance(DRAIN_INTERVAL_S + 1)  # type: ignore[attr-defined]
    await drain_shipments(rig.deps, rig.wardens.values())
    assert len(rig.deps.tool_lane.background) == 1
    await rig.settle()
    assert len(await rig.kit.store.pending_shipments()) == 1  # Only the absent Cell's remains.
    await rig.aclose()


async def test_a_queued_shipment_the_warden_refuses_is_completed_not_retried_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, script=WardenScript(refuse_offer=True))
    digest = await _keep(rig, b"a blob the Cell will refuse")
    await rig.kit.store.queue_shipment(digest, rig.link.cell.id, "operator asked")

    await drain_shipments(rig.deps, rig.wardens.values())
    await rig.settle()

    assert await rig.kit.store.pending_shipments() == ()
    assert await _count(rig, "basket.refused") == 1
    await rig.aclose()


async def test_a_queued_shipment_that_times_out_stays_pending_for_another_try(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(
        tmp_path, monkeypatch, script=WardenScript(answers=False), request_timeout_s=0.3
    )
    digest = await _keep(rig, b"a blob sent into silence")
    await rig.kit.store.queue_shipment(digest, rig.link.cell.id, "operator asked")

    await drain_shipments(rig.deps, rig.wardens.values())
    await rig.settle()

    assert len(await rig.kit.store.pending_shipments()) == 1
    assert rig.deps.tool_lane.draining == set()
    await rig.aclose()
