"""Define DockerClientPort: the narrow slice of the Docker API DockerCellBackend actually needs.

`DockerCellBackend` (`hivemind.hive.backends.docker.backend`) never talks to the `docker` SDK
directly, and never sees an SDK type: it calls this Protocol instead, which two modules implement
-- `hivemind.hive.backends.docker.sdk_client.SdkDockerClient` (the real thing, the only module
that may `import docker`) and `hivemind.hive.backends.docker.fake.FakeDockerClient` (in-memory, for
tests and `hive doctor`). Keeping the Protocol narrow (create/start/remove a container, create/
remove a network, attach or detach one, create/remove a volume, list containers by label,
pause/unpause, commit a container to an image and recreate one from an image) rather than wrapping
the whole Docker SDK
means a fake can implement it honestly in a few hundred lines, and it documents exactly what this
backend relies on Docker for: nothing here does image builds, `docker exec` or log streaming.

Roadmap step 5.10 adds `commit_container`/`remove_image`/`recreate_from_image`: the narrow slice
`hivemind.hive.snapshot.docker.DockerSnapshotter` needs for Capping's whole-Cell rollback
(`hivemind.cell.snapshot.Snapshotter`), reusing this same Protocol and its two implementations
rather than opening a second door to Docker. `list_images` finds a Cell's snapshot images by the
label every commit stamps on them, so a Night Veil Cell's can be removed at its teardown
(codingrules section 12, `hivemind.hive.snapshot.docker.DockerSnapshotImages`); and
`ContainerSpec.log_driver` lets a Night Veil Cell run with no daemon log to outlive it. These image
calls form `_ImagesPort`, one of the two bases `DockerClientPort` extends.

Roadmap step 10.6a adds the network slice isolation needs, as `DockerNetworkPort`, the other base
`DockerClientPort` extends (it holds the two network calls that were already here, beside the
three new ones, so each Protocol stays within the class limit): `ensure_network` (the per-Hive
control network every Cell's Waggle link rides, created once and then reused), and
`connect_network`/`disconnect_network`, the runtime lever that attaches a running container to
its egress network or takes it off again (`hivemind.hive.backends.docker.egress`).
`ContainerInfo.networks` says which networks a container is on now, so that lever is idempotent.

The value types below (`ContainerSpec`, `ContainerInfo`, `NetworkSpec`, `VolumeSpec`,
`CommitResult`) are this Protocol's own request/response shapes: frozen dataclasses (codingrules
8.5), not pydantic models, because they never cross a process, network or file boundary. They are
how `hivemind.hive.backends.docker.backend` talks to whichever `DockerClientPort` it was given,
all inside one Python process, mirroring `hivemind.hive.backends.base.VirtualCellRecord`'s own
choice of dataclass over pydantic model for the same reason.

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
    - `connect_network`/`disconnect_network` are idempotent too: attaching an attached container,
      or detaching a detached one, returns normally, so a repeated cut or restore changes nothing.
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
    "CommitResult",
    "ContainerInfo",
    "ContainerSpec",
    "DockerClientError",
    "DockerClientPort",
    "DockerNetworkPort",
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
        cap_add: Linux capabilities to add back, on top of `cap_drop`; empty for every Cell but
            one on `hivemind.hive.NetworkPolicy.VPN_TOR`, which needs `("NET_ADMIN",)` to manage
            its own tunnel interface and nftables kill-switch (roadmap step 5.7a,
            `hivemind.hive.backends.docker.backend`'s own docstring names the exact rule).
        security_opt: Docker `--security-opt` values; `("no-new-privileges:true",)` by default.
        read_only_rootfs: Whether the container's root filesystem is read-only (the scratch volume
            and any tmpfs mounts stay writable regardless).
        tmpfs: Extra in-memory, writable mount points for a read-only root (`/tmp`, typically).
        log_driver: The container's log driver, or None for the daemon's own default: `"none"`
            for a Night Veil Cell, whose output must reach no daemon log that outlives it
            (codingrules section 12).
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
    cap_add: tuple[str, ...] = ()
    log_driver: str | None = None


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    """One container as `list_containers` reports it: enough for `list_cells` to build a record.

    Attributes:
        id: Docker's own container id (a hash), never `DockerCellBackend`'s own chosen name.
        name: The container's name, e.g. `"hivemind-cell-cell_01H..."`.
        status: Docker's own status string (`"running"`, `"paused"`, `"exited"`, ...).
        labels: Every label on the container.
        created_at: When Docker created it.
        networks: The name of every network the container is attached to right now (roadmap step
            10.6a: an isolated Cell is on its control network alone).
    """

    id: str
    name: str
    status: str
    labels: Mapping[str, str]
    created_at: datetime
    networks: tuple[str, ...] = ()


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
        subnet: The IPv4 subnet the daemon must give the network, in CIDR form, or None to let it
            choose one (every per-Cell network). The control network fixes it, so the Queen's
            listener can bind its gateway before any Cell exists.
        gateway: The host's own address on the network (the bridge's), or None to let the daemon
            choose; set together with `subnet`.
        isolates_peers: True turns inter-container traffic on the bridge off, so the containers on
            it cannot reach one another, only the host (the per-Hive control network).
    """

    name: str
    internal: bool
    labels: Mapping[str, str]
    subnet: str | None = None
    gateway: str | None = None
    isolates_peers: bool = False


@dataclass(frozen=True, slots=True)
class VolumeSpec:
    """One per-Cell scratch volume to create before its container.

    Attributes:
        name: The volume's name, chosen the same deterministic way as `ContainerSpec.name`.
        labels: Labels to stamp on the volume.
    """

    name: str
    labels: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CommitResult:
    """What `commit_container` hands back: the committed image's own ref and its reported size.

    Attributes:
        image: The image reference `recreate_from_image` can later boot a fresh container from
            (`f"{repository}:{tag}"` for the real client; a fake's own made-up equivalent).
        size_bytes: The committed image's own reported size, for
            `hivemind.hive.snapshot.ledger.SnapshotLedger`'s disk-Forage accounting. Best-effort:
            a Docker layer's reported size is an estimate of the new layer's own bytes, not a
            precise disk delta.
    """

    image: str
    size_bytes: int


class DockerNetworkPort(Protocol):
    """The network slice of `DockerClientPort`: create, reuse, remove, attach and detach.

    A base Protocol of its own (module docstring) so `DockerClientPort` stays within the class
    limit; every `DockerClientPort` implementation implements these too.
    """

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

    async def ensure_network(self, spec: NetworkSpec) -> str:
        """Create `spec` unless a network of its name exists; reuse that one when it matches.

        Args:
            spec: The network's name, internal flag, subnet, gateway and labels.

        Returns:
            Docker's own network id, the existing network's or the new one's.

        Raises:
            DockerClientError: The daemon refused or failed to create it, or a network of that
                name exists with a different internal flag, subnet or gateway: a Cell is never
                attached to a network that does not enforce what `spec` says.
        """
        ...

    async def connect_network(self, network: str, container: str) -> None:
        """Attach `container` to `network`. Idempotent: an attached container returns normally.

        Args:
            network: The network's name.
            container: The container's name.

        Raises:
            DockerClientError: Either does not exist, or the daemon refused the attachment.
        """
        ...

    async def disconnect_network(self, network: str, container: str) -> None:
        """Detach `container` from `network`. Idempotent: a detached one returns normally.

        Args:
            network: The network's name.
            container: The container's name.

        Raises:
            DockerClientError: The container does not exist, or the daemon refused.
        """
        ...


class _ImagesPort(Protocol):
    """The image half of DockerClientPort (snapshots), split out for codingrules 5.1's class size.

    Roadmap step 5.10's commit and rollback, and the Night Veil teardown's image removal; every
    DockerClientPort implementation implements both halves, and callers only ever name the whole.
    """

    async def commit_container(
        self, name: str, *, repository: str, tag: str, labels: Mapping[str, str]
    ) -> CommitResult:
        """Commit `name`'s current root filesystem and image config to a new image.

        Roadmap step 5.10: the operation behind `hivemind.hive.snapshot.docker.DockerSnapshotter.
        snapshot`. A commit captures the container's root filesystem layer and its image
        metadata (env, cmd, entrypoint, the `labels` given here) exactly as they stand right now;
        it does NOT capture a mounted volume (the scratch volume is unaffected) or any in-flight
        process state (no process checkpoint -- a container recreated from the result starts
        fresh at the image's own entrypoint, the same way starting any other container does).

        Args:
            name: The running (or stopped) container to commit.
            repository: The committed image's own repository name.
            tag: The committed image's own tag; unique per commit so `remove_image` can target
                exactly this one later.
            labels: Labels to stamp on the committed image's own config, for audit.

        Returns:
            The committed image's ref and its reported size.

        Raises:
            DockerClientError: `name` does not exist, or the daemon refused or failed to commit.
        """
        ...

    async def remove_image(self, image: str) -> None:
        """Remove an image. Idempotent: an image named `image` that does not exist returns normally.

        Args:
            image: The image ref (`CommitResult.image`) to remove.

        Raises:
            DockerClientError: The daemon acknowledged the image exists but refused to remove it
                (e.g. a container still references it).
        """
        ...

    async def list_images(self, labels: Mapping[str, str]) -> Sequence[str]:
        """Return every image carrying every one of `labels`, as refs `remove_image` accepts.

        Args:
            labels: Labels an image must carry, each with this exact value.

        Returns:
            The matching images' refs, in no particular order; empty when none match.

        Raises:
            DockerClientError: The daemon refused or failed to list images.
        """
        ...

    async def recreate_from_image(self, name: str, image: str) -> None:
        """Stop and remove the container named `name`, then recreate it, booted from `image`.

        Roadmap step 5.10: the operation behind `hivemind.hive.snapshot.docker.DockerSnapshotter.
        rollback`. The new container keeps the old one's own name, network attachment, volume
        mounts and resource limits (read back from the container being replaced, not from any
        `ContainerSpec` this Protocol's caller may or may not still hold) but boots from `image`
        instead of whatever it was running before -- undoing anything a proposal changed on the
        root filesystem or in the image's own config, while leaving the mounted scratch volume
        (never part of an image) exactly as it stood.

        Args:
            name: The container to replace; must currently exist.
            image: The image (typically a prior `commit_container` result) to recreate it from.

        Raises:
            DockerClientError: `name` does not exist, or the daemon refused or failed to recreate
                it from `image`.
        """
        ...


class DockerClientPort(DockerNetworkPort, _ImagesPort, Protocol):
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
