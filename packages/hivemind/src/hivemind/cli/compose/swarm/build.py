"""Define SwarmAccess and build_swarm: turn `[swarm]` into the Hive's listener, door and gate.

`build_swarm` is the one place the manifest's `[swarm]` section becomes running parts (codingrules
section 13). With `[swarm] enabled = false` (the default) it builds nothing and returns None, so a
Hive that never mentions the Swarm has no listener, no table and no Warden for it. With it on it
builds, in the order each needs the last: the registry of enrolled devices and their invites (in
the Hive's one SQLite file, so an invite the operator minted in one process is seen by a running
Hive); the Hive's own signing key, from the secret store, which signs every frame the Hive sends a
device and whose public half every invite carries for the device to pin; the enrolment door that
mints invites and admits the device that presents one; the hub of live links; the listener that
binds `[swarm] listen_host`/`listen_port` (TLS when a certificate is configured, plaintext
`ws://` on loopback otherwise, which the section's own validator enforces); the factory that makes
each device's Warden; and the gate that owns the listener and keeps those Wardens.

Nothing it builds is started: `hivemind.cli.compose.hive.run_hive` starts the gate with the Queen,
as it starts the Virtual side. `SwarmAccess` is what a caller needs a handle on: the gate to run,
and the listener, registry and door for tests and for `hive swarm`, which mint an invite or list
nodes against the same parts the running Hive holds.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.swarm`. Called by
    `hivemind.cli.compose.hive.build._assemble_hive`, once per Hive. Calls into
    `hivemind.cell` (CellIdentity), `hivemind.cli.compose.deps` (HiveParts, manifest_dir),
    `hivemind.cli.compose.swarm.device_warden`, `.door` (the invite's address half, shared with
    `hive swarm invite`), `hivemind.cli.stores` (open_swarm_registry), `hivemind.cli.version`
    (the runtime a colonized device installs), `hivemind.common.secrets`, `hivemind.guard`,
    `hivemind.queen.swarm_gate`, `hivemind.swarm` and waggle only.

Key invariants:
    - `[swarm] enabled = false` builds nothing: no file is opened, no key minted, no port bound.
    - `build_swarm` never binds a port or touches the network; it opens the registry's SQLite file
      and reads (or, on the Hive's first run, mints) the Hive's signing key, once each.
    - The signing key is the Hive's one key, the same the Virtual side signs with, so a Hive that
      has both presents one identity.
    - The invite carries the URL the listener advertises and the fingerprint of the certificate it
      serves, read from the same file, so the invite and the listener cannot disagree.

See Also:
    - docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md for the trust model.
    - hivemind.cli.compose.virtual_cells for the other optional subsystem the Hive starts and stops.
    - hivemind.queen.swarm_gate for the gate this builds.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from hivemind.cell import CellIdentity
from hivemind.cli.compose.deps.parts import HiveParts, manifest_dir
from hivemind.cli.compose.swarm.device_warden import SwarmWardenFactory
from hivemind.cli.compose.swarm.door import invite_endpoint
from hivemind.cli.stores import open_swarm_registry
from hivemind.cli.version import collect_version_info
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.guard.scanner import ContentScanner
from hivemind.queen.swarm_gate import DeviceWardens, GateTerms, SwarmGate
from hivemind.swarm import (
    EnrolmentDoor,
    EnrolmentRules,
    GatewayHub,
    ListenerConfig,
    ListenerDeps,
    SwarmListener,
    SwarmRecorder,
    SwarmRegistry,
)
from hivemind.swarm.gateway import HiveVoice

__all__ = ["SwarmAccess", "build_swarm"]


@dataclass(frozen=True, slots=True)
class SwarmAccess:
    """Everything a caller needs a handle on once the Swarm is built.

    Attributes:
        gate: Owns the listener and keeps a Warden on each device; `run_hive` starts, runs and
            stops it.
        listener: The swarm listener devices dial; its `hub` says who is connected.
        registry: The enrolled devices and their invites.
        door: Mints a device invite and admits the device that presents it.
    """

    gate: SwarmGate
    listener: SwarmListener
    registry: SwarmRegistry
    door: EnrolmentDoor


def build_swarm(parts: HiveParts, scanner: ContentScanner) -> SwarmAccess | None:
    """Build the Swarm's listener, door, hub and gate from `[swarm]`, not yet started.

    Args:
        parts: This Hive's shared collaborators; its manifest's `[swarm]` section decides whether
            anything is built.
        scanner: The Hive's one untrusted-content scanner, shared with every device Warden.

    Returns:
        The Swarm's handles, or None when `[swarm] enabled` is false.

    SAFETY: a fresh event loop for the one secret-store read below, the same seam `build_hive` uses
    for the Virtual side's key: the store is async, `build_hive` is a sync composition root, and
    the call is one small file read (or one write, the first time), never a network request.
    """
    manifest = parts.manifest
    section = manifest.swarm
    if not section.enabled:
        return None
    registry = open_swarm_registry(manifest.resolve_path(manifest.hive.db))
    secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    signer = asyncio.run(load_or_mint_hive_signer(secrets))
    config = ListenerConfig.from_section(section, manifest_dir(manifest))
    recorder = _recorder(parts)
    rules = EnrolmentRules.from_section(section, invite_endpoint(manifest, config, signer))
    door = EnrolmentDoor(registry, recorder, rules)
    hub = GatewayHub(parts.clock)
    voice = HiveVoice(manifest.hive.id, manifest.hive.node_id)
    listener = SwarmListener(config, ListenerDeps(registry, door, hub, recorder, voice, signer))
    wardens = DeviceWardens(SwarmWardenFactory(parts, hub, recorder, scanner), hub)
    gate = SwarmGate(listener, registry, wardens, _gate_terms(parts), parts.clock)
    return SwarmAccess(gate=gate, listener=listener, registry=registry, door=door)


def _gate_terms(parts: HiveParts) -> GateTerms:
    """Gather the numbers the gate works to from `[swarm]`, `[supervision]` and the build itself."""
    manifest = parts.manifest
    section = manifest.swarm
    return GateTerms(
        interval_s=section.heartbeat_interval_s,
        unreachable_grace_s=section.unreachable_grace_s,
        max_offline_s=manifest.supervision.max_offline_s,
        # The Hive's own build: the runtime a colonized device installs is the one that serves it.
        runtime_version=collect_version_info().hivemind_version,
        relief_pressure=section.ladder.relief_pressure,
        min_free_memory_bytes=section.ladder.min_free_memory_bytes,
    )


def _recorder(parts: HiveParts) -> SwarmRecorder:
    """Build the writer of the swarm's events: the Hive's trail, as the system."""
    manifest = parts.manifest
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    return SwarmRecorder(parts.stores.trail, parts.clock, identity)
