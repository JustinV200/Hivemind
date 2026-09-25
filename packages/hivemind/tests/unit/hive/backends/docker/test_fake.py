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

import dataclasses

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


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 5.10: commit_container / remove_image / recreate_from_image
# ──────────────────────────────────────────────────────────────────────────────


async def test_commit_container_returns_a_repository_tagged_image_ref() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)

    result = await client.commit_container(
        _CONTAINER_SPEC.name, repository="hivemind-snapshot", tag="abc", labels={}
    )

    assert result.image == "hivemind-snapshot:abc"
    assert client.commit_container_calls == [_CONTAINER_SPEC.name]


async def test_commit_container_reports_the_arranged_size() -> None:
    client = FakeDockerClient()
    client.set_commit_size_bytes(4096)
    await client.create_container(_CONTAINER_SPEC)

    result = await client.commit_container(
        _CONTAINER_SPEC.name, repository="hivemind-snapshot", tag="abc", labels={}
    )

    assert result.size_bytes == 4096


async def test_commit_container_of_an_unknown_name_raises() -> None:
    client = FakeDockerClient()

    with pytest.raises(DockerClientError):
        await client.commit_container(
            "never-created", repository="hivemind-snapshot", tag="abc", labels={}
        )


async def test_commit_container_failure_is_one_shot() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)
    client.set_commit_container_failure("daemon busy")

    with pytest.raises(DockerClientError, match="daemon busy"):
        await client.commit_container(
            _CONTAINER_SPEC.name, repository="hivemind-snapshot", tag="abc", labels={}
        )

    await client.commit_container(  # succeeds the second time
        _CONTAINER_SPEC.name, repository="hivemind-snapshot", tag="abc", labels={}
    )


async def test_list_images_finds_committed_images_by_every_given_label() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)
    for tag, cell in (("a", "cell_one"), ("b", "cell_two")):
        await client.commit_container(
            _CONTAINER_SPEC.name, repository="snap", tag=tag, labels={"of": cell, "kind": "x"}
        )

    assert await client.list_images({"of": "cell_one"}) == ("snap:a",)
    assert await client.list_images({"of": "cell_two", "kind": "x"}) == ("snap:b",)
    assert await client.list_images({"of": "cell_one", "kind": "y"}) == ()
    await client.remove_image("snap:a")
    assert await client.list_images({"of": "cell_one"}) == ()


async def test_remove_image_of_an_unknown_ref_is_a_silent_no_op() -> None:
    client = FakeDockerClient()

    await client.remove_image("never-committed:tag")  # must not raise

    assert client.remove_image_calls == ["never-committed:tag"]


async def test_recreate_from_image_swaps_the_tracked_containers_own_image() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)

    await client.recreate_from_image(_CONTAINER_SPEC.name, "hivemind-snapshot:abc")

    records = await client.list_containers({})
    assert records[0].status == "running"


async def test_recreate_from_image_of_an_unknown_name_raises() -> None:
    client = FakeDockerClient()

    with pytest.raises(DockerClientError):
        await client.recreate_from_image("never-created", "hivemind-snapshot:abc")


async def test_recreate_from_image_failure_is_one_shot() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)
    client.set_recreate_from_image_failure("daemon busy")

    with pytest.raises(DockerClientError, match="daemon busy"):
        await client.recreate_from_image(_CONTAINER_SPEC.name, "hivemind-snapshot:abc")

    await client.recreate_from_image(  # succeeds the second time
        _CONTAINER_SPEC.name, "hivemind-snapshot:abc"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 10.6a: the network slice (ensure, attach, detach, attachments listed).
# ──────────────────────────────────────────────────────────────────────────────

_CONTROL = NetworkSpec(
    name="hivemind-hive_test-control",
    internal=True,
    labels={"hivemind.hive_id": "hive_test"},
    subnet="10.213.7.0/24",
    gateway="10.213.7.1",
    isolates_peers=True,
)
_OWN = NetworkSpec(name="hivemind-cell-test-net", internal=False, labels={})


async def _dual_homed(client: FakeDockerClient) -> None:
    """Create the two networks, a container on the control one, attached to its own too."""
    await client.ensure_network(_CONTROL)
    await client.create_network(_OWN)
    await client.create_container(dataclasses.replace(_CONTAINER_SPEC, network_name=_CONTROL.name))
    await client.connect_network(_OWN.name, _CONTAINER_SPEC.name)


async def test_ensure_network_creates_once_then_reuses_the_same_one() -> None:
    client = FakeDockerClient()

    first = await client.ensure_network(_CONTROL)
    second = await client.ensure_network(_CONTROL)

    assert first == second == _CONTROL.name
    assert client.networks == {_CONTROL.name: _CONTROL}


async def test_ensure_network_refuses_a_same_named_network_that_does_not_match() -> None:
    client = FakeDockerClient()
    await client.create_network(NetworkSpec(name=_CONTROL.name, internal=False, labels={}))

    # A non-internal network of the control network's name would carry the Cells' egress too.
    with pytest.raises(DockerClientError, match="does not match"):
        await client.ensure_network(_CONTROL)


async def test_a_container_lists_every_network_it_is_attached_to() -> None:
    client = FakeDockerClient()

    await _dual_homed(client)
    records = await client.list_containers({"hivemind.cell_id": "cell_test"})

    assert records[0].networks == tuple(sorted((_CONTROL.name, _OWN.name)))


async def test_detach_and_attach_move_a_container_off_and_back_on_idempotently() -> None:
    client = FakeDockerClient()
    await _dual_homed(client)

    await client.disconnect_network(_OWN.name, _CONTAINER_SPEC.name)
    await client.disconnect_network(_OWN.name, _CONTAINER_SPEC.name)
    cut = client.attached(_CONTAINER_SPEC.name)
    await client.connect_network(_OWN.name, _CONTAINER_SPEC.name)
    await client.connect_network(_OWN.name, _CONTAINER_SPEC.name)

    assert cut == frozenset({_CONTROL.name})
    assert client.attached(_CONTAINER_SPEC.name) == frozenset({_CONTROL.name, _OWN.name})


async def test_attaching_to_a_missing_network_or_container_raises() -> None:
    client = FakeDockerClient()
    await client.create_container(_CONTAINER_SPEC)

    with pytest.raises(DockerClientError, match="not found"):
        await client.connect_network("no-such-network", _CONTAINER_SPEC.name)
    with pytest.raises(DockerClientError, match="not found"):
        await client.disconnect_network(_OWN.name, "no-such-container")


async def test_the_attach_failure_switch_fires_once() -> None:
    client = FakeDockerClient()
    await _dual_homed(client)
    client.set_attach_failure("daemon busy")

    with pytest.raises(DockerClientError, match="daemon busy"):
        await client.disconnect_network(_OWN.name, _CONTAINER_SPEC.name)
    await client.disconnect_network(_OWN.name, _CONTAINER_SPEC.name)

    assert client.attached(_CONTAINER_SPEC.name) == frozenset({_CONTROL.name})
