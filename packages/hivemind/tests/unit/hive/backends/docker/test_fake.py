"""Unit tests for hivemind.hive.backends.docker.fake: FakeDockerClient's own extra surface.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/fake.py (codingrules section 3). DockerCellBackend's own
    use of this fake (provisioning, cleanup-on-failure, listing) is covered by test_backend.py;
    this module covers FakeDockerClient's own switches, idempotency and call recording directly.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.fake for FakeDockerClient, the class under test.
"""

from __future__ import annotations

import pytest

from hivemind.hive.backends.docker.client import (
    ContainerSpec,
    DockerClientError,
    NetworkSpec,
    VolumeSpec,
)
from hivemind.hive.backends.docker.fake import FakeDockerClient

_CONTAINER_SPEC = ContainerSpec(
    name="hivemind-cell-test",
    image="hivemind/base-ubuntu:dev",
    environment={"HIVEMIND_CELL_ID": "cell_test"},
    labels={"hivemind.hive_id": "hive_test", "hivemind.cell_id": "cell_test"},
    network_name="hivemind-cell-test-net",
    extra_hosts={"host.docker.internal": "host-gateway"},
    volume_name="hivemind-cell-test-scratch",
    volume_mount_path="/var/lib/hivemind/scratch",
    nano_cpus=1_000_000_000,
    mem_limit_bytes=1024,
    pids_limit=64,
    cap_drop=("ALL",),
    security_opt=("no-new-privileges:true",),
    read_only_rootfs=True,
)


async def test_create_and_list_containers_round_trips_labels() -> None:
    client = FakeDockerClient()

    await client.create_container(_CONTAINER_SPEC)
    records = await client.list_containers({"hivemind.hive_id": "hive_test"})

    assert [record.name for record in records] == ["hivemind-cell-test"]
    assert records[0].status == "created"


async def test_list_containers_requires_every_given_label_to_match() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)

    assert await client.list_containers({"hivemind.hive_id": "other-hive"}) == ()
    assert len(await client.list_containers({"hivemind.hive_id": "hive_test"})) == 1


async def test_start_container_marks_it_running() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)

    await client.start_container(_CONTAINER_SPEC.name)

    records = await client.list_containers({})
    assert records[0].status == "running"


async def test_pause_and_unpause_round_trip_status() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)
    await client.start_container(_CONTAINER_SPEC.name)

    await client.pause_container(_CONTAINER_SPEC.name)
    assert (await client.list_containers({}))[0].status == "paused"

    await client.unpause_container(_CONTAINER_SPEC.name)
    assert (await client.list_containers({}))[0].status == "running"


async def test_remove_container_of_an_unknown_name_is_a_silent_no_op() -> None:
    client = FakeDockerClient()

    await client.remove_container("never-created", force=True)  # must not raise

    assert client.remove_container_calls == ["never-created"]


async def test_remove_network_and_volume_of_unknown_names_are_silent_no_ops() -> None:
    client = FakeDockerClient()

    await client.remove_network("never-created-net")  # must not raise
    await client.remove_volume("never-created-vol")  # must not raise


async def test_create_container_failure_is_one_shot() -> None:
    client = FakeDockerClient()
    client.set_create_container_failure("simulated outage")

    with pytest.raises(DockerClientError, match="simulated outage"):
        await client.create_container(_CONTAINER_SPEC)

    # The switch fired once and cleared itself: a second attempt with the same spec succeeds,
    # matching a real daemon recovering after one transient failure.
    await client.create_container(_CONTAINER_SPEC)
    assert len(await client.list_containers({})) == 1


async def test_start_container_failure_is_one_shot() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)
    client.set_start_container_failure("daemon busy")

    with pytest.raises(DockerClientError, match="daemon busy"):
        await client.start_container(_CONTAINER_SPEC.name)

    await client.start_container(_CONTAINER_SPEC.name)  # succeeds the second time
    assert (await client.list_containers({}))[0].status == "running"


async def test_create_network_failure_is_one_shot() -> None:
    client = FakeDockerClient()
    client.set_create_network_failure("name conflict")
    spec = NetworkSpec(name="hivemind-cell-test-net", internal=True, labels={})

    with pytest.raises(DockerClientError, match="name conflict"):
        await client.create_network(spec)

    await client.create_network(spec)  # succeeds the second time


async def test_create_volume_failure_is_one_shot() -> None:
    client = FakeDockerClient()
    client.set_create_volume_failure("disk full")
    spec = VolumeSpec(name="hivemind-cell-test-scratch", labels={})

    with pytest.raises(DockerClientError, match="disk full"):
        await client.create_volume(spec)

    await client.create_volume(spec)  # succeeds the second time


async def test_remove_container_failure_is_one_shot() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)
    client.set_remove_container_failure("still in use")

    with pytest.raises(DockerClientError, match="still in use"):
        await client.remove_container(_CONTAINER_SPEC.name, force=True)

    await client.remove_container(_CONTAINER_SPEC.name, force=True)  # succeeds the second time
    assert await client.list_containers({}) == ()


async def test_every_call_is_recorded() -> None:
    client = FakeDockerClient()
    network = NetworkSpec(name="net", internal=False, labels={})
    volume = VolumeSpec(name="vol", labels={})

    await client.create_network(network)
    await client.create_volume(volume)
    await client.create_container(_CONTAINER_SPEC)
    await client.start_container(_CONTAINER_SPEC.name)
    await client.pause_container(_CONTAINER_SPEC.name)
    await client.unpause_container(_CONTAINER_SPEC.name)
    await client.remove_container(_CONTAINER_SPEC.name, force=True)
    await client.remove_volume(volume.name)
    await client.remove_network(network.name)

    assert client.create_network_calls == [network]
    assert client.create_volume_calls == [volume]
    assert client.create_container_calls == [_CONTAINER_SPEC]
    assert client.start_container_calls == [_CONTAINER_SPEC.name]
    assert client.pause_container_calls == [_CONTAINER_SPEC.name]
    assert client.unpause_container_calls == [_CONTAINER_SPEC.name]
    assert client.remove_container_calls == [_CONTAINER_SPEC.name]
    assert client.remove_volume_calls == [volume.name]
    assert client.remove_network_calls == [network.name]
