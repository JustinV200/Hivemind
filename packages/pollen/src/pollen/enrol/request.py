"""Build the enrolment request: the device's identity, its key and what it finds out about itself.

The first thing a new device says to the Queen (the Hive's central orchestrator) is one
``swarm.enrol_request``, signed with the node key the Pollen Packet (the thin gateway program on a
device the Hive borrows) has just made. It carries the single-use token from the invite, the ids
the packet minted for itself (a ``device_id`` that is its bee address for life and the ``node_id``
whose key signs its frames), the matching public key, the device's hostname, the packet's version,
and the three probes everything the Hive later decides about the device reads: the platform report,
the capability flags and the host capacity. It also asks for an access level, ``FULL`` by default,
which the operator's cap may lower. Building the request is separate from sending it so the probes
and the identity can be tested without a connection; the token goes in here and nowhere else, and
the request is never written to disk or to an outbox.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.enrol.enrol;
    calls into pollen.executors.info (the probes), the platform shim and waggle's message models.

Key invariants:
    - The token is copied into the request exactly once, from the parsed invite, and is not
      logged, stored or echoed.
    - The probes run on this device and describe it; the Queen trusts none of the text in them.
    - The request's identities equal the envelope's, which the Queen's door requires.

See Also:
    - docs/waggle/enrolment.md section 5 (the request) and spec section 8.10 (the fields).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from pollen.enrol.invite import Invite
from pollen.executors.info import (
    capabilities_report,
    host_capacity,
    hostname,
    platform_report,
    runtime_version,
)
from pollen.platform.base import PlatformShim
from waggle.ids import DeviceId, NodeId
from waggle.messages.labels import AccessLevel
from waggle.messages.swarm import EnrolRequest

__all__ = ["DeviceIdentity", "build_request"]


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """The ids and key a device minted for itself, before it has told anyone."""

    device_id: DeviceId  # The bee address every frame from the packet carries.
    node_id: NodeId  # The node whose key signs them.
    public_key_hex: str  # The raw 32-byte Ed25519 public key, lowercase hex.


async def build_request(
    invite: Invite,
    identity: DeviceIdentity,
    shim: PlatformShim,
    data_dir: Path,
    requested_access: AccessLevel,
) -> EnrolRequest:
    """Probe the device and assemble the request.

    Args:
        invite: The parsed invite; only its token is used here.
        identity: The ids and public key the device minted.
        shim: The platform shim, which knows the operating-system facts.
        data_dir: The data directory, whose filesystem is the disk the capacity describes.
        requested_access: The level to ask for; the operator's cap may lower it.

    Returns:
        The request, ready to be wrapped in an envelope, signed and sent once.
    """
    # The capacity probe reads the disk and the load, which can take a moment: off the loop.
    capacity = await asyncio.to_thread(host_capacity, data_dir)
    return EnrolRequest(
        invite_token=invite.token,
        device_id=identity.device_id,
        node_id=identity.node_id,
        public_key_hex=identity.public_key_hex,
        hostname=hostname(),
        runtime_version=runtime_version(),
        platform=platform_report(shim),
        capabilities=capabilities_report(shim),
        capacity=capacity,
        max_sub_bees=None,
        requested_access=requested_access,
    )
