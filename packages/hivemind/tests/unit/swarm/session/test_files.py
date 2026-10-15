"""Tests for hivemind.swarm.session.files: moving bytes to, from and out of a device's disk.

Fits into the Hive:
    Mirrors src/hivemind/swarm/session/files.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The file clauses of tests/contracts/test_cell_session_contract.py are repeated
    here against the scripted device, because PollenSession is not registered in that suite.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.session.files for the module under test.
    - builders.swarm.device.files for the device's side of a transfer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from builders.swarm import (
    DeviceScript,
    LinkRig,
    RecordingGuard,
    make_heartbeat,
    open_rig_session,
)

from hivemind.cell.errors import PathNotAllowedError
from hivemind.swarm.errors import CommandDeniedError, DeviceRequestError, TransferIntegrityError
from hivemind.swarm.session.exec import UNEXPECTED_REPLY_CODE
from waggle.envelope import Envelope
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.session.files import SessionPutFile

RigFactory = Callable[[DeviceScript], Awaitable[LinkRig]]


async def test_put_then_get_round_trips_bytes(rig: LinkRig) -> None:
    session, _lease, _guard = await open_rig_session(rig)

    await session.put_file(Path("data.bin"), b"payload")

    assert await session.get_file(Path("data.bin")) == b"payload"


async def test_a_relative_path_resolves_under_the_scratch_dir(rig: LinkRig) -> None:
    session, lease, _guard = await open_rig_session(rig)

    await session.put_file(Path("nested/dir/data.bin"), b"payload")

    # The rig's device is POSIX, so its paths are spelled with `/` whatever this host is.
    assert rig.device.state.files == {
        f"{lease.scratch_root.as_posix()}/nested/dir/data.bin": b"payload"
    }
    assert await session.get_file(session.scratch_dir / "nested" / "dir" / "data.bin") == b"payload"


async def test_dot_dot_segments_cannot_climb_out_of_scratch(rig: LinkRig) -> None:
    session, _lease, _guard = await open_rig_session(rig)

    with pytest.raises(PathNotAllowedError):
        await session.put_file(Path("../../escape.txt"), b"x")

    assert rig.device.state.files == {}


async def test_delete_removes_a_file_and_a_missing_path_raises_not_found(rig: LinkRig) -> None:
    session, _lease, _guard = await open_rig_session(rig)
    await session.put_file(Path("gone.txt"), b"bye")

    await session.delete_file(Path("gone.txt"))

    with pytest.raises(FileNotFoundError):
        await session.get_file(Path("gone.txt"))
    with pytest.raises(FileNotFoundError):
        await session.delete_file(Path("gone.txt"))


async def test_a_path_outside_scratch_is_refused_unless_the_lease_allows_it(rig: LinkRig) -> None:
    refusing, _lease, _guard = await open_rig_session(rig)

    with pytest.raises(PathNotAllowedError):
        await refusing.put_file(Path("/pollen/outside/data.bin"), b"payload")


async def test_a_path_under_an_allowed_root_is_written_and_marked_outside_scratch(
    rigs: RigFactory,
) -> None:
    rig = await rigs(DeviceScript(allowed_scopes=("/pollen/outside/*",)))
    session, _lease, guard = await open_rig_session(rig, allowed_paths=(Path("/pollen/outside"),))

    await session.put_file(Path("/pollen/outside/data.bin"), b"payload")

    assert rig.device.state.files == {"/pollen/outside/data.bin": b"payload"}
    command = guard.authorised[-1]
    assert command.outside_scratch is True
    assert [str(need) for need in command.needs] == [
        "fs:write:/pollen/outside/data.bin",
        "cell:outside_scratch:/pollen/outside/data.bin",
    ]


async def test_a_path_allowed_after_the_session_exists_is_reachable(rigs: RigFactory) -> None:
    rig = await rigs(DeviceScript(allowed_scopes=("/pollen/widened/*",)))
    session, lease, _guard = await open_rig_session(rig)
    with pytest.raises(PathNotAllowedError):
        await session.put_file(Path("/pollen/widened/data.bin"), b"payload")

    lease.note_allowed_path(Path("/pollen/widened"))
    await session.put_file(Path("/pollen/widened/data.bin"), b"payload")

    assert "/pollen/widened/data.bin" in rig.device.state.files


async def test_a_large_file_is_sent_in_wire_sized_chunks_with_one_final_digest(
    rig: LinkRig,
) -> None:
    session, _lease, _guard = await open_rig_session(rig)
    data = b"x" * (MAX_CHUNK_BYTES * 2 + 5)

    await session.put_file(Path("big.bin"), data)

    chunks = [e.payload for e in rig.device.state.received if isinstance(e.payload, SessionPutFile)]
    assert [len(chunk.chunk) for chunk in chunks] == [MAX_CHUNK_BYTES, MAX_CHUNK_BYTES, 5]
    assert [chunk.final for chunk in chunks] == [False, False, True]
    assert chunks[-1].sha256 == hashlib.sha256(data).hexdigest()
    assert len({chunk.transfer_id for chunk in chunks}) == 1
    first_envelope = next(
        e for e in rig.device.state.received if isinstance(e.payload, SessionPutFile)
    )
    assert chunks[0].transfer_id == first_envelope.id
    assert await session.get_file(Path("big.bin")) == data


async def test_an_empty_file_is_one_final_chunk(rig: LinkRig) -> None:
    session, _lease, _guard = await open_rig_session(rig)

    await session.put_file(Path("empty"), b"")

    assert await session.get_file(Path("empty")) == b""


async def test_a_device_that_reports_another_digest_raises_transfer_integrity(
    rigs: RigFactory,
) -> None:
    rig = await rigs(DeviceScript(lie_about_sha256=True))
    session, _lease, _guard = await open_rig_session(rig)

    with pytest.raises(TransferIntegrityError):
        await session.put_file(Path("a.bin"), b"payload")


async def test_a_read_whose_digest_differs_raises_and_returns_no_bytes(rigs: RigFactory) -> None:
    rig = await rigs(DeviceScript())
    session, lease, _guard = await open_rig_session(rig)
    await session.put_file(Path("a.bin"), b"payload")
    rig.device.script.lie_about_sha256 = True

    with pytest.raises(TransferIntegrityError):
        await session.get_file(Path("a.bin"))
    assert lease.id


async def test_chunks_that_arrive_out_of_order_raise_transfer_integrity(rigs: RigFactory) -> None:
    rig = await rigs(DeviceScript())
    session, _lease, _guard = await open_rig_session(rig)
    await session.put_file(Path("big.bin"), b"y" * (MAX_CHUNK_BYTES + 1))
    rig.device.script.reorder_file_chunks = True

    with pytest.raises(TransferIntegrityError):
        await session.get_file(Path("big.bin"))


async def test_a_path_the_device_refuses_after_its_own_resolution_is_not_allowed(
    rigs: RigFactory,
) -> None:
    rig = await rigs(DeviceScript(allowed_scopes=()))
    session, lease, _guard = await open_rig_session(rig, allowed_paths=(Path("/pollen/linked"),))

    # The Hive judged the text reachable; the device resolved a link and refused (the second wall).
    with pytest.raises(PathNotAllowedError):
        await session.put_file(Path("/pollen/linked/x"), b"payload")
    assert lease.allowed_paths == (Path("/pollen/linked"),)


async def test_a_denied_write_sends_no_chunk(rig: LinkRig) -> None:
    guard = RecordingGuard()
    session, lease, _ = await open_rig_session(rig, guard)
    guard.deny.add(f"fs:write:{lease.scratch_root.as_posix()}/denied")

    with pytest.raises(CommandDeniedError):
        await session.put_file(Path("denied"), b"x")

    assert not [e for e in rig.device.state.received if isinstance(e.payload, SessionPutFile)]
    assert guard.sent == []


async def test_a_read_needs_only_fs_read_and_a_delete_needs_the_write_capabilities(
    rig: LinkRig,
) -> None:
    session, lease, guard = await open_rig_session(rig)
    await session.put_file(Path("a"), b"1")

    await session.get_file(Path("a"))
    await session.delete_file(Path("a"))

    get_command, delete_command = guard.authorised[-2:]
    assert [str(need) for need in get_command.needs] == [
        f"fs:read:{lease.scratch_root.as_posix()}/a"
    ]
    assert (delete_command.operation, get_command.operation) == ("delete_file", "get_file")
    assert [str(need) for need in delete_command.needs] == [
        f"fs:write:{lease.scratch_root.as_posix()}/a"
    ]


async def test_the_trail_record_names_the_operation_and_never_the_path_or_the_bytes(
    rig: LinkRig,
) -> None:
    session, _lease, guard = await open_rig_session(rig)

    await session.put_file(Path("secret-name.txt"), b"secret bytes")

    command, _request_id = guard.sent[-1]
    assert command.operation == "put_file"
    assert "secret" not in repr((command.program, command.operation, command.argument_count))


async def test_the_device_refusing_an_unexpected_code_is_a_device_request_error(
    rigs: RigFactory,
) -> None:
    from waggle.messages.session.files import SessionGetFile

    rig = await rigs(DeviceScript(refusals={SessionGetFile: "pollen.transfer_refused"}))
    session, _lease, _guard = await open_rig_session(rig)

    with pytest.raises(DeviceRequestError) as raised:
        await session.get_file(Path("big.bin"))

    assert raised.value.device_code == "pollen.transfer_refused"


async def test_a_write_the_device_does_not_end_with_its_exit_is_an_error(rigs: RigFactory) -> None:
    rig = await rigs(DeviceScript())
    session, _lease, _guard = await open_rig_session(rig)

    async def wrong_answer(request: Envelope) -> None:
        await rig.device.send_raw(make_heartbeat(rig.party), request.id)

    rig.device.override(SessionPutFile, wrong_answer)

    with pytest.raises(DeviceRequestError) as caught:
        await session.put_file(Path("out.txt"), b"data")

    assert caught.value.device_code == UNEXPECTED_REPLY_CODE
