"""Implement DockerClientPort over the `docker` SDK: the one module that imports it.

`SdkDockerClient` is `DockerClientPort`'s real implementation, over `docker.DockerClient`.
`docker` is an optional dependency (the `hivemind[docker]` extra, `packages/hivemind/
pyproject.toml`), so this module imports it lazily, inside `__init__`, never at module scope --
importing `hivemind.hive.backends.docker` (the package) must succeed with the extra absent
(codingrules 5.5's sibling rule for an optional vendor dependency: nothing above this one module
may need `docker` installed just to import).

Every docker-py call is synchronous and blocking (it is a thin HTTP client over the daemon's Unix
socket or named pipe), so every method here runs its docker-py calls under `asyncio.to_thread`
(codingrules section 11) rather than on the event loop directly: the container calls' blocking
halves are this class's own `_sync_*` methods, and the network, volume and image calls' live in
the `networks`, `volumes` and `images` siblings. Roadmap step 10.6a adds the network slice
isolation needs (`ensure_network`, `connect_network`, `disconnect_network`, and the networks
`list_containers` reports per container). A Night Veil Cell's container is created with the log
driver its spec names (`none`), so no daemon log of it outlives the Cell (codingrules section 12).
Every docker-py exception is caught and translated into `hivemind.hive.backends.docker.client.
DockerClientError`: no SDK type or exception ever reaches `hivemind.hive.backends.docker.backend.
DockerCellBackend`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker.sdk_client`. Implements
    `hivemind.hive.backends.docker.client.DockerClientPort`; constructed by the composition root
    (`hivemind.cli.compose.virtual_cell_backends`) when `[virtual_cells] backend = "docker"`.
    Calls into the `docker` package, its `handle`, `networks`, `volumes` and `images` siblings,
    and `hivemind.common.errors` for the one configuration error a missing install raises.

Key invariants:
    - `import docker` appears exactly once in this module, inside `__init__`, never at module
      scope: `from hivemind.hive.backends.docker import SdkDockerClient` must succeed even when
      the `docker` package is not installed, and only fail once someone tries to construct one.
    - `remove_container`/`remove_network`/`remove_volume` never raise for a resource that does not
      exist (docker-py's own `docker.errors.NotFound`): `DockerClientPort`'s own idempotency
      invariant.
    - No `docker.*` type or exception is ever part of this module's own public signature or ever
      escapes a method uncaught.

See Also:
    - .claude/codingrules.md section 8.6 for the vendor-SDK confinement pattern this module
      mirrors for infrastructure instead of LLM providers.
    - hivemind.hive.backends.docker.client for DockerClientPort, ContainerSpec, ContainerInfo,
      NetworkSpec, VolumeSpec and DockerClientError, everything this class implements and raises.
    - hivemind.hive.backends.docker.fake for FakeDockerClient, the in-memory implementation tests
      use instead of this one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from hivemind.common.errors import ConfigurationError
from hivemind.hive.backends.docker.client import (
    CommitResult,
    ContainerInfo,
    ContainerSpec,
    DockerClientError,
    NetworkSpec,
    VolumeSpec,
)
from hivemind.hive.backends.docker.sdk_client.handle import DockerHandle
from hivemind.hive.backends.docker.sdk_client.images import (
    sync_commit_container,
    sync_list_images,
    sync_recreate_from_image,
    sync_remove_image,
)
from hivemind.hive.backends.docker.sdk_client.networks import (
    attached_networks,
    sync_attach,
    sync_create_network,
    sync_ensure_network,
    sync_remove_network,
)
from hivemind.hive.backends.docker.sdk_client.volumes import sync_create_volume, sync_remove_volume

if TYPE_CHECKING:
    # Only for type checkers: this import never runs (mypy never executes module bodies), so it
    # never breaks importing this module without the "docker" extra installed. __init__ below is
    # the one place the real, runtime import happens.
    from docker import DockerClient

__all__ = ["SdkDockerClient"]

_INSTALL_HINT = (
    "The 'docker' package is not installed; install the 'hivemind[docker]' extra "
    "(e.g. `uv sync --extra docker`) to use SdkDockerClient."
)


class SdkDockerClient:
    """The real DockerClientPort, over the `docker` SDK's `DockerClient`."""

    def __init__(self, client: DockerClient | None = None) -> None:
        """Connect to the Docker daemon `docker.from_env()` finds, or use a given `client`.

        Args:
            client: An already-constructed `docker.DockerClient`, for a caller that needs a
                non-default connection (a remote daemon, a custom timeout). `docker.from_env()`
                otherwise -- docker-py's own "read DOCKER_HOST and the rest of the standard Docker
                environment variables" default.

        Raises:
            ConfigurationError: The `docker` package is not installed.
        """
        # SAFETY: this is the one legal `import docker` in the whole workspace (root
        # pyproject.toml's import-linter contract confines it to this module); every other module
        # in this package sees only DockerClientPort's own types.
        try:
            import docker
        except ImportError as exc:
            raise ConfigurationError(_INSTALL_HINT) from exc
        self._docker = docker
        self._client: Any = client if client is not None else docker.from_env()

    async def create_network(self, spec: NetworkSpec) -> str:
        """See `DockerNetworkPort.create_network`."""
        return await asyncio.to_thread(sync_create_network, self._handle(), spec)

    async def ensure_network(self, spec: NetworkSpec) -> str:
        """See `DockerNetworkPort.ensure_network` (roadmap step 10.6a)."""
        return await asyncio.to_thread(sync_ensure_network, self._handle(), spec)

    async def remove_network(self, name: str) -> None:
        """See `DockerNetworkPort.remove_network` (idempotent)."""
        await asyncio.to_thread(sync_remove_network, self._handle(), name)

    async def connect_network(self, network: str, container: str) -> None:
        """See `DockerNetworkPort.connect_network` (idempotent; roadmap step 10.6a)."""
        await asyncio.to_thread(sync_attach, self._handle(), network, container, True)

    async def disconnect_network(self, network: str, container: str) -> None:
        """See `DockerNetworkPort.disconnect_network` (idempotent; roadmap step 10.6a)."""
        await asyncio.to_thread(sync_attach, self._handle(), network, container, False)

    async def create_volume(self, spec: VolumeSpec) -> str:
        """See `DockerClientPort.create_volume`."""
        return await asyncio.to_thread(sync_create_volume, self._handle(), spec)

    async def remove_volume(self, name: str) -> None:
        """See `DockerClientPort.remove_volume` (idempotent)."""
        await asyncio.to_thread(sync_remove_volume, self._handle(), name)

    async def create_container(self, spec: ContainerSpec) -> str:
        """See `DockerClientPort.create_container`."""
        return await asyncio.to_thread(self._sync_create_container, spec)

    async def start_container(self, name: str) -> None:
        """See `DockerClientPort.start_container`."""
        await asyncio.to_thread(self._sync_container_action, name, "start")

    async def remove_container(self, name: str, *, force: bool) -> None:
        """See `DockerClientPort.remove_container` (idempotent)."""
        await asyncio.to_thread(self._sync_remove_container, name, force)

    async def list_containers(self, labels: Mapping[str, str]) -> Sequence[ContainerInfo]:
        """See `DockerClientPort.list_containers`."""
        return await asyncio.to_thread(self._sync_list_containers, labels)

    async def pause_container(self, name: str) -> None:
        """See `DockerClientPort.pause_container`."""
        await asyncio.to_thread(self._sync_container_action, name, "pause")

    async def unpause_container(self, name: str) -> None:
        """See `DockerClientPort.unpause_container`."""
        await asyncio.to_thread(self._sync_container_action, name, "unpause")

    async def commit_container(
        self, name: str, *, repository: str, tag: str, labels: Mapping[str, str]
    ) -> CommitResult:
        """See `DockerClientPort.commit_container` (roadmap step 5.10).

        The blocking half lives in the `images` sibling, not in a `_sync_*` method (codingrules
        section 5.1's class-size limit: this class was already at its own 200-line ceiling before
        roadmap step 5.10 added three more methods for it).
        """
        return await asyncio.to_thread(
            sync_commit_container, self._handle(), name, repository, tag, labels
        )

    async def remove_image(self, image: str) -> None:
        """See `DockerClientPort.remove_image` (idempotent; roadmap step 5.10)."""
        await asyncio.to_thread(sync_remove_image, self._handle(), image)

    async def list_images(self, labels: Mapping[str, str]) -> Sequence[str]:
        """See `DockerClientPort.list_images` (a Night Veil Cell's snapshot images)."""
        return await asyncio.to_thread(sync_list_images, self._handle(), labels)

    async def recreate_from_image(self, name: str, image: str) -> None:
        """See `DockerClientPort.recreate_from_image` (roadmap step 5.10)."""
        await asyncio.to_thread(sync_recreate_from_image, self._handle(), name, image)

    def _sync_create_container(self, spec: ContainerSpec) -> str:
        """Blocking half of create_container: maps ContainerSpec onto docker-py's own kwargs."""
        volumes = (
            {spec.volume_name: {"bind": spec.volume_mount_path, "mode": "rw"}}
            if spec.volume_name is not None
            else {}
        )
        try:
            container = self._client.containers.create(
                image=spec.image,
                name=spec.name,
                environment=dict(spec.environment),
                labels=dict(spec.labels),
                network=spec.network_name,
                extra_hosts=dict(spec.extra_hosts),
                volumes=volumes,
                tmpfs=dict(spec.tmpfs),
                nano_cpus=spec.nano_cpus,
                mem_limit=spec.mem_limit_bytes,
                pids_limit=spec.pids_limit,
                cap_drop=list(spec.cap_drop),
                cap_add=list(spec.cap_add),
                security_opt=list(spec.security_opt),
                read_only=spec.read_only_rootfs,
                log_config=_log_config(spec.log_driver),
                # No `ports`/`publish_all_ports`: a Virtual Cell publishes no inbound port under
                # any network policy (codingrules section 15); the image's own USER (images/
                # base-ubuntu's Dockerfile) supplies the non-root user, so `user` stays unset here
                # rather than fighting a future image that runs as a different non-root name.
            )
        except self._docker.errors.ImageNotFound as exc:
            raise DockerClientError(f"image {spec.image!r} not found: {exc}") from exc
        except self._docker.errors.APIError as exc:
            raise DockerClientError(f"could not create container {spec.name!r}: {exc}") from exc
        return str(container.id)

    def _sync_container_action(self, name: str, action: str) -> None:
        """Blocking half of start/pause/unpause: look the container up, then call `action` on it."""
        try:
            container = self._client.containers.get(name)
            getattr(container, action)()
        except self._docker.errors.NotFound as exc:
            raise DockerClientError(f"container {name!r} not found: {exc}") from exc
        except self._docker.errors.APIError as exc:
            raise DockerClientError(f"could not {action} container {name!r}: {exc}") from exc

    def _sync_remove_container(self, name: str, force: bool) -> None:
        """Blocking half of remove_container: a missing container is success, not failure."""
        try:
            self._client.containers.get(name).remove(force=force)
        except self._docker.errors.NotFound:
            return  # Idempotent: nothing to remove, matching DockerClientPort's own contract.
        except self._docker.errors.APIError as exc:
            raise DockerClientError(f"could not remove container {name!r}: {exc}") from exc

    def _sync_list_containers(self, labels: Mapping[str, str]) -> Sequence[ContainerInfo]:
        """Blocking half of list_containers: filters by label, includes every status."""
        filters = {"label": [f"{key}={value}" for key, value in labels.items()]}
        try:
            containers = self._client.containers.list(all=True, filters=filters)
        except self._docker.errors.APIError as exc:
            raise DockerClientError(f"could not list containers: {exc}") from exc
        return tuple(
            ContainerInfo(
                id=str(container.id),
                name=str(container.name),
                status=str(container.status),
                labels=dict(container.labels),
                created_at=_parse_created_at(container.attrs.get("Created")),
                networks=attached_networks(container.attrs),
            )
            for container in containers
        )

    def _handle(self) -> DockerHandle:
        """Bundle the SDK client and module for a blocking half in a sibling module."""
        return DockerHandle(self._client, self._docker)


def _log_config(driver: str | None) -> dict[str, str] | None:
    """Return docker-py's `log_config` for `driver`, or None to keep the daemon's own default.

    docker-py turns the plain mapping into its own `LogConfig`, so no SDK type is built here.
    """
    return None if driver is None else {"type": driver}


def _parse_created_at(raw: str | None) -> datetime:
    """Parse docker-py's own "Created" timestamp (ISO-8601, nanosecond precision) into UTC.

    Falls back to "now" for a missing or unparseable value rather than raising: `created_at` is
    informational (`VirtualCellRecord`'s own docstring), never something a caller's correctness
    depends on, so a malformed daemon response should not turn `list_cells` into a hard failure.
    """
    if raw is None:
        return datetime.now(UTC)
    # Docker reports nanosecond-precision fractional seconds; datetime.fromisoformat accepts at
    # most 6 fractional digits (microseconds), so trim rather than assume a fixed string length.
    if "." in raw:
        whole, _, fraction_and_zone = raw.partition(".")
        zone = "+00:00" if fraction_and_zone.endswith("Z") else ""
        digits = fraction_and_zone.rstrip("Z")
        raw = f"{whole}.{digits[:6]}{zone}"
    elif raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(UTC)
