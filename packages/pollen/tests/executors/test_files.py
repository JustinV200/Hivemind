"""Test file transfer through a real packet: put, get and delete, inside scratch and outside it.

A Warden (the per-Cell supervisor) writes files onto a borrowed Real Cell in chunks, reads them
back and deletes them (gateway-contract section 9.2), and every path is resolved before it is
judged (section 12): symbolic links followed, ``..`` removed, the check made on the path that is
acted on. These tests run the transfers against a real packet and check that the bytes and digests
are right, that a transfer which breaks a rule (an offset out of order, a wrong length or digest,
too many open transfers) is refused with ``pollen.transfer_refused`` and leaves no partial file,
that a path outside scratch needs the ``cell:outside_scratch`` grant and has its prior bytes
recorded before it is changed, that the quota refuses a write that would pass it, and that a read
past ``max_bytes`` is refused rather than streamed.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.files and
    pollen.executors.transfers end to end through a running packet.

Key invariants:
    - A refused or failed transfer leaves nothing behind in scratch.
    - A change outside scratch is recorded in the lease table before the file changes.

See Also:
    - docs/waggle/gateway-contract.md sections 9.2, 10 and 12.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest
from device_kit import Device, Live, Terms, live_lease, make_device
from disk import tree
from frame_overrides import Overrides
from queen_double import QueenDouble
from scenarios import Upload, get_file, put_chunked, put_file

from pollen.lease import LeaseTable, RestoreKind
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.ids import new_message_id
from waggle.messages.control import ErrorMessage
from waggle.messages.labels import AccessLevel
from waggle.messages.session import (
    SessionDeleteFile,
    SessionExit,
    SessionPutFile,
    SessionRequestKind,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="symbolic links and modes")


def _code(answer: Envelope) -> str:
    """Return the error code of a control.error answer."""
    assert isinstance(answer.payload, ErrorMessage), answer.payload
    return answer.payload.code


async def test_a_file_put_in_chunks_is_written_whole_and_verified(live: Live) -> None:
    data = os.urandom(70_000)
    upload = Upload("dir/blob.bin", data, chunk_size=20_000)
    ended = await put_chunked(live.queen, live.session, live.lease.lease_id, upload)
    assert isinstance(ended.payload, SessionExit)
    assert ended.payload.request_kind is SessionRequestKind.PUT_FILE
    assert (ended.payload.size_bytes, ended.payload.sha256) == (
        len(data),
        hashlib.sha256(data).hexdigest(),
    )
    written = Path(live.lease.scratch_root) / "dir" / "blob.bin"
    assert written.read_bytes() == data
    assert not tree(Path(live.lease.scratch_root), "*.part")


@posix_only
async def test_the_executable_bit_is_set_when_asked(live: Live) -> None:
    upload = Upload("run.sh", b"#!/bin/sh\n", is_executable=True)
    await put_chunked(live.queen, live.session, live.lease.lease_id, upload)
    mode = (Path(live.lease.scratch_root) / "run.sh").stat().st_mode
    assert mode & 0o100


async def test_a_file_comes_back_with_its_size_and_digest(live: Live) -> None:
    data = os.urandom(300_000)
    await put_file(live.queen, live.session, live.lease.lease_id, "back.bin", data)
    result = await get_file(live.queen, live.session, live.lease.lease_id, "back.bin")
    assert not isinstance(result, Envelope)
    assert result.content == data
    assert (result.exit.size_bytes, result.exit.sha256) == (len(data), result.sha256)
    assert result.exit.request_kind is SessionRequestKind.GET_FILE


async def test_a_file_past_max_bytes_is_refused_not_streamed(live: Live) -> None:
    await put_file(live.queen, live.session, live.lease.lease_id, "big.bin", b"x" * 100)
    refused = await get_file(live.queen, live.session, live.lease.lease_id, "big.bin", max_bytes=50)
    assert isinstance(refused, Envelope)
    assert _code(refused) == "pollen.transfer_refused"


async def test_reading_a_missing_file_is_not_found(live: Live) -> None:
    refused = await get_file(live.queen, live.session, live.lease.lease_id, "nothing.txt")
    assert isinstance(refused, Envelope)
    assert _code(refused) == "pollen.not_found"


async def test_a_delete_removes_one_file_and_ends_in_an_exit(live: Live) -> None:
    await put_file(live.queen, live.session, live.lease.lease_id, "gone.txt", b"bye")
    request = await live.session.send(
        SessionDeleteFile(
            lease_id=live.lease.lease_id, path="gone.txt", task_id=None, worker_id=None
        ),
        sender=live.queen.warden_id,
    )
    ended = await live.session.expect(SessionExit, where=lambda e: e.correlation_id == request.id)
    assert ended.payload.request_kind is SessionRequestKind.DELETE_FILE
    assert (ended.payload.size_bytes, ended.payload.sha256) == (None, None)
    assert not (Path(live.lease.scratch_root) / "gone.txt").exists()


@pytest.mark.parametrize(
    ("name", "code"), [("missing.txt", "pollen.not_found"), (".", "pollen.path_refused")]
)
async def test_a_delete_of_a_missing_file_or_a_directory_is_refused(
    live: Live, name: str, code: str
) -> None:
    request = await live.session.send(
        SessionDeleteFile(lease_id=live.lease.lease_id, path=name, task_id=None, worker_id=None),
        sender=live.queen.warden_id,
    )
    refused = await live.session.expect(
        ErrorMessage, where=lambda e: e.correlation_id == request.id
    )
    assert refused.payload.code == code


async def _chunk(live: Live, transfer_id: str, **fields: object) -> SessionPutFile:
    """Send one chunk of a hand-built transfer."""
    values: dict[str, object] = {
        "lease_id": live.lease.lease_id,
        "transfer_id": transfer_id,
        "total_bytes": 10,
        "path": "t.bin",
        "chunk": b"01234",
        "offset": 0,
        "final": False,
        "sha256": None,
        "is_executable": False,
        "task_id": None,
        "worker_id": None,
    }
    values.update(fields)
    message = SessionPutFile(**values)
    first = message.offset == 0
    named = Overrides(message_id=transfer_id) if first else None
    await live.session.send(message, sender=live.queen.warden_id, overrides=named)
    return message


async def test_a_chunk_out_of_order_ends_the_transfer_and_leaves_nothing(live: Live) -> None:
    transfer = new_message_id(live.queen.clock)
    await _chunk(live, transfer)
    await _chunk(live, transfer, offset=7, chunk=b"789")  # Skips bytes 5 to 6.
    refused = await live.session.expect(ErrorMessage, where=lambda e: e.correlation_id == transfer)
    assert refused.payload.code == "pollen.transfer_refused"
    assert not tree(Path(live.lease.scratch_root))


async def test_a_wrong_digest_ends_the_transfer_and_leaves_nothing(live: Live) -> None:
    transfer = new_message_id(live.queen.clock)
    await _chunk(live, transfer, chunk=b"0123456789", final=True, sha256="0" * 64)
    refused = await live.session.expect(ErrorMessage, where=lambda e: e.correlation_id == transfer)
    assert refused.payload.code == "pollen.transfer_refused"
    assert not tree(Path(live.lease.scratch_root))


async def test_a_short_file_marked_final_is_refused(live: Live) -> None:
    transfer = new_message_id(live.queen.clock)
    digest = hashlib.sha256(b"01234").hexdigest()
    await _chunk(live, transfer, final=True, sha256=digest)  # Declared 10 bytes, sent 5.
    refused = await live.session.expect(ErrorMessage, where=lambda e: e.correlation_id == transfer)
    assert refused.payload.code == "pollen.transfer_refused"


async def test_too_many_open_transfers_are_refused(live: Live) -> None:
    transfers = [new_message_id(live.queen.clock) for _ in range(9)]
    for index, transfer in enumerate(transfers):
        await _chunk(live, transfer, path=f"open{index}.bin")
    refused = await live.session.expect(
        ErrorMessage, where=lambda e: e.correlation_id == transfers[-1]
    )
    assert refused.payload.code == "pollen.transfer_refused"


async def test_a_write_past_the_quota_is_refused(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    async with live_lease(queen, device, clock, quota=1_000) as live:
        answer = await put_file(live.queen, live.session, live.lease.lease_id, "big", b"x" * 5_000)
        assert _code(answer) == "pollen.quota_exceeded"


async def test_a_path_outside_scratch_is_denied_to_a_scratch_level_lease(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    device = make_device(queen, tmp_path / "data", clock, Terms(access=AccessLevel.SCRATCH))
    target = tmp_path / "elsewhere.txt"
    async with live_lease(queen, device, clock, access=AccessLevel.SCRATCH) as live:
        answer = await put_file(live.queen, live.session, live.lease.lease_id, str(target), b"no")
        assert _code(answer) == "pollen.capability_denied"
        escape = await put_file(live.queen, live.session, live.lease.lease_id, "../x.txt", b"no")
        assert _code(escape) == "pollen.capability_denied"
        assert not target.exists()


@posix_only
async def test_a_link_inside_scratch_that_points_outside_is_outside(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    device = make_device(queen, tmp_path / "data", clock, Terms(access=AccessLevel.SCRATCH))
    outside = tmp_path / "outside"
    outside.mkdir()
    async with live_lease(queen, device, clock, access=AccessLevel.SCRATCH) as live:
        (Path(live.lease.scratch_root) / "door").symlink_to(outside)
        answer = await put_file(
            live.queen, live.session, live.lease.lease_id, "door/file.txt", b"through"
        )
        assert _code(answer) == "pollen.capability_denied"
        assert not (outside / "file.txt").exists()


async def test_a_read_only_lease_may_read_but_not_write(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    device = make_device(queen, tmp_path / "data", clock, Terms(access=AccessLevel.READ_ONLY))
    readable = tmp_path / "readable.txt"
    readable.write_bytes(b"look")
    async with live_lease(queen, device, clock, access=AccessLevel.READ_ONLY) as live:
        seen = await get_file(live.queen, live.session, live.lease.lease_id, str(readable))
        assert not isinstance(seen, Envelope) and seen.content == b"look"
        denied = await put_file(live.queen, live.session, live.lease.lease_id, "w.txt", b"no")
        assert _code(denied) == "pollen.capability_denied"


async def test_a_change_outside_scratch_is_recorded_before_it_is_made(
    live: Live, tmp_path: Path
) -> None:
    target = tmp_path / "recorded.txt"
    target.write_bytes(b"before")
    await put_file(live.queen, live.session, live.lease.lease_id, str(target), b"after")
    held = LeaseTable.load(live.device.layout.lease_table).get(live.lease.lease_id)
    assert held is not None
    assert [(Path(r.path), r.kind) for r in held.restores] == [
        (target.resolve(), RestoreKind.REPLACED)
    ]
    assert target.read_bytes() == b"after"


async def test_a_delete_outside_scratch_keeps_the_prior_bytes_for_the_release(
    live: Live, tmp_path: Path
) -> None:
    target = tmp_path / "precious.txt"
    target.write_bytes(b"keep me")
    request = await live.session.send(
        SessionDeleteFile(
            lease_id=live.lease.lease_id, path=str(target), task_id=None, worker_id=None
        ),
        sender=live.queen.warden_id,
    )
    await live.session.expect(SessionExit, where=lambda e: e.correlation_id == request.id)
    assert not target.exists()
    from scenarios import teardown

    await teardown(live.queen, live.session, live.lease.lease_id)
    assert target.read_bytes() == b"keep me"


async def test_a_file_that_vanishes_before_it_is_streamed_is_a_refusal_not_a_hang(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The size check passes, as it would if the file were removed just after it.
    monkeypatch.setattr("pollen.executors.files._regular_size", lambda _path, _lease: 5)
    refused = await get_file(live.queen, live.session, live.lease.lease_id, "ghost.bin")
    assert isinstance(refused, Envelope) and _code(refused) == "pollen.transfer_refused"
    assert isinstance(refused.payload, ErrorMessage)
    assert refused.payload.failed_kind == "session.get_file"
