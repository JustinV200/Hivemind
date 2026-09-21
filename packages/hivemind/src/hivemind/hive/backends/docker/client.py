"""Define DockerClientPort: the narrow slice of the Docker API DockerCellBackend actually needs.

`DockerCellBackend` (`hivemind.hive.backends.docker.backend`) never talks to the `docker` SDK
directly, and never sees an SDK type: it calls this Protocol instead, which two modules implement
-- `hivemind.hive.backends.docker.sdk_client.SdkDockerClient` (the real thing, the only module
that may `import docker`) and `hivemind.hive.backends.docker.fake.FakeDockerClient` (in-memory, for
tests and `hive doctor`). Keeping the Protocol narrow (create/start/remove a container, create/
remove a network, create/remove a volume, list containers by label, pause/unpause) rather than
wrapping the whole Docker SDK means a fake can implement it honestly in a few hundred lines, and it
documents exactly what this backend relies on Docker for: nothing here does image builds,
`docker exec`, log streaming or `commit` (snapshotting is a later roadmap step, 5.10, and reuses a
different seam, `hivemind.cell.snapshot.Snapshotter`).

The value types below (`ContainerSpec`, `ContainerInfo`, `NetworkSpec`, `VolumeSpec`) are this
Protocol's own request/response shapes: frozen dataclasses (codingrules 8.5), not pydantic models,
because they never cross a process, network or file boundary -- they are how
`hivemind.hive.backends.docker.backend` talks to whichever `DockerClientPort` it was given, all
inside one Python process, mirroring `hivemind.hive.backends.base.VirtualCellRecord`'s own choice
of dataclass over pydantic model for the same reason.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker`. Called by
    `hivemind.hive.backends.docker.backend.DockerCellBackend`; implemented by
    `hivemind.hive.backends.docker.sdk_client.SdkDockerClient` and
    `hivemind.hive.backends.docker.fake.FakeDockerClient`. Calls into nothing of its own: a plain
    Protocol and its value types carry no logic.

Key invariants:
    - No SDK type (from the vendor `docker` package) appears in this module's signatures: every
      return value is a plain `str` (a name or id `DockerCellBackend` chose or Docker assigned) or
      one of this module's own dataclasses.
    - Every `remove_*` method is idempotent: removing a container, network or volume that does not
      exist returns normally, never raises `DockerClientError` -- `DockerCellBackend.destroy`'s own
      idempotency (codingrules Appendix A.1) depends on that, and both implementations honour it.
    - `DockerClientError` is the only exception any method raises for an operation that genuinely
      failed; `hivemind.hive.backends.docker.backend` is what translates it into
      `hivemind.hive.errors.CellProvisionError`/`CellDestroyError`, so this Protocol itself stays
      independent of the `hive` package's own error tree.

See Also:
    - hivemind.hive.backends.docker.sdk_client for the real implementation.
    - hivemind.hive.backends.docker.fake for the in-memory implementation.
    - hivemind.hive.backends.docker.backend for DockerCellBackend, this Protocol's one caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

__all__ = [
    "ContainerInfo",
    "ContainerSpec",
    "DockerClientError",
    "DockerClientPort",
    "NetworkSpec",
    "VolumeSpec",
]


class DockerClientError(Exception):
    """Raised by a DockerClientPort implementation when a Docker operation genuinely fails.

    Never raised for "the thing I was asked to remove does not exist" (see the module docstring's
    idempotency invariant); always raised with a full sentence naming what failed, so
    `hivemind.hive.backends.docker.backend` can fold it straight into a typed `hive` error message
    without needing to inspect a wrapped SDK exception.
    """


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    """Everything needed to create one container; DockerCellBackend's own resource-limit mapping.

    Attributes:
        name: The container's name, chosen by DockerCellBackend so `destroy()` can recompute it
            from a `CellId` alone with no other state (codingrules Appendix A.1: idempotent).
        image: The image to run, e.g. `"hivemind/base-ubuntu:dev"`.
        environment: The Cell's own `HIVEMIND_*` variables (`CellBootstrap.environment()`).
        labels: Every label to stamp on the container (`hivemind.hive_id`, `hivemind.cell_id`, a
            caller's own `spec.labels`, ...).
        network_name: The network to attach at creation; DockerCellBackend always creates this
            network first, so a container is never created without one.
        network_aliases: Extra hostname aliases resolved through the daemon's embedded DNS on
            `network_name` for a real deployment; empty for one, since a Cell never needs to be
            reachable by name (it is never dialled into, ADR-0027).
        extra_hosts: Static `/etc/hosts` entries, e.g. `{"host.docker.internal": "host-gateway"}`
            (`hivemind.hive.backends.docker.network`'s own host-gateway helper).
        volume_name: The scratch volume to mount, or None for a Cell that needs none.
        volume_mount_path: Where `volume_name` is mounted inside the container.
        nano_cpus: CPU limit, Docker's own unit (billionths of a CPU); `int(cpu_cores * 1e9)`.
        mem_limit_bytes: Memory limit in bytes.
        pids_limit: Maximum process count; bounds a fork bomb inside the Cell.
        cap_drop: Linux capabilities to drop; `("ALL",)` for the least-privilege default.
        security_opt: Docker `--security-opt` values; `("no-new-privileges:true",)` by default.
        read_only_rootfs: Whether the container's root filesystem is read-only (the scratch volume
            and any tmpfs mounts stay writable regardless).
        tmpfs: Extra in-memory, writable mount points for a read-only root (`/tmp`, typically).
    """

    name: str
    image: str
    environment: Mapping[str, str]
    labels: Mapping[str, str]
    network_name: str
    extra_hosts: Mapping[str, str]
    volume_name: str | None
    volume_mount_path: str
    nano_cpus: int
    mem_limit_bytes: int
    pids_limit: int
    cap_drop: tuple[str, ...]
    security_opt: tuple[str, ...]
    read_only_rootfs: bool
    tmpfs: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    """One container as `list_containers` reports it: enough for `list_cells` to build a record.

    Attributes:
        id: Docker's own container id (a hash), never `DockerCellBackend`'s own chosen name.
        name: The container's name, e.g. `"hivemind-cell-cell_01H..."`.
        status: Docker's own status string (`"running"`, `"paused"`, `"exited"`, ...).
        labels: Every label on the container.
        created_at: When Docker created it.
    """

    id: str
    name: str
    status: str
    labels: Mapping[str, str]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NetworkSpec:
    """One per-Cell Docker network to create before its container.

    Attributes:
        name: The network's name, chosen the same deterministic way as `ContainerSpec.name`.
        internal: True withholds the outbound NAT rule Docker would otherwise install, so the
            network has no route to anything beyond itself and the host
            (`hivemind.hive.backends.docker.network`'s own module docstring documents exactly what
            this does and does not block, including the Docker Desktop caveat).
        labels: Labels to stamp on the network, for `list_cells`-independent cleanup by an
            operator's own `docker network ls --filter label=...`.
    """

    name: str
    internal: bool
    labels: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class VolumeSpec:
    """One per-Cell scratch volume to create before its container.

    Attributes:
        name: The volume's name, chosen the same deterministic way as `ContainerSpec.name`.
        labels: Labels to stamp on the volume.
    """

    name: str
    labels: Mapping[str, str]


class DockerClientPort(Protocol):
    """The slice of the Docker API DockerCellBackend needs, with no SDK type in sight.

    Implementations must be safe to call concurrently: `DockerCellBackend.provision` may run
    several times at once (the Queen may provision several Cells during Swarming).
    """

    async def create_container(self, spec: ContainerSpec) -> str:
        """Create (but do not start) a container from `spec`.

        Args:
            spec: Everything the container needs.

        Returns:
            Docker's own container id.

        Raises:
            DockerClientError: The daemon refused or failed to create the container.
        """
        ...

    async def start_container(self, name: str) -> None:
        """Start a container previously created with `create_container`.

        Args:
            name: The container's name (`ContainerSpec.name`).

        Raises:
            DockerClientError: The daemon refused or failed to start it.
        """
        ...

    async def remove_container(self, name: str, *, force: bool) -> None:
        """Remove a container, stopping it first if `force` is True.

        Idempotent: a container named `name` that does not exist returns normally (see the module
        docstring's idempotency invariant).

        Args:
            name: The container's name.
            force: Whether to stop a running container before removing it.

        Raises:
            DockerClientError: The daemon acknowledged the container exists but refused to
                remove it.
        """
        ...

    async def list_containers(self, labels: Mapping[str, str]) -> Sequence[ContainerInfo]:
        """Return every container carrying all of `labels`, whatever its status.

        Args:
            labels: Every label a container must carry, e.g. `{"hivemind.hive_id": hive_id}`.

        Returns:
            One ContainerInfo per matching container that exists right now.

        Raises:
            DockerClientError: The daemon could not be reached or refused the query.
        """
        ...

    async def pause_container(self, name: str) -> None:
        """Pause a running container.

        Args:
            name: The container's name.

        Raises:
            DockerClientError: The daemon refused or failed to pause it.
        """
        ...

    async def unpause_container(self, name: str) -> None:
        """Resume a paused container.

        Args:
            name: The container's name.

        Raises:
            DockerClientError: The daemon refused or failed to unpause it.
        """
        ...

    async def create_network(self, spec: NetworkSpec) -> str:
        """Create a bridge network from `spec`.

        Args:
            spec: The network's name, internal flag and labels.

        Returns:
            Docker's own network id.

        Raises:
            DockerClientError: The daemon refused or failed to create it.
        """
        ...

    async def remove_network(self, name: str) -> None:
        """Remove a network by name. Idempotent: a missing `name` returns normally, not an error.

        Args:
            name: The network's name.

        Raises:
            DockerClientError: The daemon acknowledged the network exists but refused to remove it
                (e.g. a container is still attached).
        """
        ...

    async def create_volume(self, spec: VolumeSpec) -> str:
        """Create a named volume from `spec`.

        Args:
            spec: The volume's name and labels.

        Returns:
            The volume's own name (Docker volumes are named, not id-assigned).

        Raises:
            DockerClientError: The daemon refused or failed to create it.
        """
        ...

    async def remove_volume(self, name: str) -> None:
        """Remove a volume. Idempotent: a volume named `name` that does not exist returns normally.

        Args:
            name: The volume's name.

        Raises:
            DockerClientError: The daemon acknowledged the volume exists but refused to remove it.
        """
        ...
