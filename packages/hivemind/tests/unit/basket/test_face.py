"""Tests for hivemind.basket's public face: the design's export list is all there.

Fits into the Hive:
    Mirrors src/hivemind/basket/__init__.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/phase-9-handoff.md section 2, "Interfaces fixed up front", for the list.
"""

from __future__ import annotations

import hivemind.basket as basket

# The `basket` export list the phase 9 design fixed up front, so later waves can rely on it.
_DESIGN_EXPORTS = {
    "Blob",
    "BlobManifest",
    "BlobState",
    "DepositRequest",
    "HoneyRecord",
    "HoneyRecordPort",
    "BasketDeposits",
    "BasketStore",
    "SqliteBasketStore",
    "ResidencyDecision",
    "decide_residency",
    "ResidentBasket",
    "blob_clearance",
    "CHUNK_BYTES",
    "Offer",
    "Chunk",
    "Ack",
    "Refuse",
    "RefusalCode",
    "TransferSender",
    "TransferReceiver",
}


def test_every_name_the_design_promises_is_exported() -> None:
    assert set(basket.__all__) >= _DESIGN_EXPORTS


def test_every_exported_name_resolves() -> None:
    assert all(hasattr(basket, name) for name in basket.__all__)
