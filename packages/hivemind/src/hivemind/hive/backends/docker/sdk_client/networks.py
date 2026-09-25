"""Run the blocking halves of SdkDockerClient's network calls, each on a worker thread.

Roadmap step 10.6a's network slice: create a bridge network (with a fixed subnet and gateway, and
inter-container traffic off, when its spec asks, as the per-Hive control network's does), make one
or reuse one that already enforces what its spec says, remove one, and attach or detach a running
container, reading its attachments first so a repeated cut or restore changes nothing. Each is a
module function over one `DockerHandle` (`handle`'s docstring says why), and every docker-py
exception becomes a `DockerClientError`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker.sdk_client`. Called by
    `SdkDockerClient` under `asyncio.to_thread`, and `attached_networks` by its `client` and
    `images` siblings too. Calls into docker-py only through a `DockerHandle`.

Key invariants:
    - `sync_remove_network` never raises for a network that does not exist, and `sync_attach`
      never touches a container already in the state asked for (`DockerNetworkPort`'s contract).
    - `sync_ensure_network` never hands back a network whose internal flag, peer isolation,
      subnet or gateway differs from its spec: a Cell never joins one that enforces less.

See Also:
    - hivemind.hive.backends.docker.client.DockerNetworkPort for the contract implemented here.
    - hivemind.hive.backends.docker.egress for the isolation lever these calls carry.
"""

from __future__ import annotations

from typing import Any

from hivemind.hive.backends.docker.client import DockerClientError, NetworkSpec
from hivemind.hive.backends.docker.sdk_client.handle import DockerHandle

__all__ = [
    "attached_networks",
    "check_matches",
    "sync_attach",
    "sync_create_network",
    "sync_ensure_network",
    "sync_remove_network",
]

# The bridge driver's inter-container switch: "false" keeps containers on one bridge apart.
_ICC_OPTION = "com.docker.network.bridge.enable_icc"


def sync_create_network(handle: DockerHandle, spec: NetworkSpec) -> str:
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


def sync_ensure_network(handle: DockerHandle, spec: NetworkSpec) -> str:
    """Blocking half of ensure_network: reuse a network of that name that matches, else create."""
    existing = _find_network(handle, spec.name)
    if existing is None:
        try:
            return sync_create_network(handle, spec)
        except DockerClientError:
            # A concurrent provision may have created it between the lookup and the create: reuse
            # that one after the same check, or fail with the create's own error if there is none.
            existing = _find_network(handle, spec.name)
            if existing is None:
                raise
    network_id, attrs = existing
    check_matches(attrs, spec)
    return network_id


def sync_remove_network(handle: DockerHandle, name: str) -> None:
    """Blocking half of remove_network: a missing network is success, not failure."""
    try:
        handle.client.networks.get(name).remove()
    except handle.docker_module.errors.NotFound:
        return  # Idempotent: nothing to remove, matching DockerNetworkPort's own contract.
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not remove network {name!r}: {exc}") from exc


def sync_attach(handle: DockerHandle, network: str, container: str, attach: bool) -> None:
    """Blocking half of connect/disconnect_network: attach or detach, unless it already is so."""
    verb = "attach" if attach else "detach"
    try:
        target = handle.client.containers.get(container)
        # Read first, so a repeated cut or restore changes nothing (DockerNetworkPort's contract);
        # a network already gone counts as detached, since the container is on it no longer.
        if (network in attached_networks(target.attrs)) == attach:
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


def check_matches(attrs: dict[str, Any], spec: NetworkSpec) -> None:
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


def attached_networks(attrs: dict[str, Any]) -> tuple[str, ...]:
    """Return the names of the networks an inspected container is attached to, sorted."""
    networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    return tuple(sorted(networks))


def _find_network(handle: DockerHandle, name: str) -> tuple[str, dict[str, Any]] | None:
    """Return the id and inspect attrs of the network named `name`, or None when there is none."""
    try:
        network = handle.client.networks.get(name)
    except handle.docker_module.errors.NotFound:
        return None
    except handle.docker_module.errors.APIError as exc:
        raise DockerClientError(f"could not inspect network {name!r}: {exc}") from exc
    return str(network.id), dict(network.attrs)
