"""Integration: a Pollen Packet in a container is leased and driven through PollenSession.

Roadmap step 11.5 on a second machine. The CellSession contract proves `PollenSession` against a
real packet that shares the Hive Stand's disk and process table; this proves it across a real
boundary. A swarm listener serves `wss://` with a self-signed certificate on the docker bridge's
host address; a container built from `e2e/swarm/device/Dockerfile` (the packet installed from
wheels built from this tree) maps a host name to that address with `--add-host`, enrols with an
invite that names the host and pins the certificate, and dials in. The device is then leased
through `SwarmSource` with the real Guard in front, and the session runs commands, moves files,
starts and stops a background program and closes. Everything it did happened inside the
container: its host name, its process ids, its files. Afterwards the lease's scratch directory
is gone, the file written outside scratch is gone, and the container's process table and the
directories' file trees are exactly as they were before the lease. A second test cuts a container
off its network and puts it back, the lever the exit-criteria scenarios use to take a device
away, and shows the janitor leaves nothing behind.

`@pytest.mark.integration` (codingrules 14.2): skips cleanly when no daemon answers or when
`HIVEMIND_TEST_DEVICE_IMAGE` names an image the daemon lacks. The first run builds the device
image (about a minute: wheels, an apt layer, a pip install); later runs find it by its tag.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises swarm/session,
    swarm/source and the gateway listener against pollen in a container, through `e2e.swarm`.

Key invariants:
    - The container is always removed, whatever the test did: the janitor closes in a `finally`.
    - Waits on the container's process table are bounded polls of a real process, not sleeps.

See Also:
    - tests/contracts/test_cell_session_contract.py for the same clauses against a packet in
      this process.
    - e2e/swarm/ for the image, container and Stand helpers used here.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest
import pytest_asyncio
from builders.swarm.leasing import lease_device
from builders.swarm.live import LiveHive
from e2e.swarm import (
    DATA_DIR,
    ContainerDevice,
    ContainerSpec,
    ImageUnavailableError,
    Janitor,
    daemon_answers,
    device_image,
    docker,
    enrol_container,
    start_stand,
)
from e2e.swarm.container import Container, FileEntry

from hivemind.cell.session import BackgroundSpec, ExecSpec, run
from waggle.messages.swarm import RuntimeLevel

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TEST_TIMEOUT_S = 180.0  # A whole scenario, a cold packet start included; bounds a hang.
SETTLE_S = 10.0  # How long a killed program may take to leave the container's process table.
POLL_S = 0.25
SHARED = "/srv/shared"  # A directory outside scratch that the lease allows the session to write.
# What a container runs to dial the host: exits 0 on a connection, non-zero when it cannot.
DIAL = "import socket, sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), 3).close()"


@dataclass(frozen=True, slots=True)
class Rig:
    """The Hive Stand and the container device enrolled on it."""

    hive: LiveHive
    device: ContainerDevice


@pytest.fixture(scope="module")
def image() -> str:
    """The device image for this tree, built on first use; the module skips without a daemon."""
    if not daemon_answers():
        pytest.skip("no Docker daemon reachable")
    try:
        return device_image()
    except ImageUnavailableError as missing:
        pytest.skip(str(missing))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def rig(image: str, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Rig]:
    """A Hive Stand on the docker bridge and a packet in a container enrolled on it."""
    janitor, hive = Janitor(), None
    try:
        hive = await start_stand(tmp_path_factory.mktemp("stand"))
        yield Rig(hive, await enrol_container(hive, janitor, image))
    finally:
        janitor.close()  # The container first, so the Stand sees a device go, not a Stand go.
        if hive is not None:
            await hive.stop()


async def _settled_to(container: Container, expected: dict[int, str]) -> dict[int, str]:
    """Poll the container's process table until it equals `expected`, or `SETTLE_S` has passed."""
    deadline = time.monotonic() + SETTLE_S
    while True:
        table = await asyncio.to_thread(container.processes)
        if table == expected or time.monotonic() > deadline:
            return table
        await asyncio.sleep(POLL_S)


def _files_under(container: Container, path: str) -> dict[str, FileEntry]:
    """Snapshot a directory, or nothing if it is not there.

    The packet makes its `leases` directory when the first lease opens, so before that there is
    none; no directory and an empty one both mean the lease left nothing behind.
    """
    return container.file_tree(path) if container.exists(path) else {}


async def _close_at_once(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Accept a connection and hang up: enough to show a dialler could connect."""
    writer.close()
    await writer.wait_closed()


async def test_the_packet_enrols_in_the_container_over_tls_and_the_hive_lists_it(rig: Rig) -> None:
    node = await rig.hive.node(rig.device.node.device_id)

    assert node.level is RuntimeLevel.GATEWAY
    assert rig.device.container.is_running()
    # The invite named a host that exists only in the container's /etc/hosts, and wss.
    assert rig.hive.url.startswith("wss://hive.test:")
    # The packet is the container's own process, started by the container's init.
    assert "docker-init" in rig.device.container.processes().values()


async def test_a_session_runs_in_the_container_and_leaves_the_device_as_it_found_it(
    rig: Rig,
) -> None:
    container = rig.device.container
    container.exec(["mkdir", "-p", SHARED])
    processes_before = container.processes()
    leases_before = _files_under(container, f"{DATA_DIR}/leases")
    shared_before = container.file_tree(SHARED)

    async with asyncio.timeout(TEST_TIMEOUT_S):
        leased = await lease_device(
            rig.hive, rig.device.node, PurePosixPath(DATA_DIR), (Path(SHARED),)
        )
        session, scratch = leased.session, leased.session.scratch_dir

        # A command runs on the container: its host name is the container's, not this machine's.
        named = await run(session, ExecSpec(argv=("hostname",)))
        assert named.stdout.decode().strip() == container.exec(["hostname"]).stdout.strip()
        assert named.stdout.decode().strip() != socket.gethostname()
        script = "echo out; echo err >&2; exit 3"
        failed = await run(session, ExecSpec(argv=("sh", "-c", script)))
        assert (failed.exit_code, failed.stdout, failed.stderr) == (3, b"out\n", b"err\n")

        # Files land on the container's disk, in scratch and in the directory the lease allows.
        await session.put_file(Path("note.txt"), b"hello from the hive")
        assert container.exists(f"{scratch}/note.txt")
        assert await session.get_file(Path("note.txt")) == b"hello from the hive"
        await session.put_file(Path(SHARED) / "outside.txt", b"outside scratch")
        assert container.exists(f"{SHARED}/outside.txt")
        await session.delete_file(Path("note.txt"))
        assert not container.exists(f"{scratch}/note.txt")

        # A background program runs in the container's process table and stops with its tree.
        started = await session.start(BackgroundSpec(argv=("sleep", "600")))
        assert container.processes().get(started.pid) == "sleep"
        assert await session.is_running(started)
        assert await session.stop(started) is True
        assert not await session.is_running(started)

        await session.close()
        assert not session.is_open
        report = await leased.lease.release()

    assert report.is_restored
    assert not container.exists(str(scratch))
    assert container.file_tree(SHARED) == shared_before  # The file written outside is gone.
    assert _files_under(container, f"{DATA_DIR}/leases") == leases_before
    assert await _settled_to(container, processes_before) == processes_before


async def test_a_container_cut_off_its_network_cannot_reach_the_host_until_it_is_put_back(
    image: str,
) -> None:
    janitor = Janitor()
    try:
        network = janitor.network()
        container = janitor.start(ContainerSpec(image=image, network=network.name))
        server = await asyncio.start_server(_close_at_once, network.gateway, 0)
        async with server:
            dial = ["python3", "-c", DIAL, network.gateway, str(server.sockets[0].getsockname()[1])]

            assert container.exec(dial, check=False).exit_code == 0
            network.disconnect(container)
            assert container.exec(dial, check=False).exit_code != 0
            network.connect(container)
            assert container.exec(dial, check=False).exit_code == 0
    finally:
        janitor.close()

    # The janitor left neither the container nor its network behind.
    assert docker("ps", "--all", "--quiet", "--filter", f"name={container.name}").stdout == ""
    assert docker("network", "ls", "--quiet", "--filter", f"name={network.name}").stdout == ""
