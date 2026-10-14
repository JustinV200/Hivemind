"""Gateway conformance suite, part three: files and observation.

These tests state sections 9.4 and 12 of the gateway contract: a file moves in verified chunks and
is written whole or not at all, a path is resolved before it is judged, and an observation comes
from fixed collectors, is cut to what was asked for and needs the capabilities it names. Part one,
`test_gateway_contract.py`, starts the gateway under test and documents how to point the suite at
another one (`HIVEMIND_GATEWAY_COMMAND`); this module shares its assumption that the gateway runs
on this machine.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises sections 9.4 and 12 of
    the gateway contract through `contracts.gateway`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - contracts.test_gateway_contract for the suite's overview and the command template.
    - docs/waggle/gateway-contract.md sections 9.4 and 12 for the clauses tested here.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from builders.swarm.live import LiveHive, LiveHiveOptions, start_live_hive
from contracts.gateway import (
    Gateway,
    exists,
    is_directory,
    names_in,
    read_file,
    refused,
    running_gateway,
    write_file,
)

from hivemind.cell import AccessLevel
from waggle.clock import SystemClock
from waggle.ids import new_device_id
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.session import (
    SessionOutcome,
    SessionRequestKind,
    SessionStream,
)
from waggle.messages.swarm import FileChangeKind, ObservationKind

pytestmark = pytest.mark.asyncio(loop_scope="module")

HEARTBEAT_S = 5.0  # These tests need no quick beat; a quiet link keeps their frames easy to read.
DENIED = "pollen.capability_denied"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def hive() -> AsyncIterator[LiveHive]:
    """A Hive Stand with the real listener."""
    stand = await start_live_hive(LiveHiveOptions(heartbeat_interval_s=HEARTBEAT_S))
    yield stand
    await stand.stop()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def gateway(
    hive: LiveHive, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[Gateway]:
    """A gateway enrolled with a FULL invite and online."""
    async with running_gateway(hive, tmp_path_factory.mktemp("full"), AccessLevel.FULL) as g:
        yield g


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scratch(
    hive: LiveHive, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[Gateway]:
    """A gateway whose invite is capped at SCRATCH."""
    async with running_gateway(hive, tmp_path_factory.mktemp("sc"), AccessLevel.SCRATCH) as g:
        yield g


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def watcher(
    hive: LiveHive, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[Gateway]:
    """A FULL gateway reserved for the observation tests that change what it may watch."""
    async with running_gateway(hive, tmp_path_factory.mktemp("watch"), AccessLevel.FULL) as g:
        yield g


# ── Files (sections 9.2 and 12) ────────────────────────────────────────────────────────


async def test_a_file_written_in_chunks_is_read_back_whole_with_size_and_digest(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    data = os.urandom(MAX_CHUNK_BYTES + MAX_CHUNK_BYTES // 2 + 17)

    async with driver.leased() as lease:
        wrote = await driver.put_file(lease, "deep/er/big.bin", data, chunk_bytes=100_000)
        read, chunks, closing = await driver.get_file(lease, "deep/er/big.bin")

    assert wrote.request_kind is SessionRequestKind.PUT_FILE and wrote.size_bytes == len(data)
    assert read == data and len(chunks) > 1
    assert all(c.stream is SessionStream.FILE and len(c.chunk) <= MAX_CHUNK_BYTES for c in chunks)
    assert [c.offset for c in chunks] == [
        sum(len(x.chunk) for x in chunks[:i]) for i in range(len(chunks))
    ]
    assert chunks[-1].final and not any(c.final for c in chunks[:-1])
    assert closing.request_kind is SessionRequestKind.GET_FILE
    assert (closing.size_bytes, closing.sha256) == (wrote.size_bytes, wrote.sha256)


async def test_an_empty_file_can_be_written_and_read(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        await driver.put_file(lease, "empty", b"")
        read, _chunks, closing = await driver.get_file(lease, "empty")

    assert read == b"" and closing.size_bytes == 0


async def test_a_relative_path_lands_in_scratch(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        await driver.put_file(lease, "sub/dir/f.txt", b"here")

        assert read_file(Path(lease.scratch_root) / "sub" / "dir" / "f.txt") == b"here"


async def test_a_put_whose_digest_is_wrong_is_refused_and_leaves_no_file_or_partial(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        transfer, messages = driver.put_messages(lease, "bad.bin", b"abc", digest="0" * 64)

        code = await refused(driver.send_chunks(transfer, messages))

        assert code == "pollen.transfer_refused"
        assert await refused(driver.get_file(lease, "bad.bin")) == "pollen.not_found"
        assert names_in(lease.scratch_root) == []  # No target, and the temporary file is gone.


async def test_a_transfer_that_does_not_begin_at_offset_zero_isrefused(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        transfer, messages = driver.put_messages(lease, "x.bin", b"abcdef")
        late_start = [messages[0].model_copy(update={"offset": 3, "chunk": b"def"})]

        assert await refused(driver.send_chunks(transfer, late_start)) == "pollen.transfer_refused"


async def test_a_file_larger_than_max_bytes_is_refused_not_truncated(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        await driver.put_file(lease, "ten.bin", b"0123456789")

        code = await refused(driver.get_file(lease, "ten.bin", max_bytes=5))

    assert code == "pollen.transfer_refused"


async def test_a_file_that_is_not_there_draws_not_found_for_a_read_and_a_delete(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        assert await refused(driver.get_file(lease, "missing.txt")) == "pollen.not_found"
        assert await refused(driver.delete_file(lease, "missing.txt")) == "pollen.not_found"


async def test_a_delete_removes_one_file_and_refuses_a_directory(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        await driver.put_file(lease, "dir/gone.txt", b"bye")

        gone = await driver.delete_file(lease, "dir/gone.txt")

        assert gone.request_kind is SessionRequestKind.DELETE_FILE
        assert gone.outcome is SessionOutcome.COMPLETED
        assert gone.size_bytes is None and gone.sha256 is None
        assert await refused(driver.delete_file(lease, "dir")) == "pollen.path_refused"
        assert is_directory(str(Path(lease.scratch_root) / "dir"))


async def test_a_link_in_scratch_that_points_outside_is_judged_where_it_points(
    scratch: Gateway, tmp_path: Path
) -> None:
    driver, target = scratch.driver, tmp_path / "elsewhere"
    target.mkdir()
    async with driver.leased() as lease:
        os.symlink(target, Path(lease.scratch_root) / "link")

        code = await refused(driver.put_file(lease, "link/planted.txt", b"no"))

        assert code == DENIED
        assert not exists(str(target / "planted.txt"))


async def test_a_write_that_would_pass_the_scratch_quota_isrefused(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased(quota=1_000) as lease:
        code = await refused(driver.put_file(lease, "too-big.bin", b"x" * 5_000))

        assert code == "pollen.quota_exceeded"
        assert (await driver.put_file(lease, "fits.bin", b"x" * 500)).size_bytes == 500


# ── Observation (section 9.4) ────────────────────────────────────────────────────────────


async def test_an_observation_reports_processes_and_resources_from_fixed_collectors(
    watcher: Gateway,
) -> None:
    driver = watcher.driver
    before = datetime.now(UTC)

    seen = await driver.observe([ObservationKind.PROCESS_LIST, ObservationKind.RESOURCE_USE])

    assert seen.device_id == watcher.node.device_id
    assert watcher.process.pid in {sample.pid for sample in seen.processes}
    assert seen.resources is not None and seen.resources.memory_bytes > 0
    assert seen.resources.disk_free_bytes <= seen.resources.disk_bytes
    assert (
        before - timedelta(seconds=5)
        <= seen.observed_at
        <= datetime.now(UTC) + timedelta(seconds=5)
    )


async def test_an_observation_is_cut_to_max_items_and_says_so(watcher: Gateway) -> None:
    seen = await watcher.driver.observe([ObservationKind.PROCESS_LIST], max_items=1)

    assert len(seen.processes) == 1 and seen.is_truncated


async def test_logs_and_file_changes_under_a_root_are_read_without_a_lease(
    watcher: Gateway, tmp_path: Path
) -> None:
    driver, root = watcher.driver, tmp_path / "watched"
    write_file(root / "app.log", b"first line\nsecond line\n")
    await driver.observe([ObservationKind.FILE_CHANGES], roots=[str(root)])  # The baseline look.

    write_file(root / "new.txt", b"hello")
    seen = await driver.observe(
        [ObservationKind.LOGS, ObservationKind.FILE_CHANGES], roots=[str(root)]
    )

    assert [line.line for line in seen.log_lines][-2:] == ["first line", "second line"]
    created = {Path(c.path).name for c in seen.file_changes if c.change is FileChangeKind.CREATED}
    assert "new.txt" in created


async def test_an_observation_naming_another_device_is_refused_as_invalid(
    watcher: Gateway,
) -> None:
    driver = watcher.driver
    bad = driver.observation_request(
        [ObservationKind.PROCESS_LIST], device_id=new_device_id(SystemClock())
    )

    assert await driver.refusal(bad) == "waggle.codec.invalid_payload"


async def test_an_observation_needs_the_watch_capability_and_read_on_each_root(
    watcher: Gateway, tmp_path: Path
) -> None:
    driver = watcher.driver
    try:
        await driver.update_terms(capabilities=("fs:read:**",))  # No watch:<cell>.

        assert await refused(driver.observe([ObservationKind.PROCESS_LIST])) == DENIED

        await driver.update_terms(capabilities=(f"watch:{watcher.node.cell_id}",))  # No fs:read.

        assert (
            await refused(driver.observe([ObservationKind.LOGS], roots=[str(tmp_path)])) == DENIED
        )
        assert (await driver.observe([ObservationKind.PROCESS_LIST])).processes
    finally:
        await driver.update_terms(access=AccessLevel.FULL)
