"""Integration: what a Night Veil Cell leaves on a real Docker daemon, and what its end removes.

`@pytest.mark.integration` (codingrules 14.2). Skips cleanly when no Docker daemon answers or the
small `python:3.12-slim` image is not present. Codingrules section 12 against a real daemon, for
the two things only a real one can prove: a Night Veil container is created with the `none` log
driver (so `docker logs` and the host's json-file log hold nothing of it), and every snapshot image
committed of a Cell is found again by the label the commit stamped on it, from a fresh client in
another object (as a teardown in another process would), and removed, while another Cell's
snapshot image stays. No Warden boots here: the container is created and committed, never started.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises hive/backends/docker/sdk_client.py and
    hive/snapshot/docker.py against a real daemon.

Key invariants:
    - Only ids minted here are ever removed: every container, network and image this test makes
      is named or labelled with a Cell id it minted, so a daemon other work shares is safe.

See Also:
    - packages/hivemind/tests/integration/test_docker_snapshot_and_cli.py for snapshot/rollback.
    - hivemind.hive.snapshot.docker.DockerSnapshotImages for the side channel under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from builders.cells import make_cell
from integration.docker_helpers import daemon_reachable, image_present

from hivemind.cell import Cell, CellKind
from hivemind.hive.backends.docker import SdkDockerClient
from hivemind.hive.backends.docker.backend import container_name
from hivemind.hive.backends.docker.client import ContainerSpec, NetworkSpec
from hivemind.hive.snapshot import DockerSnapshotImages, DockerSnapshotter, SnapshotLedger
from waggle.clock import SystemClock

pytestmark = pytest.mark.integration

_IMAGE = "python:3.12-slim"  # Small, and needs no Warden: nothing here is ever started.
_SNAPSHOT_OF = "hivemind.snapshot_of"


def _skip_unless_docker() -> None:
    if not daemon_reachable():
        pytest.skip("no Docker daemon reachable")
    if not image_present(_IMAGE):
        pytest.skip(f"{_IMAGE} is not present on the daemon")


def _spec(cell: Cell, network: str, log_driver: str | None) -> ContainerSpec:
    """A minimal container for `cell`, never started, created with `log_driver`."""
    return ContainerSpec(
        name=container_name(cell.id),
        image=_IMAGE,
        environment={},
        labels={"hivemind.test": "night_veil_teardown"},
        network_name=network,
        extra_hosts={},
        volume_name=None,
        volume_mount_path="/scratch",
        nano_cpus=500_000_000,
        mem_limit_bytes=64 * 1024 * 1024,
        pids_limit=16,
        cap_drop=("ALL",),
        security_opt=("no-new-privileges:true",),
        read_only_rootfs=False,
        log_driver=log_driver,
    )


@asynccontextmanager
async def _containers(
    client: SdkDockerClient, *cells: tuple[Cell, str | None]
) -> AsyncIterator[None]:
    """Create one container per `(cell, log_driver)` on a fresh network; remove all afterwards."""
    network = f"hivemind-test-{cells[0][0].id}"
    await client.create_network(NetworkSpec(name=network, internal=True, labels={}))
    try:
        for cell, driver in cells:
            await client.create_container(_spec(cell, network, driver))
        yield
    finally:
        for cell, _driver in cells:
            await client.remove_container(container_name(cell.id), force=True)
            for image in await client.list_images({_SNAPSHOT_OF: str(cell.id)}):
                await client.remove_image(image)  # Whatever the test left, it made itself.
        await client.remove_network(network)


def _log_driver(cell: Cell) -> str:
    """The log driver the daemon itself reports for `cell`'s container."""
    import docker

    attrs = docker.from_env().containers.get(container_name(cell.id)).attrs
    return str(attrs["HostConfig"]["LogConfig"]["Type"])


async def test_a_night_veil_container_logs_nowhere_and_its_snapshot_images_go_with_it() -> None:
    _skip_unless_docker()
    clock = SystemClock()
    client = SdkDockerClient()
    night_veil = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    meadow = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    async with _containers(client, (night_veil, "none"), (meadow, None)):
        assert _log_driver(night_veil) == "none"
        assert _log_driver(meadow) != "none"  # The daemon's own default, whatever it is here.
        snapshotter = DockerSnapshotter(client, SnapshotLedger(), clock)
        for cell in (night_veil, night_veil, meadow):
            await snapshotter.snapshot(cell)

        # A fresh client, as a teardown in another process has: the images are found by label.
        removed = await DockerSnapshotImages(SdkDockerClient()).purge(night_veil.id, frozenset())

        assert removed == 2
        assert await client.list_images({_SNAPSHOT_OF: str(night_veil.id)}) == ()
        assert len(await client.list_images({_SNAPSHOT_OF: str(meadow.id)})) == 1
