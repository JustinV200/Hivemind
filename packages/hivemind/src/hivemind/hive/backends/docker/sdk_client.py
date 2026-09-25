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
(codingrules section 11) rather than on the event loop directly. Roadmap step 10.6a adds the
network slice isolation needs (`ensure_network`, `connect_network`, `disconnect_network`, and the
networks `list_containers` reports per container) and keeps every network a container was on
across a snapshot rollback's recreate, so a dual-homed Cell comes back dual-homed. Every
docker-py exception is caught and translated into `hivemind.hive.backends.docker.client.
DockerClientError`: no SDK type or exception ever reaches `hivemind.hive.backends.docker.backend.
DockerCellBackend`.

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
from dataclasses import dataclass
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
# The bridge driver's inter-container switch: "false" keeps containers on one bridge apart.
_ICC_OPTION = "com.docker.network.bridge.enable_icc"


@dataclass(frozen=True, slots=True)
class _DockerHandle:
    """Bundles `SdkDockerClient._client`/`._docker`, so a module function takes this one argument.

    Codingrules section 5.1's five-parameter limit: the module-level blocking halves below (the
    network and volume calls, roadmap step 5.10's commit, image removal and recreate, and the
    Night Veil teardown's image listing) take this instead of the two separately, and live
    outside the class for its 200-line limit.
    """

    client: Any
    docker_module: Any


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
        return await asyncio.to_thread(_sync_create_network, self._handle(), spec)

    async def ensure_network(self, spec: NetworkSpec) -> str:
        """See `DockerNetworkPort.ensure_network` (roadmap step 10.6a)."""
        return await asyncio.to_thread(_sync_ensure_network, self._handle(), spec)

    async def remove_network(self, name: str) -> None:
        """See `DockerNetworkPort.remove_network` (idempotent)."""
        await asyncio.to_thread(_sync_remove_network, self._handle(), name)

    async def connect_network(self, network: str, container: str) -> None:
        """See `DockerNetworkPort.connect_network` (idempotent; roadmap step 10.6a)."""
        await asyncio.to_thread(_sync_attach, self._handle(), network, container, True)

    async def disconnect_network(self, network: str, container: str) -> None:
        """See `DockerNetworkPort.disconnect_network` (idempotent; roadmap step 10.6a)."""
        await asyncio.to_thread(_sync_attach, self._handle(), network, container, False)

    async def create_volume(self, spec: VolumeSpec) -> str:
        """See `DockerClientPort.create_volume`."""
        return await asyncio.to_thread(_sync_create_volume, self._handle(), spec)

    async def remove_volume(self, name: str) -> None:
        """See `DockerClientPort.remove_volume` (idempotent)."""
        await asyncio.to_thread(_sync_remove_volume, self._handle(), name)

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

        The blocking half lives as a module-level function, not a `_sync_*` method (codingrules
        section 5.1's class-size limit: this class was already at its own 200-line ceiling before
        roadmap step 5.10 added three more methods for it).
        """
        return await asyncio.to_thread(
            _sync_commit_container, self._handle(), name, repository, tag, labels
        )

    async def remove_image(self, image: str) -> None:
        """See `DockerClientPort.remove_image` (idempotent; roadmap step 5.10)."""
        await asyncio.to_thread(_sync_remove_image, self._handle(), image)

    async def list_images(self, labels: Mapping[str, str]) -> Sequence[str]:
        """See `DockerClientPort.list_images` (a Night Veil Cell's snapshot images)."""
        return await asyncio.to_thread(_sync_list_images, self._handle(), labels)

    async def recreate_from_image(self, name: str, image: str) -> None:
        """See `DockerClientPort.recreate_from_image` (roadmap step 5.10)."""
        await asyncio.to_thread(_sync_recreate_from_image, self._handle(), name, image)

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
                networks=_attached_networks(container.attrs),
            )
            for container in containers
        )

    def _handle(self) -> _DockerHandle:
        """Bundle the SDK client and module for a module-level blocking half (class docstring)."""
        return _DockerHandle(self._client, self._docker)


def _sync_create_network(handle: _DockerHandle, spec: NetworkSpec) -> str:
    """Blocking half of create_network: a bridge network with whatever subnet `spec` fixes."""
    options = {_ICC_OPTION: "false"} if spec.isolates_peers else None
    ipam = None
    # A fixed subnet and gateway only when the spec names them (the control network); every
    # per-Cell network lets the daemon pick a free one from its own address pools.
    if spec.subnet is not None:
        pool = handle.docker_module.types.IPAMPool(subnet=spec.subnet, gateway=spec.gateway)
        ipam = handle.docker_module.types.IPAMConfig(pool_configs=[pool])
    try:
        network = handle.client.networks.create(
            name=spec.name,
            driver="bridge",
            internal=spec.internal,
            labels=dict(spec.labels),
            options=options,
            ipam=ipam,
        )
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not create network {spec.name!r}: {exc}") from exc
    return str(network.id)


def _sync_ensure_network(handle: _DockerHandle, spec: NetworkSpec) -> str:
    """Blocking half of ensure_network: reuse a network of that name that matches, else create."""
    existing = _find_network(handle, spec.name)
    if existing is None:
        try:
            return _sync_create_network(handle, spec)
        except DockerClientError:
            # A concurrent provision may have created it between the lookup and the create: reuse
            # that one after the same check, or fail with the create's own error if there is none.
            existing = _find_network(handle, spec.name)
            if existing is None:
                raise
    network_id, attrs = existing
    _check_matches(attrs, spec)
    return network_id


def _sync_remove_network(handle: _DockerHandle, name: str) -> None:
    """Blocking half of remove_network: a missing network is success, not failure."""
    try:
        handle.client.networks.get(name).remove()
    except handle.docker_module.errors.NotFound:
        return  # Idempotent: nothing to remove, matching DockerNetworkPort's own contract.
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not remove network {name!r}: {exc}") from exc


def _sync_attach(handle: _DockerHandle, network: str, container: str, attach: bool) -> None:
    """Blocking half of connect/disconnect_network: attach or detach, unless it already is so."""
    verb = "attach" if attach else "detach"
    try:
        target = handle.client.containers.get(container)
        # Read first, so a repeated cut or restore changes nothing (DockerNetworkPort's contract);
        # a network already gone counts as detached, since the container is on it no longer.
        if (network in _attached_networks(target.attrs)) == attach:
            return
        bridge = handle.client.networks.get(network)
        if attach:
            bridge.connect(container)
        else:
            bridge.disconnect(container)
    except handle.docker_module.errors.NotFound as exc:
        raise DockerClientError(f"could not {verb} {container!r} ({network!r}): {exc}") from exc
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not {verb} {container!r} ({network!r}): {exc}") from exc


def _sync_create_volume(handle: _DockerHandle, spec: VolumeSpec) -> str:
    """Blocking half of create_volume: runs on a worker thread, never the event loop."""
    try:
        volume = handle.client.volumes.create(name=spec.name, labels=dict(spec.labels))
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not create volume {spec.name!r}: {exc}") from exc
    return str(volume.name)


def _sync_remove_volume(handle: _DockerHandle, name: str) -> None:
    """Blocking half of remove_volume: a missing volume is success, not failure."""
    try:
        handle.client.volumes.get(name).remove()
    except handle.docker_module.errors.NotFound:
        return  # Idempotent: nothing to remove, matching DockerClientPort's own contract.
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not remove volume {name!r}: {exc}") from exc


def _find_network(handle: _DockerHandle, name: str) -> tuple[str, dict[str, Any]] | None:
    """Return the id and inspect attrs of the network named `name`, or None when there is none."""
    try:
        network = handle.client.networks.get(name)
    except handle.docker_module.errors.NotFound:
        return None
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not inspect network {name!r}: {exc}") from exc
    return str(network.id), dict(network.attrs)


def _check_matches(attrs: dict[str, Any], spec: NetworkSpec) -> None:
    """Raise DockerClientError unless an existing network enforces what `spec` asks of it."""
    pool = ((attrs.get("IPAM") or {}).get("Config") or [{}])[0]
    isolates = (attrs.get("Options") or {}).get(_ICC_OPTION) == "false"
    # The flags must agree; an unset subnet or gateway accepts whatever the network already has.
    if (
        bool(attrs.get("Internal")) != spec.internal
        or isolates != spec.isolates_peers
        or spec.subnet not in (None, pool.get("Subnet"))
        or spec.gateway not in (None, pool.get("Gateway"))
    ):
        raise DockerClientError(f"network {spec.name!r} exists but does not match {spec}")


def _attached_networks(attrs: dict[str, Any]) -> tuple[str, ...]:
    """Return the names of the networks an inspected container is attached to, sorted."""
    networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    return tuple(sorted(networks))


def _sync_list_images(handle: _DockerHandle, labels: Mapping[str, str]) -> Sequence[str]:
    """Blocking half of list_images: the daemon filters by every label, ANDed together."""
    wanted = [f"{key}={value}" for key, value in sorted(labels.items())]
    try:
        images = handle.client.images.list(filters={"label": wanted})
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not list images labelled {wanted!r}: {exc}") from exc
    return tuple(str(image.id) for image in images)


def _sync_commit_container(
    handle: _DockerHandle, name: str, repository: str, tag: str, labels: Mapping[str, str]
) -> CommitResult:
    """Blocking half of commit_container: `docker commit`, image config carries `labels`.

    Module-level, not a method (see `SdkDockerClient.commit_container`'s own docstring for why):
    `handle` bundles `SdkDockerClient._client`/`._docker`, passed explicitly.
    """
    try:
        container = handle.client.containers.get(name)
        image = container.commit(repository=repository, tag=tag, conf={"Labels": dict(labels)})
    except handle.docker_module.errors.NotFound as exc:
        raise DockerClientError(f"could not commit container {name!r}: not found: {exc}") from exc
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not commit container {name!r}: {exc}") from exc
    # "Size" is docker-py's own reported layer size on the committed image's attrs; missing on
    # some daemon versions, so 0 (an honest "unknown", not a crash) is the fallback.
    size_bytes = int(image.attrs.get("Size", 0))
    return CommitResult(image=f"{repository}:{tag}", size_bytes=size_bytes)


def _sync_remove_image(handle: _DockerHandle, image: str) -> None:
    """Blocking half of remove_image: a missing image is success, not failure."""
    try:
        handle.client.images.get(image).remove(force=True)
    except handle.docker_module.errors.NotFound:
        return  # Idempotent: nothing to remove, matching DockerClientPort's own contract.
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not remove image {image!r}: {exc}") from exc


def _sync_recreate_from_image(handle: _DockerHandle, name: str, image: str) -> None:
    """Blocking half of recreate_from_image: read the old container's config, then swap it."""
    try:
        old = handle.client.containers.get(name)
        spec = _recreate_spec(old.attrs, image)
        networks = _attached_networks(old.attrs)
        old.remove(force=True)
        new_container = handle.client.containers.create(**spec)
        # `create` attached the first; every other network the old one was on is attached before
        # start, so a dual-homed Cell comes back dual-homed, and an isolated one still isolated.
        for extra in networks[1:]:
            handle.client.networks.get(extra).connect(new_container)
        new_container.start()
    except handle.docker_module.errors.NotFound as exc:
        raise DockerClientError(f"could not recreate container {name!r}: not found: {exc}") from exc
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(
            f"could not recreate container {name!r} from {image!r}: {exc}"
        ) from exc


def _log_config(driver: str | None) -> dict[str, str] | None:
    """Return docker-py's `log_config` for `driver`, or None to keep the daemon's own default.

    docker-py turns the plain mapping into its own `LogConfig`, so no SDK type is built here.
    """
    return None if driver is None else {"type": driver}


def _recreate_spec(attrs: dict[str, Any], image: str) -> dict[str, Any]:
    """Build docker-py `containers.create` kwargs from an existing container's own inspect attrs.

    Reads the container being replaced's own network, volumes and resource limits back (rather
    than needing the original `ContainerSpec`, which `DockerClientPort.recreate_from_image`'s own
    docstring documents this module never receives), so the recreated container keeps everything
    about `name`'s own runtime shape except the image it boots from.
    """
    host_config = attrs.get("HostConfig", {})
    config = attrs.get("Config", {})
    networks = _attached_networks(attrs)
    network_name = networks[0] if networks else None
    volumes = {
        mount["Name"]: {"bind": mount["Destination"], "mode": "rw"}
        for mount in attrs.get("Mounts", [])
        if mount.get("Type") == "volume" and "Name" in mount
    }
    return {
        "image": image,
        "name": attrs.get("Name", "").lstrip("/"),
        "environment": config.get("Env", []),
        "labels": config.get("Labels") or {},
        "network": network_name,
        "extra_hosts": host_config.get("ExtraHosts") or None,
        "volumes": volumes,
        "nano_cpus": host_config.get("NanoCpus") or None,
        "mem_limit": host_config.get("Memory") or None,
        "pids_limit": host_config.get("PidsLimit") or None,
        "cap_drop": host_config.get("CapDrop") or None,
        "cap_add": host_config.get("CapAdd") or None,
        "security_opt": host_config.get("SecurityOpt") or None,
        "read_only": bool(host_config.get("ReadonlyRootfs", False)),
        "tmpfs": host_config.get("Tmpfs") or {},
    }


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
