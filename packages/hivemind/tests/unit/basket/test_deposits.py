"""Tests for hivemind.basket.deposits: the one door a blob enters through.

Covers dedupe, the size cap, the scanner refusal with its events, the one-Honey-record rule, the
Night Veil export ceiling, label merging on a repeat deposit and that no event or error carries a
blob's bytes or its Honey text.

Fits into the Hive:
    Mirrors src/hivemind/basket/deposits.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.deposits for the module under test.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from builders.basket import (
    INJECTION,
    BasketKit,
    FakeHoneyPort,
    make_deposit_request,
    make_kit,
)

from hivemind.basket import (
    BasketDeposits,
    Blob,
    BlobManifest,
    BlobNotFoundError,
    BlobRefusedError,
    BlobTooLargeError,
    HoneyRecord,
    MediaKind,
)
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.pheromone import PheromoneEvent, TrailQuery


@pytest.fixture
async def kit(tmp_path: Path) -> BasketKit:
    """A deposit door over a real store, a recording Honey port and the shipped scanner."""
    return await make_kit(tmp_path, max_blob_bytes=1_000)


def _kinds(events: tuple[PheromoneEvent, ...]) -> list[str]:
    return [event.kind for event in events]


async def test_a_new_blob_is_stored_signed_described_and_reported(kit: BasketKit) -> None:
    request = make_deposit_request(b"fresh bytes", title="A wheel", summary="Built for a task.")

    outcome = await kit.deposits.deposit(request)

    sha256 = hashlib.sha256(b"fresh bytes").hexdigest()
    assert outcome.is_new and outcome.blob.sha256 == sha256
    assert await kit.store.open_bytes(sha256) == b"fresh bytes"
    (await kit.store.manifest(sha256)).verify(kit.verifier)
    [event] = outcome.events
    assert event.kind == "basket.deposited" and event.id == outcome.blob.deposit_event_id
    assert event.subject_id == request.origin_cell_id
    [record] = kit.honey.records
    assert (record.title, record.summary, record.sha256) == ("A wheel", "Built for a task.", sha256)
    assert record.clearance is outcome.blob.clearance and record.origin_bee == request.origin_bee


async def test_a_borrowed_cell_origin_is_labelled_c2_whatever_was_declared(kit: BasketKit) -> None:
    outcome = await kit.deposits.deposit(
        make_deposit_request(declared=HoneyClearance.C0, from_borrowed_cell=True)
    )

    assert outcome.blob.clearance is HoneyClearance.C2


async def test_a_repeat_deposit_is_deduplicated_and_writes_no_second_honey_record(
    kit: BasketKit,
) -> None:
    await kit.deposits.deposit(make_deposit_request(b"same bytes"))

    again = await kit.deposits.deposit(make_deposit_request(b"same bytes", title="Another title"))

    assert not again.is_new and _kinds(again.events) == ["basket.deposit_deduplicated"]
    assert again.events[0].payload["label_raised"] is False
    assert len(kit.honey.records) == 1 and len(await kit.store.list_blobs()) == 1


async def test_a_repeat_from_a_borrowed_cell_raises_the_label_and_re_signs_the_manifest(
    kit: BasketKit,
) -> None:
    first = await kit.deposits.deposit(make_deposit_request(b"shared bytes"))
    assert first.blob.clearance is HoneyClearance.C0

    again = await kit.deposits.deposit(
        make_deposit_request(b"shared bytes", from_borrowed_cell=True, tainted=True)
    )

    sha256 = first.blob.sha256
    assert again.blob.clearance is HoneyClearance.C2 and again.blob.tainted
    assert again.events[0].payload["label_raised"] is True
    stored = await kit.store.manifest(sha256)
    assert stored.blob.clearance is HoneyClearance.C2
    stored.verify(kit.verifier)  # Re-signed for the merged label: still verifies.
    assert len(kit.honey.records) == 1


async def test_a_repeat_with_a_lower_label_never_lowers_the_stored_one(kit: BasketKit) -> None:
    await kit.deposits.deposit(make_deposit_request(b"x" * 10, from_borrowed_cell=True))

    again = await kit.deposits.deposit(make_deposit_request(b"x" * 10, declared=HoneyClearance.C0))

    assert again.blob.clearance is HoneyClearance.C2


async def test_a_blob_over_the_cap_is_refused_with_its_event_and_nothing_is_kept(
    kit: BasketKit,
) -> None:
    request = make_deposit_request(b"x" * 1_001)

    with pytest.raises(BlobTooLargeError) as raised:
        await kit.deposits.deposit(request)

    assert _kinds(raised.value.events) == ["basket.deposit_refused"]
    assert raised.value.events[0].payload["code"] == "size"
    assert await kit.store.list_blobs() == () and kit.honey.records == []


async def test_a_flagged_text_blob_is_refused_recorded_and_never_stored(kit: BasketKit) -> None:
    request = make_deposit_request(INJECTION, media_kind=MediaKind.TEXT, media_type="text/plain")

    with pytest.raises(BlobRefusedError) as raised:
        await kit.deposits.deposit(request)

    assert raised.value.reason_code == "scanner"
    [refused] = raised.value.events
    assert refused.kind == "basket.deposit_refused" and refused.payload["code"] == "scanner"
    # The scanner itself recorded the injection signal on its trail, hash only.
    [flag] = await kit.trail.query(TrailQuery(kind="guard.injection_suspected"))
    assert flag.payload["source"] == "nectar_intake"
    assert await kit.store.list_blobs() == () and kit.honey.records == []


async def test_a_clean_text_blob_passes_the_scanner(kit: BasketKit) -> None:
    outcome = await kit.deposits.deposit(
        make_deposit_request(
            b"All 12 tests passed.", media_kind=MediaKind.TEXT, media_type="text/plain"
        )
    )

    assert outcome.is_new and await kit.trail.query(TrailQuery()) == ()


async def test_a_binary_blob_is_deposited_unscanned_even_if_it_holds_an_imperative(
    kit: BasketKit,
) -> None:
    # A binary cannot be scanned, so it is stored as is: a blob is inert (ADR-0048).
    outcome = await kit.deposits.deposit(
        make_deposit_request(
            INJECTION, media_kind=MediaKind.DATASET, media_type="application/octet-stream"
        )
    )

    assert outcome.is_new and await kit.trail.query(TrailQuery()) == ()


async def test_a_tool_package_that_is_plain_text_is_scanned_and_a_binary_one_is_skipped(
    kit: BasketKit,
) -> None:
    kind = {"media_kind": MediaKind.TOOL_PACKAGE, "media_type": "application/zip"}
    binary = INJECTION + b"\xff\xfe\x00 not utf-8"

    stored = await kit.deposits.deposit(make_deposit_request(binary, **kind))
    with pytest.raises(BlobRefusedError):
        await kit.deposits.deposit(make_deposit_request(INJECTION, **kind))

    assert stored.is_new


async def test_declared_text_with_stray_bytes_is_still_scanned(kit: BasketKit) -> None:
    # Lenient decoding: a stray byte cannot hide an imperative from the scanner.
    request = make_deposit_request(
        b"\xff" + INJECTION, media_kind=MediaKind.OTHER, media_type="text/markdown"
    )

    with pytest.raises(BlobRefusedError) as raised:
        await kit.deposits.deposit(request)

    assert raised.value.reason_code == "scanner"


async def test_a_night_veil_cell_cannot_export_above_c1(kit: BasketKit) -> None:
    ok = await kit.deposits.deposit(
        make_deposit_request(
            b"veil c1", tier=CombShieldLevel.NIGHT_VEIL, declared=HoneyClearance.C1
        )
    )

    with pytest.raises(BlobRefusedError) as raised:
        await kit.deposits.deposit(
            make_deposit_request(
                b"veil c2", tier=CombShieldLevel.NIGHT_VEIL, declared=HoneyClearance.C2
            )
        )

    assert ok.blob.clearance is HoneyClearance.C1
    assert raised.value.reason_code == "clearance"
    assert _kinds(raised.value.events) == ["basket.deposit_refused"]
    assert len(kit.honey.records) == 1


async def test_no_event_or_error_carries_the_bytes_the_title_or_the_summary(kit: BasketKit) -> None:
    secret = b"the-payload-must-not-leak"
    request = make_deposit_request(secret, title="unleakable title", summary="unleakable summary")
    outcome = await kit.deposits.deposit(request)
    flagged = make_deposit_request(INJECTION, media_kind=MediaKind.TEXT, media_type="text/plain")
    with pytest.raises(BlobRefusedError) as raised:
        await kit.deposits.deposit(flagged)

    dumped = " ".join(e.model_dump_json() for e in (*outcome.events, *raised.value.events))
    dumped += str(raised.value) + " ".join(
        e.model_dump_json() for e in await kit.trail.query(TrailQuery())
    )

    for leak in ("the-payload-must-not-leak", "unleakable", "ignore all previous"):
        assert leak not in dumped


async def test_a_honey_failure_keeps_nothing_so_a_retry_loses_no_record(kit: BasketKit) -> None:
    class BrokenHoney:
        async def record(self, record: HoneyRecord) -> None:
            raise RuntimeError("honey is down")

    door = BasketDeposits(kit.store, BrokenHoney(), kit.wiring, kit.clock)

    with pytest.raises(RuntimeError):
        await door.deposit(make_deposit_request(b"orphan check"))
    retry = await kit.deposits.deposit(make_deposit_request(b"orphan check"))

    assert retry.is_new and len(kit.honey.records) == 1


def test_the_fake_honey_port_remembers_nothing_until_given_a_record() -> None:
    assert FakeHoneyPort().records == []


async def test_manifests_are_signed_with_the_wired_signer_id(kit: BasketKit) -> None:
    outcome = await kit.deposits.deposit(make_deposit_request(b"signed"))

    manifest: BlobManifest = await kit.store.manifest(outcome.blob.sha256)

    assert manifest.signer == kit.signer_id


async def test_losing_a_race_to_a_concurrent_deposit_is_reported_as_a_dedupe(
    kit: BasketKit,
) -> None:
    class RacedStore:
        """A store whose deposit finds the bytes already taken by another caller."""

        async def get(self, sha256: str) -> Blob:
            raise BlobNotFoundError(sha256)

        async def deposit(
            self, request_bytes: bytes, blob: Blob, manifest: BlobManifest
        ) -> tuple[Blob, bool]:
            return blob, False

    door = BasketDeposits(RacedStore(), kit.honey, kit.wiring, kit.clock)  # type: ignore[arg-type]  # WHY: a deliberately partial fake store.

    outcome = await door.deposit(make_deposit_request(b"raced bytes"))

    assert not outcome.is_new and _kinds(outcome.events) == ["basket.deposit_deduplicated"]
