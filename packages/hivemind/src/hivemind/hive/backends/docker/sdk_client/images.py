"""Run the blocking halves of SdkDockerClient's image calls, each on a worker thread.

Roadmap step 5.10's snapshot slice: commit a container to an image, remove an image, and recreate
a container from one, reading the old container's own shape back from its inspect attrs (it never
receives the `ContainerSpec` it was made from) and re-attaching every network it was on. The
Night Veil teardown adds the listing of a Cell's snapshot images by the label every commit stamps
(codingrules section 12). Each is a module function over one `DockerHandle` (`handle`'s docstring
says why), and every docker-py exception becomes a `DockerClientError`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker.sdk_client`. Called by
    `SdkDockerClient` under `asyncio.to_thread`, for `hivemind.hive.snapshot.docker`'s
    `DockerSnapshotter` and `DockerSnapshotImages`. Calls into docker-py only through a
    `DockerHandle`, and into its `networks` sibling for a container's attachments.

Key invariants:
    - `sync_remove_image` never raises for an image that does not exist.
    - A recreated container keeps its name, networks, volumes and limits; only its image changes.

See Also:
    - hivemind.hive.backends.docker.client for the image half of DockerClientPort.
    - hivemind.hive.snapshot.docker for the snapshots and the teardown that call these.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hivemind.hive.backends.docker.client import CommitResult, DockerClientError
from hivemind.hive.backends.docker.sdk_client.handle import DockerHandle
from hivemind.hive.backends.docker.sdk_client.networks import attached_networks

__all__ = [
    "recreate_spec",
    "sync_commit_container",
    "sync_list_images",
    "sync_recreate_from_image",
    "sync_remove_image",
]


def sync_list_images(handle: DockerHandle, labels: Mapping[str, str]) -> Sequence[str]:
    """Blocking half of list_images: the daemon filters by every label, ANDed together."""
    wanted = [f"{key}={value}" for key, value in sorted(labels.items())]
    try:
        images = handle.client.images.list(filters={"label": wanted})
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not list images labelled {wanted!r}: {exc}") from exc
    return tuple(str(image.id) for image in images)


def sync_commit_container(
    handle: DockerHandle, name: str, repository: str, tag: str, labels: Mapping[str, str]
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


def sync_remove_image(handle: DockerHandle, image: str) -> None:
    """Blocking half of remove_image: a missing image is success, not failure."""
    try:
        handle.client.images.get(image).remove(force=True)
    except handle.docker_module.errors.NotFound:
        return  # Idempotent: nothing to remove, matching DockerClientPort's own contract.
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not remove image {image!r}: {exc}") from exc


def sync_recreate_from_image(handle: DockerHandle, name: str, image: str) -> None:
    """Blocking half of recreate_from_image: read the old container's config, then swap it."""
    try:
        old = handle.client.containers.get(name)
        spec = recreate_spec(old.attrs, image)
        networks = attached_networks(old.attrs)
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


def recreate_spec(attrs: dict[str, Any], image: str) -> dict[str, Any]:
    """Build docker-py `containers.create` kwargs from an existing container's own inspect attrs.

    Reads the container being replaced's own network, volumes and resource limits back (rather
    than needing the original `ContainerSpec`, which `DockerClientPort.recreate_from_image`'s own
    docstring documents this module never receives), so the recreated container keeps everything
    about `name`'s own runtime shape except the image it boots from.
    """
    host_config = attrs.get("HostConfig", {})
    config = attrs.get("Config", {})
    networks = attached_networks(attrs)
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
