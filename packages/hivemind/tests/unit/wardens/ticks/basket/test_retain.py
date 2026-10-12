"""Tests for hivemind.wardens.ticks.basket.retain: depositing what a Worker retains.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/basket/retain.py (codingrules section 3). The Worker's side
    is a `RetainDeposit` handed over in process; the Hive Stand Warden deposits through the Hive's
    door (a recording Honey port shows the one record), a Cell's through its own door (the record
    is a Nectar sent up) and then ships the blob up in a background job.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.basket.retain for the module under test.
    - hivemind.workers.tools.comb.retain for the tool that hands the sink its deposit.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from builders.wardens_tools import QueenDouble, ToolRig, add_sub_bee

from hivemind.basket import BlobState
from hivemind.cell import HoneyClearance
from hivemind.pheromone import TrailQuery
from hivemind.wardens.quarantine import QuarantineRecord
from hivemind.wardens.spawn import SubBee
from hivemind.wardens.ticks.basket import WardenRetainSink, is_tainted
from hivemind.workers.tools.comb import RetainDeposit
from waggle.ids import new_event_id
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.signing import Ed25519Verifier

_REPORT = b"quarterly figures, one per line\n" * 40
_STAGED = "retained/abc123-report.csv"


async def _stage(rig: ToolRig, content: bytes = _REPORT, path: str = _STAGED) -> RetainDeposit:
    """Write a staged file into the Warden's session and describe it as the Worker would."""
    session = rig.warden._session
    assert session is not None
    await session.put_file(Path(path), content)
    return RetainDeposit(
        path=path,
        title="Quarterly report",
        summary="Per-line quarterly figures.\n\nUseful when reconciling totals.",
        media_kind="dataset",
        media_type="text/csv",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def _sink(rig: ToolRig, sub_bee: SubBee) -> WardenRetainSink:
    return WardenRetainSink(rig.warden, sub_bee.worker_id, sub_bee.task_id)


async def test_the_hive_stand_deposits_with_the_workers_provenance_and_one_honey_record(
    hive_rig: ToolRig,
) -> None:
    sub_bee, _worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    deposit = await _stage(hive_rig)

    await _sink(hive_rig, sub_bee).retain(deposit)

    blob = await hive_rig.store.get(deposit.sha256)
    cell = hive_rig.warden._cell
    assert cell is not None
    assert (blob.origin_bee, blob.origin_task_id, blob.origin_cell_id) == (
        sub_bee.worker_id,
        sub_bee.task_id,
        cell.id,
    )
    assert blob.clearance is HoneyClearance.C2  # A borrowed (Real) Cell's floor.
    assert not blob.tainted and blob.state is BlobState.PRESENT
    assert hive_rig.honey is not None
    [record] = hive_rig.honey.records
    assert (record.sha256, record.title) == (deposit.sha256, "Quarterly report")
    assert "\n" not in record.summary  # Folded into the single paragraph the record requires.
    [deposited] = await hive_rig.warden._deps.trail.query(TrailQuery(kind="basket.deposited"))
    assert deposited.payload["sha256"] == deposit.sha256


async def test_a_second_deposit_of_the_same_bytes_is_deduplicated_not_described_again(
    hive_rig: ToolRig,
) -> None:
    sub_bee, _worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    deposit = await _stage(hive_rig)

    await _sink(hive_rig, sub_bee).retain(deposit)
    await _sink(hive_rig, sub_bee).retain(deposit)

    assert hive_rig.honey is not None and len(hive_rig.honey.records) == 1
    trail = hive_rig.warden._deps.trail
    assert len(await trail.query(TrailQuery(kind="basket.deposit_deduplicated"))) == 1


async def test_a_file_that_changed_after_the_gate_verified_it_is_refused(
    hive_rig: ToolRig,
) -> None:
    sub_bee, _worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    deposit = await _stage(hive_rig)
    session = hive_rig.warden._session
    assert session is not None
    await session.put_file(Path(deposit.path), b"swapped by the worker after verification")

    await _sink(hive_rig, sub_bee).retain(deposit)  # Must not raise into the Worker's tool call.

    [refusal] = await hive_rig.warden._deps.trail.query(TrailQuery(kind="basket.deposit_refused"))
    assert refusal.payload["code"] == "hash_mismatch"
    assert refusal.payload["sha256"] == deposit.sha256
    assert hive_rig.honey is not None and hive_rig.honey.records == []
    assert await hive_rig.store.list_blobs() == ()


async def test_a_staged_file_that_is_gone_is_refused(hive_rig: ToolRig) -> None:
    sub_bee, _worker = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    deposit = (await _stage(hive_rig)).model_copy(update={"path": "retained/missing.csv"})

    await _sink(hive_rig, sub_bee).retain(deposit)

    [refusal] = await hive_rig.warden._deps.trail.query(TrailQuery(kind="basket.deposit_refused"))
    assert refusal.payload["code"] == "staged_file_missing"


async def test_a_blob_from_a_quarantined_bee_or_an_isolated_cell_is_deposited_tainted(
    hive_rig: ToolRig,
) -> None:
    quarantined, _ = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    other, _ = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    assert not is_tainted(hive_rig.warden, other.worker_id, other.task_id)
    hive_rig.warden._quarantined[quarantined.task_id] = QuarantineRecord(
        task_id=quarantined.task_id,
        bee=quarantined.worker_id,
        suspect_episode_id=new_event_id(hive_rig.clock),
        checkpoint=HandoffRef(
            event_id=new_event_id(hive_rig.clock),
            written_at=hive_rig.clock.now(),
            clearance=WireHoneyClearance.C1,
        ),
        intervened_event_id=new_event_id(hive_rig.clock),
        tainted_count=0,
        clearance=WireHoneyClearance.C1,
    )
    deposit = await _stage(hive_rig)

    await _sink(hive_rig, quarantined).retain(deposit)

    assert (await hive_rig.store.get(deposit.sha256)).tainted
    assert is_tainted(hive_rig.warden, quarantined.worker_id, quarantined.task_id)
    # Isolation taints everything the Cell deposits from then on, whichever bee it came from.
    hive_rig.warden._isolated = True
    assert is_tainted(hive_rig.warden, other.worker_id, other.task_id)


async def test_a_cell_keeps_the_blob_resident_and_ships_it_up_with_the_summary_as_the_reason(
    cell_rig: ToolRig, tmp_path: Path
) -> None:
    sub_bee, _worker = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    deposit = await _stage(cell_rig)
    verifier = Ed25519Verifier({cell_rig.cell_signer_id: cell_rig.cell_signer.public_key_bytes})
    queen = QueenDouble(cell_rig, verifier, tmp_path / "queen-spool")

    await _sink(cell_rig, sub_bee).retain(deposit)
    resident = cell_rig.warden._deps.resident_basket
    assert resident is not None and await resident.is_resident(deposit.sha256)
    await queen.serve(asyncio.gather(*cell_rig.warden._tool_jobs))

    assert queen.received[deposit.sha256] == _REPORT  # Shipped up and verified.
    # The Queen writes the Honey record when she re-deposits it, from the offer's reason: the
    # Worker's summary, folded into one paragraph. The Cell sends no Nectar of its own for it.
    [offer] = queen.offers
    assert offer.reason == "Per-line quarterly figures. Useful when reconciling totals."
    assert offer.origin_task_id == sub_bee.task_id and offer.origin_bee == sub_bee.worker_id
    assert queen.other == []


async def test_a_tainted_blob_in_a_cell_is_stored_but_never_shipped(
    cell_rig: ToolRig, tmp_path: Path
) -> None:
    sub_bee, _worker = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    cell_rig.warden._isolated = True
    deposit = await _stage(cell_rig)
    verifier = Ed25519Verifier({cell_rig.cell_signer_id: cell_rig.cell_signer.public_key_bytes})
    queen = QueenDouble(cell_rig, verifier, tmp_path / "queen-spool")

    await _sink(cell_rig, sub_bee).retain(deposit)

    assert not cell_rig.warden._tool_jobs  # No shipping job was started.
    assert (await cell_rig.store.get(deposit.sha256)).tainted
    await queen.serve(asyncio.ensure_future(asyncio.sleep(0)))
    assert queen.offers == []
