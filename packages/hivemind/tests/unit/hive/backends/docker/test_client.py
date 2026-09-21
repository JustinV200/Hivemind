"""Unit tests for hivemind.hive.backends.docker.client: value types and DockerClientError.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/client.py (codingrules section 3). DockerClientPort itself
    is a Protocol with no logic of its own; both implementations' own behaviour is covered by
    test_fake.py (FakeDockerClient) and, indirectly, test_backend.py (DockerCellBackend).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.client for ContainerSpec, ContainerInfo, NetworkSpec,
      VolumeSpec and DockerClientError, under test.
"""

from __future__ import annotations

import dataclasses

import pytest

from hivemind.hive.backends.docker.client import ContainerSpec, DockerClientError, NetworkSpec


def test_docker_client_error_is_a_plain_exception() -> None:
    error = DockerClientError("container 'x' not found")

    assert isinstance(error, Exception)
    assert str(error) == "container 'x' not found"


def test_container_spec_tmpfs_defaults_to_empty() -> None:
    spec = ContainerSpec(
        name="hivemind-cell-test",
        image="hivemind/base-ubuntu:dev",
        environment={},
        labels={},
        network_name="hivemind-cell-test-net",
        extra_hosts={},
        volume_name=None,
        volume_mount_path="/var/lib/hivemind/scratch",
        nano_cpus=1_000_000_000,
        mem_limit_bytes=1024,
        pids_limit=64,
        cap_drop=("ALL",),
        security_opt=("no-new-privileges:true",),
        read_only_rootfs=True,
    )

    assert spec.tmpfs == {}


def test_container_spec_is_frozen() -> None:
    spec = ContainerSpec(
        name="hivemind-cell-test",
        image="hivemind/base-ubuntu:dev",
        environment={},
        labels={},
        network_name="hivemind-cell-test-net",
        extra_hosts={},
        volume_name=None,
        volume_mount_path="/var/lib/hivemind/scratch",
        nano_cpus=1_000_000_000,
        mem_limit_bytes=1024,
        pids_limit=64,
        cap_drop=("ALL",),
        security_opt=("no-new-privileges:true",),
        read_only_rootfs=True,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.image = "other"  # type: ignore[misc]  # The assignment is the test.


def test_network_spec_round_trips_its_own_fields() -> None:
    network = NetworkSpec(name="hivemind-cell-test-net", internal=True, labels={"a": "b"})

    assert network.name == "hivemind-cell-test-net"
    assert network.internal is True
    assert network.labels == {"a": "b"}
