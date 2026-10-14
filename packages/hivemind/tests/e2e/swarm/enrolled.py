"""Start a Hive Stand that containers can dial, and enrol a Pollen Packet in a container on it.

The two halves of "a second machine" meet here. The Hive Stand is the real swarm listener
(`builders.swarm.live`) bound to the host's address on the container network, serving `wss://` with
a self-signed certificate for the host name `HIVE_HOSTNAME`; the container maps that name to the
same address with `--add-host`, so the invite a device is given names a host that exists only
inside the container. `enrol_container` mints an invite, starts a container from the device image
whose command is the packet's own (`pollen enrol` then `pollen run`, as a person would type them),
and waits until the Hive's registry holds the node it enrolled as and the node's link is up. What
the container does after that, a test decides: lease it with `builders.swarm.leasing`, cut it off
the network with `e2e.swarm.container.Network`, or look into it with `Container`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the swarm integration and
    end-to-end tests; builds on `builders.swarm.live`, `e2e.swarm.container` and `e2e.swarm.tls`.

Key invariants:
    - The invite reaches the container as an environment variable and the packet reads it from
      there, so the container's command line shows no token.
    - A container that exits before it is enrolled, or an enrolment that does not appear in
      time, raises `DeviceStartError` carrying the container's own log, never a bare timeout.
    - The Stand listens on the container network's host address only, not on every interface.

See Also:
    - docs/waggle/enrolment.md for the ceremony the container runs.
    - e2e/swarm/device/Dockerfile for the image the container starts from.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from builders.swarm.live import LiveHive, LiveHiveOptions, start_live_hive
from e2e.swarm.container import BRIDGE, Container, ContainerSpec, Janitor, Network
from e2e.swarm.tls import certificate_for

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm.errors import GatewayUnavailableError
from hivemind.swarm.models import SwarmNode

HIVE_HOSTNAME = "hive.test"  # What the invite names; only the container's /etc/hosts knows it.
DATA_DIR = "/var/lib/pollen"  # Where the packet keeps its key, state and leases in the container.
ENROL_AND_RUN = (
    'pollen enrol --data-dir "$POLLEN_DATA_DIR" "$POLLEN_INVITE" '
    '&& exec pollen run --data-dir "$POLLEN_DATA_DIR"'
)  # The container's command: enrol, then become the packet so `docker logs` shows it.
ENROL_TIMEOUT_S = 120.0  # A container start, the packet's imports and the ceremony.
DIAL_TIMEOUT_S = 60.0  # From enrolled to a link carrying its first frames.
POLL_S = 0.25  # How often the registry and the container are looked at while waiting.

__all__ = [
    "DATA_DIR",
    "HIVE_HOSTNAME",
    "ContainerDevice",
    "DeviceStartError",
    "enrol_container",
    "start_stand",
]


class DeviceStartError(Exception):
    """A device container did not enrol; the message carries the container's own output."""


@dataclass(frozen=True, slots=True)
class ContainerDevice:
    """A packet enrolled in a container: the container and the node the Hive knows it as."""

    container: Container
    node: SwarmNode


async def start_stand(
    directory: Path, network: Network = BRIDGE, options: LiveHiveOptions | None = None
) -> LiveHive:
    """Start a Hive Stand that the containers on `network` can dial over `wss://`.

    Args:
        directory: Where the Stand's certificate and key are written.
        network: The container network; its host address is where the listener binds.
        options: Liveness settings and the like; the address, name and certificate are set here.

    Returns:
        The running Stand; the caller stops it.
    """
    certificate = certificate_for(directory, HIVE_HOSTNAME)
    chosen = replace(
        options or LiveHiveOptions(),
        host=network.gateway,
        advertise_host=HIVE_HOSTNAME,
        tls=certificate,
    )
    return await start_live_hive(chosen)


async def enrol_container(
    hive: LiveHive,
    janitor: Janitor,
    image: str,
    network: Network = BRIDGE,
    access: AccessLevel = AccessLevel.FULL,
) -> ContainerDevice:
    """Start a container from the device image and enrol its packet on `hive`.

    Args:
        hive: A Stand from `start_stand` on the same network.
        janitor: Owns the container, which it removes at its `close`.
        image: The device image tag (`e2e.swarm.image.device_image`).
        network: The network to start the container on; its host address is the Stand's.
        access: The cap on the invite; the packet asks for FULL and receives the lower.

    Returns:
        The container and its node, with the node's link up.

    Raises:
        DeviceStartError: The container exited, or the enrolment or the link did not appear.
    """
    label = f"container-{uuid.uuid4().hex[:8]}"
    minted = await hive.mint(access, CombShieldLevel.MEADOW, label)
    spec = ContainerSpec(
        image=image,
        command=("sh", "-c", ENROL_AND_RUN),
        hosts={HIVE_HOSTNAME: network.gateway},
        environment={"POLLEN_INVITE": minted.invite_string, "POLLEN_DATA_DIR": DATA_DIR},
        network=None if network is BRIDGE else network.name,
    )
    container = janitor.start(spec)
    try:
        node = await _enrolled_node(hive, label, container)
        await hive.wait_for(node.device_id, DIAL_TIMEOUT_S)
    except (TimeoutError, GatewayUnavailableError, DeviceStartError) as failed:
        raise DeviceStartError(f"{failed}\nThe container's output:\n{container.logs()}") from failed
    return ContainerDevice(container, node)


async def _enrolled_node(hive: LiveHive, label: str, container: Container) -> SwarmNode:
    """Wait for the registry to hold the node enrolled under `label`, or the container to exit."""
    async with asyncio.timeout(ENROL_TIMEOUT_S):  # Network and a container's boot, bounded.
        while True:
            for node in await hive.registry.nodes():
                if node.label == label:
                    return node
            if not container.is_running():
                raise DeviceStartError("The container exited before its packet enrolled.")
            await asyncio.sleep(POLL_S)
