"""Tests for hivemind.basket.models: Blob, DepositRequest, HoneyRecord and their bounds.

Fits into the Hive:
    Mirrors src/hivemind/basket/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.models for the module under test.
"""

from __future__ import annotations

import pytest
from builders.basket import make_blob, make_deposit_request
from pydantic import ValidationError

from hivemind.basket.models import (
    MAX_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    Blob,
    BlobState,
    HoneyRecord,
    MediaKind,
)
from hivemind.cell import OsFamily


def test_a_blob_round_trips_through_json() -> None:
    blob = make_blob(platform_tags=(OsFamily.LINUX, OsFamily.MACOS), tainted=True)

    assert Blob.model_validate_json(blob.model_dump_json()) == blob


def test_a_blob_defaults_to_present_and_untainted() -> None:
    blob = make_blob()

    assert blob.state is BlobState.PRESENT and not blob.tainted


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": "A" * 64},  # Upper case is not the one digest form.
        {"sha256": "ab" * 10},  # Too short.
        {"size_bytes": 0},  # A blob has at least one byte.
        {"media_type": "Application/Zip"},  # Lower case only.
        {"media_type": "zip"},  # No subtype.
        {"platform_tags": (OsFamily.LINUX, OsFamily.LINUX)},  # Each OS at most once.
        {"origin_cell_id": "not-an-id"},
        {"origin_bee": "task_01HZZZZZZZZZZZZZZZZZZZZZZZ"},  # Not a worker or warden.
        {"unexpected": 1},  # extra="forbid".
    ],
)
def test_a_blob_rejects_a_malformed_field(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_blob(**overrides)


def test_a_blob_is_frozen() -> None:
    blob = make_blob()

    with pytest.raises(ValidationError):
        blob.tainted = True


def test_every_media_kind_the_design_names_exists() -> None:
    assert {kind.name for kind in MediaKind} == {
        "WHEEL",
        "ARCHIVE",
        "DATASET",
        "TOOL_PACKAGE",
        "IMAGE",
        "MODEL_WEIGHTS",
        "TEXT",
        "OTHER",
    }


def test_a_deposit_request_never_prints_its_bytes() -> None:
    request = make_deposit_request(b"super secret payload")

    assert "super secret payload" not in repr(request)


@pytest.mark.parametrize(
    "overrides",
    [
        {"content": b""},
        {"title": ""},
        {"title": "t" * (MAX_TITLE_CHARS + 1)},
        {"summary": "s" * (MAX_SUMMARY_CHARS + 1)},
        {"summary": "first paragraph\n\nsecond paragraph"},  # One paragraph only.
        {"platform_tags": (OsFamily.WINDOWS, OsFamily.WINDOWS)},
    ],
)
def test_a_deposit_request_rejects_a_malformed_field(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_deposit_request(**overrides)


def test_a_summary_with_a_single_line_break_is_still_one_paragraph() -> None:
    request = make_deposit_request(summary="line one\nline two")

    assert request.summary == "line one\nline two"


def test_a_honey_record_round_trips_and_carries_the_hash_that_locates_the_blob() -> None:
    blob = make_blob()
    record = HoneyRecord(
        title="t",
        summary="s",
        sha256=blob.sha256,
        media_kind=blob.media_kind,
        clearance=blob.clearance,
        origin_cell_id=blob.origin_cell_id,
        deposited_at=blob.deposited_at,
    )

    assert HoneyRecord.model_validate_json(record.model_dump_json()) == record
