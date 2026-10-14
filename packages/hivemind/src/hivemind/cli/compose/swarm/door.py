"""Compose the enrolment door an operator mints invites with, without building a whole Hive.

An invite (`pollen1.<...>`, the one-time string a device enrols with) says where to dial, which
Hive it joins and what to pin about it: the listener's advertised URL, the Queen's bee address, the
Hive Stand's node id and public key (the Hive key), and the fingerprint of the certificate the
listener serves. The running Hive and `hive swarm invite` must put the *same* address half in every
invite, or a device would pin one thing and meet another, so both build it here, from the manifest
and the Hive's one signing key. `build_swarm` hands the endpoint to the door it keeps beside the
listener; `build_invite_door` builds the door alone for the command, over a registry and recorder
the command opened, which is all minting needs: it writes the invite row and its `swarm.invited`
event and starts no listener, Queen or Warden.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.swarm`. Called by
    `build_swarm` (for the endpoint) and by `hive swarm invite` (for the door). Calls into
    `hivemind.cli.compose.deps.parts` (manifest_dir), `hivemind.common.secrets`,
    `hivemind.manifest`, `hivemind.swarm` and waggle only.

Key invariants:
    - The Hive key in an invite is the public half of the key in the Hive's secret store, minted
      there on the Hive's first use, so an invite minted before the Hive first runs still names
      the key it will sign with.
    - The advertised URL and the certificate fingerprint are read from the same `[swarm]` settings
      and file the listener serves, so the invite and the listener cannot disagree.
    - Nothing here binds a port or reads a secret beyond the one signing key.

See Also:
    - docs/waggle/enrolment.md sections 2 and 3.3 for the listener and the invite's eight keys.
    - hivemind.cli.compose.swarm.build for the running Hive's use of the same endpoint.
"""

from __future__ import annotations

import asyncio

from hivemind.cli.compose.deps.parts import manifest_dir
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.manifest import HiveManifest
from hivemind.swarm import (
    EnrolmentDoor,
    EnrolmentRules,
    InviteEndpoint,
    ListenerConfig,
    SwarmRecorder,
    SwarmRegistry,
)
from waggle.signing import Ed25519Signer, public_key_hex

__all__ = ["build_invite_door", "invite_endpoint"]


def invite_endpoint(
    manifest: HiveManifest, config: ListenerConfig, signer: Ed25519Signer
) -> InviteEndpoint:
    """Build the address half of every invite: where to dial, who the Hive is, what to pin.

    Args:
        manifest: The Hive Manifest; its `[hive]` section names the Queen and the Hive Stand.
        config: The listener's configuration, which knows the URL it advertises and the
            fingerprint of the certificate it serves.
        signer: The Hive's signing key; its public half is the Hive key a device pins.

    Returns:
        The validated endpoint.
    """
    return InviteEndpoint(
        url=config.invite_url,
        hive_id=manifest.hive.id,
        hive_node_id=manifest.hive.node_id,
        hive_key_hex=public_key_hex(signer.public_key_bytes),
        tls_sha256=config.tls_pin,
    )


def build_invite_door(
    manifest: HiveManifest, registry: SwarmRegistry, recorder: SwarmRecorder
) -> EnrolmentDoor:
    """Build the door that mints invites for this manifest's listener.

    Must run outside any event loop: the Hive's signing key is read from the secret store with its
    own `asyncio.run`, one small file read (or, on the Hive's first use, one write).

    Args:
        manifest: The Hive Manifest, whose `[swarm]` section the invite's address half comes from.
        registry: Where the invite row is written; the running Hive reads the same rows.
        recorder: Where `swarm.invited` is built and the identity it is recorded as.

    Returns:
        A door whose `mint_invite` the command calls.

    Raises:
        FileNotFoundError: A configured TLS certificate file does not exist.
        hivemind.swarm.SwarmError: The certificate file holds no PEM certificate.
    """
    section = manifest.swarm
    config = ListenerConfig.from_section(section, manifest_dir(manifest))
    secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    signer = asyncio.run(load_or_mint_hive_signer(secrets))
    rules = EnrolmentRules.from_section(section, invite_endpoint(manifest, config, signer))
    return EnrolmentDoor(registry, recorder, rules)
