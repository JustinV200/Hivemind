"""Run the blocking halves of SdkDockerClient's volume calls, each on a worker thread.

A Virtual Cell's scratch volume is created with its labels and removed with its Cell; removing
one already gone is success, since a teardown retried after a partial failure must not fail on
what the first attempt already did. Each is a module function over one `DockerHandle`
(`handle`'s docstring says why), and every docker-py exception becomes a `DockerClientError`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker.sdk_client`. Called by
    `SdkDockerClient` under `asyncio.to_thread`. Calls into docker-py only through a
    `DockerHandle`.

Key invariants:
    - `sync_remove_volume` never raises for a volume that does not exist (`DockerClientPort`'s
      own idempotency invariant).

See Also:
    - hivemind.hive.backends.docker.client.DockerClientPort for the contract implemented here.
"""

from __future__ import annotations

from hivemind.hive.backends.docker.client import DockerClientError, VolumeSpec
from hivemind.hive.backends.docker.sdk_client.handle import DockerHandle

__all__ = ["sync_create_volume", "sync_remove_volume"]


def sync_create_volume(handle: DockerHandle, spec: VolumeSpec) -> str:
    """Blocking half of create_volume: runs on a worker thread, never the event loop."""
    try:
        volume = handle.client.volumes.create(name=spec.name, labels=dict(spec.labels))
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not create volume {spec.name!r}: {exc}") from exc
    return str(volume.name)


def sync_remove_volume(handle: DockerHandle, name: str) -> None:
    """Blocking half of remove_volume: a missing volume is success, not failure."""
    try:
        handle.client.volumes.get(name).remove()
    except handle.docker_module.errors.NotFound:
        return  # Idempotent: nothing to remove, matching DockerClientPort's own contract.
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not remove volume {name!r}: {exc}") from exc
