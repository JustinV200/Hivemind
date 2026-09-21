"""Wrap the `docker` SDK behind DockerClientPort: the only module allowed to import it.

`SdkDockerClient` is `DockerClientPort`'s real implementation, over `docker.DockerClient` (the
`docker` PyPI package, the Docker Engine SDK for Python). Codingrules section 8.6 confines a
vendor LLM SDK to `llm/providers/<name>/`; this module is `hive/backends/docker`'s own equivalent
for a vendor infrastructure SDK, and the root `pyproject.toml`'s import-linter contract enforces
it the same way. `docker` is an optional dependency (the `hivemind[docker]` extra,
`packages/hivemind/pyproject.toml`), so this module imports it lazily, inside `__init__`, never at
module scope -- importing `hivemind.hive.backends.docker` (the package) must succeed with the
extra absent (codingrules 5.5's sibling rule for an optional vendor dependency: nothing above this
one module may need `docker` installed just to import).

Every docker-py call is synchronous and blocking (it is a thin HTTP client over the daemon's Unix
socket or named pipe), so every method here runs its docker-py calls under `asyncio.to_thread`
(codingrules section 11) rather than on the event loop directly. Every docker-py exception is
caught and translated into `hivemind.hive.backends.docker.client.DockerClientError`: no SDK type
or exception ever reaches `hivemind.hive.backends.docker.backend.DockerCellBackend`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker`. Implements
    `hivemind.hive.backends.docker.client.DockerClientPort`; constructed by the composition root
    (a later phase's `cli/`) when `[hive] backend = "docker"`. Calls into the `docker` package
    only, plus `hivemind.common.errors` for the one configuration error a missing install raises.

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
    ContainerInfo,
    ContainerSpec,
    DockerClientError,
    NetworkSpec,
    VolumeSpec,
)

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
        """See `DockerClientPort.create_network`."""
        return await asyncio.to_thread(self._sync_create_network, spec)

    async def remove_network(self, name: str) -> None:
        """See `DockerClientPort.remove_network` (idempotent)."""
        await asyncio.to_thread(self._sync_remove_network, name)

    async def create_volume(self, spec: VolumeSpec) -> str:
        """See `DockerClientPort.create_volume`."""
        return await asyncio.to_thread(self._sync_create_volume, spec)

    async def remove_volume(self, name: str) -> None:
        """See `DockerClientPort.remove_volume` (idempotent)."""
        await asyncio.to_thread(self._sync_remove_volume, name)

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

    def _sync_create_network(self, spec: NetworkSpec) -> str:
        """Blocking half of create_network: runs on a worker thread, never the event loop."""
        try:
            network = self._client.networks.create(
                name=spec.name, driver="bridge", internal=spec.internal, labels=dict(spec.labels)
            )
        except self._docker.errors.APIError as exc:
            raise DockerClientError(f"could not create network {spec.name!r}: {exc}") from exc
        return str(network.id)

    def _sync_remove_network(self, name: str) -> None:
        """Blocking half of remove_network: a missing network is success, not failure."""
        try:
            self._client.networks.get(name).remove()
        except self._docker.errors.NotFound:
            return  # Idempotent: nothing to remove, matching DockerClientPort's own contract.
        except self._docker.errors.APIError as exc:
            raise DockerClientError(f"could not remove network {name!r}: {exc}") from exc

    def _sync_create_volume(self, spec: VolumeSpec) -> str:
        """Blocking half of create_volume: runs on a worker thread, never the event loop."""
        try:
            volume = self._client.volumes.create(name=spec.name, labels=dict(spec.labels))
        except self._docker.errors.APIError as exc:
            raise DockerClientError(f"could not create volume {spec.name!r}: {exc}") from exc
        return str(volume.name)

    def _sync_remove_volume(self, name: str) -> None:
        """Blocking half of remove_volume: a missing volume is success, not failure."""
        try:
            self._client.volumes.get(name).remove()
        except self._docker.errors.NotFound:
            return  # Idempotent: nothing to remove, matching DockerClientPort's own contract.
        except self._docker.errors.APIError as exc:
            raise DockerClientError(f"could not remove volume {name!r}: {exc}") from exc

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
                security_opt=list(spec.security_opt),
                read_only=spec.read_only_rootfs,
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
            )
            for container in containers
        )


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
