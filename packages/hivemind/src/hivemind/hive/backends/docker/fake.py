"""Provide FakeDockerClient: an in-memory DockerClientPort for tests, demos and hive doctor.

Three in-memory tables (containers, networks, volumes), keyed by name, stand in for a real Docker
daemon. Every method records its call and every create/remove step has its own failure switch
(`set_create_container_failure`, `set_start_container_failure`, and so on), so a test can prove
`DockerCellBackend.provision`'s all-or-nothing cleanup at each stage without ever touching a real
daemon (roadmap step 5.4's own test list: "cleanup-on-failure at each stage"). Shipped code, not
test-only (codingrules 14.4: "fakes live in src/ beside their Protocol"), because `hive doctor` and
demo paths use it too, exactly like `hivemind.hive.backends.fake.FakeCellBackend`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker`. Implements
    `hivemind.hive.backends.docker.client.DockerClientPort`; constructed directly by tests, demo
    scripts and `hive doctor`. Calls into hivemind.hive.backends.docker.client only.

Key invariants:
    - remove_container/remove_network/remove_volume never raise for a name that is not in this
      fake's own table: DockerClientPort's own idempotency invariant, honoured here exactly as a
      real daemon (empty-result "docker rm" of an already-gone resource) would be.
    - A `set_*_failure` switch is one-shot per call, not sticky: it fires on the very next matching
      call and is cleared immediately after, so a test does not have to remember to disarm it
      before the same backend instance is reused for a second, successful provision.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code."
    - hivemind.hive.backends.docker.client for DockerClientPort, ContainerSpec, ContainerInfo,
      NetworkSpec, VolumeSpec and DockerClientError, everything this class implements and raises.
    - hivemind.hive.backends.fake for FakeCellBackend, the switches-and-call-recording pattern
      this mirrors for a different Protocol.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from hivemind.hive.backends.docker.client import (
    ContainerInfo,
    ContainerSpec,
    DockerClientError,
    NetworkSpec,
    VolumeSpec,
)

__all__ = ["FakeDockerClient"]


@dataclass(slots=True)
class _TrackedContainer:
    """This fake's own bookkeeping for one created container; never exposed outside this file."""

    spec: ContainerSpec
    status: str
    created_at: datetime


class FakeDockerClient:
    """An in-memory DockerClientPort: creates, starts, pauses and removes with no real daemon."""

    def __init__(self) -> None:
        """Create a FakeDockerClient with nothing created and no failure armed."""
        self._containers: dict[str, _TrackedContainer] = {}
        self._networks: dict[str, NetworkSpec] = {}
        self._volumes: dict[str, VolumeSpec] = {}
        # One-shot failure reasons: set_*_failure arms the next matching call, which then clears
        # it (see the module docstring's key invariant).
        self._create_container_failure: str | None = None
        self._start_container_failure: str | None = None
        self._create_network_failure: str | None = None
        self._create_volume_failure: str | None = None
        self._remove_container_failure: str | None = None
        self.create_container_calls: list[ContainerSpec] = []
        self.start_container_calls: list[str] = []
        self.remove_container_calls: list[str] = []
        self.create_network_calls: list[NetworkSpec] = []
        self.remove_network_calls: list[str] = []
        self.create_volume_calls: list[VolumeSpec] = []
        self.remove_volume_calls: list[str] = []
        self.pause_container_calls: list[str] = []
        self.unpause_container_calls: list[str] = []

    def set_create_container_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `create_container` call to raise."""
        self._create_container_failure = reason

    def set_start_container_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `start_container` call to raise."""
        self._start_container_failure = reason

    def set_create_network_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `create_network` call to raise."""
        self._create_network_failure = reason

    def set_create_volume_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `create_volume` call to raise."""
        self._create_volume_failure = reason

    def set_remove_container_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the next `remove_container` call to raise."""
        self._remove_container_failure = reason

    async def create_network(self, spec: NetworkSpec) -> str:
        """Record and store `spec`; see `DockerClientPort.create_network`."""
        self.create_network_calls.append(spec)
        _fire(self, "_create_network_failure", f"network {spec.name!r}")
        self._networks[spec.name] = spec
        return spec.name

    async def remove_network(self, name: str) -> None:
        """Drop `name` from this fake's table; see `DockerClientPort.remove_network`."""
        self.remove_network_calls.append(name)
        self._networks.pop(name, None)

    async def create_volume(self, spec: VolumeSpec) -> str:
        """Record and store `spec`; see `DockerClientPort.create_volume`."""
        self.create_volume_calls.append(spec)
        _fire(self, "_create_volume_failure", f"volume {spec.name!r}")
        self._volumes[spec.name] = spec
        return spec.name

    async def remove_volume(self, name: str) -> None:
        """Drop `name` from this fake's table; see `DockerClientPort.remove_volume` (idempotent)."""
        self.remove_volume_calls.append(name)
        self._volumes.pop(name, None)

    async def create_container(self, spec: ContainerSpec) -> str:
        """Record and store `spec` as a created-but-not-started container.

        See `DockerClientPort.create_container`.
        """
        self.create_container_calls.append(spec)
        _fire(self, "_create_container_failure", f"container {spec.name!r}")
        self._containers[spec.name] = _TrackedContainer(
            spec=spec, status="created", created_at=datetime.now(UTC)
        )
        return spec.name

    async def start_container(self, name: str) -> None:
        """Mark `name` running; see `DockerClientPort.start_container`."""
        self.start_container_calls.append(name)
        _fire(self, "_start_container_failure", f"container {name!r}")
        tracked = self._containers.get(name)
        if tracked is not None:
            tracked.status = "running"

    async def remove_container(self, name: str, *, force: bool) -> None:
        """Drop `name` from this fake's table; see `DockerClientPort.remove_container`."""
        self.remove_container_calls.append(name)
        _fire(self, "_remove_container_failure", f"container {name!r}")
        self._containers.pop(name, None)

    async def list_containers(self, labels: Mapping[str, str]) -> Sequence[ContainerInfo]:
        """Return every tracked container whose labels carry all of `labels`.

        See `DockerClientPort.list_containers`.
        """
        return tuple(
            ContainerInfo(
                id=name,
                name=name,
                status=tracked.status,
                labels=dict(tracked.spec.labels),
                created_at=tracked.created_at,
            )
            for name, tracked in self._containers.items()
            if labels.items() <= tracked.spec.labels.items()
        )

    async def pause_container(self, name: str) -> None:
        """Mark `name` paused; see `DockerClientPort.pause_container`."""
        self.pause_container_calls.append(name)
        tracked = self._containers.get(name)
        if tracked is not None:
            tracked.status = "paused"

    async def unpause_container(self, name: str) -> None:
        """Mark `name` running; see `DockerClientPort.unpause_container`."""
        self.unpause_container_calls.append(name)
        tracked = self._containers.get(name)
        if tracked is not None:
            tracked.status = "running"


def _fire(client: FakeDockerClient, attr: str, subject: str) -> None:
    """Raise DockerClientError and clear the one-shot switch named `attr` if it is armed.

    A tiny shared helper so each create/start/remove method above stays a two-line "record, maybe
    fail" pair rather than repeating the same read-raise-clear dance six times.
    """
    reason = getattr(client, attr)
    if reason is not None:
        setattr(client, attr, None)
        raise DockerClientError(f"{subject}: {reason}")
