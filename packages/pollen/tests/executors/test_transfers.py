"""Test the put-file transfer rules: chunks in order, one sender, a bounded few, never a torn file.

A Warden (the per-Cell supervisor) writes a file onto the device as events grouped by a transfer id
(gateway-contract sections 7 and 9.2). The Pollen Packet (the thin gateway program on the device)
holds at most eight incomplete transfers from one sender, rejects a first chunk that does not start
at offset 0 or whose total is over its cap, rejects any chunk whose offset is not the length
received so far or that does not match the transfer that began it, discards a transfer idle longer
than the chunk-group timeout together with its partial file, and verifies the length and the SHA-256
of the whole before renaming the file into place. A failure at any chunk deletes the partial file.
These tests drive the transfer table directly with a fake clock and a recording reporter.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.transfers.

Key invariants:
    - A refused or abandoned transfer leaves no partial file in scratch.
    - A file reaches its path whole, with the right bytes and mode, or not at all.

See Also:
    - docs/waggle/gateway-contract.md sections 7 and 9.2.
"""

from __future__ import annotations

import hashlib
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from executor_kit import Kit, make_kit, open_lease_for
from states import frame_from

from pollen.errors import PathRefusedError, QuotaExceededError, TransferRefusedError
from pollen.executors.transfers import Transfer, TransferTable
from pollen.lease import DeviceLease
from waggle.clock import FakeClock
from waggle.envelope import Envelope
from waggle.ids import MessageId, new_message_id
from waggle.messages.base import CHUNK_GROUP_TIMEOUT_S, MAX_OPEN_CHUNK_GROUPS
from waggle.messages.session import SessionPutFile

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


@dataclass(frozen=True, slots=True)
class Part:
    """One chunk of a transfer, as a test describes it."""

    data: bytes  # The bytes this chunk carries.
    offset: int = 0  # Where in the file they go.
    total: int | None = None  # The declared length of the whole file; offset plus data if None.
    path: str = "out.bin"  # The destination, relative to scratch.
    final: bool = False  # Whether this is the last chunk, which carries the digest.
    whole: bytes | None = None  # What the digest is of, if not ``data`` (a wrong digest).


class Rig:
    """A transfer table over a kit and an open lease, with a way to send chunks to it."""

    def __init__(self, kit: Kit, lease: DeviceLease, clock: FakeClock) -> None:
        """Bind the rig to a kit, its open lease and the clock the table reads."""
        self.kit, self.lease, self.clock = kit, lease, clock
        self.table = TransferTable(kit.deps)
        self.scratch = Path(lease.scratch_root)

    def chunk(self, transfer_id: MessageId, part: Part) -> SessionPutFile:
        """Build one chunk of a transfer; the digest of ``part.whole`` rides on a final chunk."""
        whole = part.data if part.whole is None else part.whole
        return SessionPutFile(
            lease_id=self.lease.lease_id,
            transfer_id=transfer_id,
            total_bytes=len(part.data) + part.offset if part.total is None else part.total,
            path=part.path,
            chunk=part.data,
            offset=part.offset,
            final=part.final,
            sha256=hashlib.sha256(whole).hexdigest() if part.final else None,
            task_id=None,
            worker_id=None,
        )

    def envelope(self, payload: SessionPutFile, sender: str | None = None) -> Envelope:
        """Wrap a chunk as a frame from the Warden (or another sender)."""
        state = self.kit.state
        return frame_from(state, payload, self.clock, sender=sender or state.warden_id)

    async def feed(
        self, payload: SessionPutFile, sender: str | None = None, target: Path | None = None
    ) -> Transfer | None:
        """Apply one chunk to the table, for ``target`` (the scratch file by default)."""
        where = target or self.scratch / payload.path
        return await self.table.accept(self.envelope(payload, sender), payload, self.lease, where)

    def partials(self) -> list[Path]:
        """Return the partial files in scratch."""
        return list(self.scratch.glob(".put-*.part"))


@pytest.fixture
async def rig(tmp_path: Path) -> Rig:
    """A table over a fresh lease, on a clock the test owns."""
    clock = FakeClock()
    kit = make_kit(tmp_path, clock)
    return Rig(kit, await open_lease_for(kit, clock, quota=10_000), clock)


async def test_a_file_in_chunks_is_verified_and_moved_into_place(rig: Rig) -> None:
    transfer_id = new_message_id(rig.clock)
    whole = b"hello, device"
    first = rig.chunk(transfer_id, Part(whole[:5], total=len(whole)))
    last = rig.chunk(
        transfer_id, Part(whole[5:], offset=5, total=len(whole), final=True, whole=whole)
    )
    assert await rig.feed(first) is None
    assert len(rig.partials()) == 1
    finished = await rig.feed(last)
    assert finished is not None and finished.hexdigest() == hashlib.sha256(whole).hexdigest()
    await rig.table.commit(finished, rig.lease, is_executable=False)
    assert (rig.scratch / "out.bin").read_bytes() == whole and rig.partials() == []


@posix_only
async def test_the_executable_bit_goes_wherever_there_is_a_read_bit(rig: Rig) -> None:
    transfer_id = new_message_id(rig.clock)
    done = await rig.feed(rig.chunk(transfer_id, Part(b"#!/bin/sh\n", final=True)))
    assert done is not None
    await rig.table.commit(done, rig.lease, is_executable=True)
    mode = stat.S_IMODE((rig.scratch / "out.bin").stat().st_mode)
    assert mode & 0o100 and not mode & 0o002


@posix_only
async def test_replacing_a_file_keeps_its_mode(rig: Rig) -> None:
    target = rig.scratch / "out.bin"
    target.write_bytes(b"old")
    target.chmod(0o640)
    done = await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"new", final=True)))
    assert done is not None
    await rig.table.commit(done, rig.lease, is_executable=False)
    assert target.read_bytes() == b"new" and stat.S_IMODE(target.stat().st_mode) == 0o640


async def test_a_first_chunk_must_start_at_offset_zero(rig: Rig) -> None:
    middle = rig.chunk(new_message_id(rig.clock), Part(b"tail", offset=4, total=8))
    with pytest.raises(TransferRefusedError, match="offset 0"):
        await rig.feed(middle)
    assert rig.partials() == []


async def test_a_total_over_the_packets_cap_is_refused(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pollen.executors.transfers.MAX_PUT_FILE_BYTES", 10)
    with pytest.raises(TransferRefusedError, match="over the"):
        await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"x", total=11)))


async def test_a_sender_may_have_only_a_few_incomplete_transfers(rig: Rig) -> None:
    for index in range(MAX_OPEN_CHUNK_GROUPS):
        await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"a", total=2, path=f"f{index}")))
    with pytest.raises(TransferRefusedError, match="open transfers"):
        await rig.feed(
            rig.chunk(new_message_id(rig.clock), Part(b"a", total=2, path="one-too-many"))
        )
    other = rig.kit.state.hive_id  # Another sender has its own allowance.
    await rig.feed(
        rig.chunk(new_message_id(rig.clock), Part(b"a", total=2, path="other")), sender=other
    )


async def test_a_directory_is_not_a_file_to_write(rig: Rig) -> None:
    (rig.scratch / "dir").mkdir()
    with pytest.raises(PathRefusedError, match="directory"):
        await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"x", path="dir")))


async def test_a_write_past_the_quota_is_refused_before_anything_is_written(rig: Rig) -> None:
    with pytest.raises(QuotaExceededError):
        await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"x", total=20_000)))
    assert rig.partials() == []


@pytest.mark.parametrize("what", ["sender", "path"])
async def test_a_chunk_that_does_not_match_its_transfer_ends_it(rig: Rig, what: str) -> None:
    transfer_id = new_message_id(rig.clock)
    await rig.feed(rig.chunk(transfer_id, Part(b"abc", total=6)))
    follow = rig.chunk(transfer_id, Part(b"def", offset=3, total=6, final=True, whole=b"abcdef"))
    with pytest.raises(TransferRefusedError, match="does not match"):
        if what == "sender":
            await rig.feed(follow, sender=rig.kit.state.hive_id)
        else:
            await rig.feed(follow, target=rig.scratch / "elsewhere.bin")
    assert rig.partials() == []


async def test_a_chunk_out_of_order_ends_the_transfer(rig: Rig) -> None:
    transfer_id = new_message_id(rig.clock)
    await rig.feed(rig.chunk(transfer_id, Part(b"abc", total=9)))
    with pytest.raises(TransferRefusedError, match="not at the 3 bytes"):
        await rig.feed(rig.chunk(transfer_id, Part(b"ghi", offset=6, total=9)))
    assert rig.partials() == []


async def test_a_file_shorter_than_declared_is_refused_and_removed(rig: Rig) -> None:
    last = rig.chunk(new_message_id(rig.clock), Part(b"abc", total=5, final=True))
    with pytest.raises(TransferRefusedError, match="not the declared"):
        await rig.feed(last)
    assert rig.partials() == []


async def test_a_file_that_fails_its_digest_is_refused_and_removed(rig: Rig) -> None:
    last = rig.chunk(new_message_id(rig.clock), Part(b"abcde", final=True, whole=b"00000"))
    with pytest.raises(TransferRefusedError, match="digest"):
        await rig.feed(last)
    assert rig.partials() == []


async def test_a_transfer_idle_past_the_timeout_is_discarded_with_its_partial_file(
    rig: Rig,
) -> None:
    transfer_id = new_message_id(rig.clock)
    await rig.feed(rig.chunk(transfer_id, Part(b"abc", total=6)))
    assert len(rig.partials()) == 1
    rig.clock.advance(CHUNK_GROUP_TIMEOUT_S + 1)
    with pytest.raises(TransferRefusedError, match="offset 0"):  # The purge forgot it.
        await rig.feed(rig.chunk(transfer_id, Part(b"def", offset=3, total=6)))
    assert rig.partials() == []


async def test_dropping_a_lease_forgets_its_transfers_only(rig: Rig) -> None:
    await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"a", total=2, path="one")))
    await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"b", total=2, path="two")))
    assert rig.table.drop_lease(rig.lease.lease_id) == 2
    assert rig.table.drop_lease(rig.lease.lease_id) == 0


async def test_a_chunk_that_cannot_be_written_ends_the_transfer(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("pollen.executors.transfers._append", refuse)
    with pytest.raises(QuotaExceededError, match="could not be written"):
        await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"abc")))
    assert rig.partials() == []


async def test_a_transfer_that_cannot_start_is_refused(rig: Rig) -> None:
    for entry in rig.scratch.iterdir():
        entry.unlink()
    rig.scratch.rmdir()  # Scratch is gone: no partial file can be created.
    with pytest.raises(QuotaExceededError, match="could not start"):
        await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"abc")))


async def test_a_file_that_cannot_be_placed_is_refused_and_its_partial_removed(rig: Rig) -> None:
    (rig.scratch / "blocker").write_text("a file where a directory is needed")
    done = await rig.feed(
        rig.chunk(new_message_id(rig.clock), Part(b"abc", path="blocker/inner.bin", final=True))
    )
    assert done is not None
    with pytest.raises(PathRefusedError, match="could not be placed"):
        await rig.table.commit(done, rig.lease, is_executable=False)
    assert rig.partials() == []


async def test_a_file_outside_scratch_is_recorded_before_it_is_placed(
    rig: Rig, tmp_path: Path
) -> None:
    target = tmp_path / "outside.bin"
    target.write_bytes(b"before")
    done = await rig.feed(
        rig.chunk(new_message_id(rig.clock), Part(b"after", final=True)), target=target
    )
    assert done is not None
    await rig.table.commit(done, rig.lease, is_executable=False)
    held = rig.kit.table.get(rig.lease.lease_id)
    assert held is not None and [r.path for r in held.restores] == [str(target)]
    assert target.read_bytes() == b"after"


async def test_a_move_across_filesystems_falls_back_to_a_copy(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: object) -> None:
        raise OSError("cross-device link")

    monkeypatch.setattr("pollen.executors.transfers.os.replace", refuse)
    done = await rig.feed(rig.chunk(new_message_id(rig.clock), Part(b"payload", final=True)))
    assert done is not None
    await rig.table.commit(done, rig.lease, is_executable=False)
    assert (rig.scratch / "out.bin").read_bytes() == b"payload"


async def test_a_refusal_while_recording_removes_the_partial_file(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pollen.lease.restore.MAX_BACKUP_BYTES", 1)
    target = tmp_path / "heavy.bin"
    target.write_bytes(b"too heavy to back up")
    done = await rig.feed(
        rig.chunk(new_message_id(rig.clock), Part(b"new", final=True)), target=target
    )
    assert done is not None
    with pytest.raises(QuotaExceededError):
        await rig.table.commit(done, rig.lease, is_executable=False)
    assert rig.partials() == [] and target.read_bytes() == b"too heavy to back up"


async def test_a_chunk_past_the_declared_total_ends_the_transfer(rig: Rig) -> None:
    transfer_id = new_message_id(rig.clock)
    await rig.feed(rig.chunk(transfer_id, Part(b"abc", total=4)))
    # A later chunk raises the declared total (each chunk carries its own) and is never final, so
    # the only check that knew the first total was the one on the last chunk.
    with pytest.raises(TransferRefusedError, match="past the declared 4"):
        await rig.feed(rig.chunk(transfer_id, Part(b"def", offset=3, total=4_000)))
    assert rig.partials() == []


async def test_room_is_measured_again_for_every_chunk(rig: Rig) -> None:
    transfer_id = new_message_id(rig.clock)
    await rig.feed(rig.chunk(transfer_id, Part(b"a" * 100, total=9_000)))  # Honest and roomy.
    # Scratch fills up between two chunks (a command of the lease writing there): the next chunk
    # would cross the quota the start measured against an empty directory.
    (rig.scratch / "filled-meanwhile").write_bytes(b"x" * 9_900)
    with pytest.raises(QuotaExceededError, match="past its quota"):
        await rig.feed(rig.chunk(transfer_id, Part(b"b" * 100, offset=100, total=9_000)))
    assert rig.partials() == []
