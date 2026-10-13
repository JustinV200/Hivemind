"""Test the packet's diagnostics: the counters and the status document ``pollen status`` reads.

The Pollen Packet (the thin gateway program on a device the Hive borrows) keeps no audit log of its
own; it keeps a small status document for the operator (gateway-contract sections 7 and 16): whether
the link to the Queen is up and since when, the stable code of the last failure, how many frames
were dropped and why, how many leases and queued frames there are and how long the Queen has been
silent. A gateway whose clock is wrong drops every frame as stale or from the future and is asked to
say so rather than retry silently, so a run of freshness drops with no admitted frame between marks
the clock as suspect. The document holds ids, counts and error codes only.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.status.

Key invariants:
    - The clock is suspect after ``CLOCK_SUSPECT_STREAK`` consecutive freshness drops, and clears at
      the first admitted frame.
    - The document is written atomically and a damaged or missing one reads as None.

See Also:
    - docs/waggle/gateway-contract.md sections 7 and 16.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from pollen.agent import PacketStatus, StatusDocument, read_status, write_status
from pollen.agent.status import CLOCK_SUSPECT_STREAK
from waggle.clock import FakeClock


def _suspect(status: PacketStatus) -> bool:
    """Read the clock-suspect flag through a call, so mypy does not narrow it across updates."""
    return status.is_clock_suspect


def _snapshot(status: PacketStatus) -> StatusDocument:
    """Take a snapshot with fixed lease, outbox and silence figures."""
    return status.snapshot(open_leases=2, outbox_pending=3, silence_s=4.5)


def test_a_new_status_is_down_with_no_failure_and_no_drops() -> None:
    document = _snapshot(PacketStatus(FakeClock()))
    assert (document.is_connected, document.connected_since, document.last_failure) == (
        False,
        None,
        None,
    )
    assert document.dropped == {} and not document.is_clock_suspect
    assert (document.open_leases, document.outbox_pending, document.silence_s) == (2, 3, 4.5)
    assert document.pid == os.getpid()


def test_connecting_and_failing_are_remembered_until_the_next_connection() -> None:
    clock = FakeClock()
    status = PacketStatus(clock)
    status.disconnected("waggle.transport.connect_failed")
    status.connected()
    up = _snapshot(status)
    assert up.is_connected and up.connected_since == clock.now() and up.last_failure is None
    status.disconnected(None)
    status.disconnected("waggle.transport.tls_pin")
    down = _snapshot(status)
    assert not down.is_connected and down.last_failure == "waggle.transport.tls_pin"
    status.disconnected(None)  # A clean end does not erase what went wrong before.
    assert _snapshot(status).last_failure == "waggle.transport.tls_pin"


def test_drops_are_counted_by_reason() -> None:
    status = PacketStatus(FakeClock())
    for reason in ("DUPLICATE", "DUPLICATE", "SENDER"):
        status.note_drop(reason)
    assert _snapshot(status).dropped == {"DUPLICATE": 2, "SENDER": 1}


def test_a_run_of_freshness_drops_marks_the_clock_and_one_admitted_frame_clears_it() -> None:
    status = PacketStatus(FakeClock())
    for _ in range(CLOCK_SUSPECT_STREAK - 1):
        status.note_drop("STALE")
    assert not _suspect(status)
    status.note_drop("FUTURE")
    assert _suspect(status) and _snapshot(status).is_clock_suspect
    status.note_admitted()
    assert not _suspect(status)


def test_a_drop_for_another_reason_breaks_the_run() -> None:
    status = PacketStatus(FakeClock())
    for _ in range(CLOCK_SUSPECT_STREAK - 1):
        status.note_drop("STALE")
    status.note_drop("SENDER")
    status.note_drop("STALE")
    assert not _suspect(status)


def test_the_document_round_trips_and_refuses_what_its_fields_refuse() -> None:
    document = _snapshot(PacketStatus(FakeClock()))
    assert StatusDocument.model_validate_json(document.model_dump_json()) == document
    good = document.model_dump(mode="json")
    for bad in ({"pid": 0}, {"open_leases": -1}, {"surprise": True}, {"silence_s": -0.5}):
        with pytest.raises(ValidationError):
            StatusDocument.model_validate({**good, **bad})


async def test_the_document_is_written_and_read_back(tmp_path: Path) -> None:
    document = _snapshot(PacketStatus(FakeClock()))
    await write_status(tmp_path / "status.json", document)
    assert read_status(tmp_path / "status.json") == document


def test_a_missing_or_damaged_document_reads_as_none(tmp_path: Path) -> None:
    assert read_status(tmp_path / "absent.json") is None
    (tmp_path / "damaged.json").write_text("{not json")
    assert read_status(tmp_path / "damaged.json") is None
    (tmp_path / "wrong.json").write_text('{"pid": "x"}')
    assert read_status(tmp_path / "wrong.json") is None


def test_the_document_carries_no_address_path_or_key() -> None:
    names = set(StatusDocument.model_fields)
    assert not names & {"url", "queen_url", "path", "key", "token", "hostname"}
