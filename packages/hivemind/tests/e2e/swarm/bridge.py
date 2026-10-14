"""Build a whole Hive whose swarm listener the containers on a docker network dial.

`e2e.swarm.enrolled.start_stand` serves the bare listener, which is all a test of the wire needs. A
test of what the Queen does with a device (places a task on it, holds its tasks when it goes, lets
its Warden watch it, moves its Warden onto it) needs the rest: the real Queen, Wardens, trail and
SQLite file, built exactly as `hive run` builds them from a manifest. `bridge_manifest` writes that
manifest, with `[swarm]` listening on the network's host address over `wss://` with a fresh
self-signed certificate (the invite carries its pin) and advertising the host name the containers
map to that address, and `bridge_hive` builds the Hive from it with a scripted model. `BridgeHive`
is the Hive seen as a `Door`, the three things `enrol_device` asks of a Stand, so the same
enrolment that serves the bare listener serves the whole Hive. This is the one such rig: every
phase 11 scenario that puts a whole Hive in front of a container builds it here.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the phase 11 end-to-end
    scenarios that need a device a real Hive places work on; builds on `builders.swarm.manifest`,
    `e2e.swarm.enrolled`, `e2e.swarm.tls` and `hivemind.cli.compose`.

Key invariants:
    - The Hive is the real one; only its model is scripted (the responder the caller passes), and
      the Drone's Forage footprint is set to cost no core, so a task is never held back because the
      machine under the test (whose load a container reports as its own) is busy.
    - The listener binds the container network's host address only, never every interface, and
      the invite names the host name the container's `--add-host` resolves.
    - `bridge_hive` builds with `build_hive`, which runs event loops of its own: call it from
      synchronous test code, as `e2e.swarm.hive.swarm_hive` is.
    - Extra manifest tables (`BridgeTerms.tables`) are appended after everything else, so a table a
      scenario adds can never be shadowed by one this file writes.

See Also:
    - e2e/swarm/enrolled.py for the enrolment this Hive is a Door for.
    - e2e/swarm/hive.py for the same Hive on loopback, for a packet in the test's own process.
    - e2e/swarm/drone.py for the scripted model and the goal a scenario puts to this Hive.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from builders.cli import ManifestTuning
from builders.swarm.live import free_port
from builders.swarm.manifest import tuned_swarm_manifest
from e2e.swarm.container import Network
from e2e.swarm.enrolled import HIVE_HOSTNAME
from e2e.swarm.tls import certificate_for

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.cli.compose import Hive, build_hive
from hivemind.cli.compose.swarm import SwarmAccess
from hivemind.llm import Responder
from hivemind.manifest import load_manifest
from hivemind.swarm import DeviceLink, MintedInvite, SwarmRegistry
from waggle.clock import SystemClock
from waggle.ids import DeviceId

# A device is called lost after four silent beats. The scenarios wait seconds for anything that
# depends on it, so the beat is slow enough that a busy build machine stalling the Hive or the
# container for a second (the load average here is often above the core count) is not a lost device.
BRIDGE_BEAT_S = 1.0

__all__ = ["BRIDGE_BEAT_S", "BridgeHive", "BridgeTerms", "bridge_hive", "bridge_manifest"]


@dataclass(frozen=True, slots=True)
class BridgeTerms:
    """What a bridge Hive's manifest says beyond the listener.

    Attributes:
        swarm: Further `[swarm]` keys, as TOML scalars: `dead_man_s`, `unreachable_grace_s` and
            the like. They are written after the listener's own, so one named here wins.
        watch_table: The body of a `[supervision.watch]` table (see `e2e.watch_helpers.
            watch_table`), or empty to leave watch mode at its documented defaults.
        tables: Whole TOML tables to append to the manifest, such as the `[llm.servers.*]` row and
            the `[forage.map.*]` rows that list a Nuc's model server; empty adds none.
    """

    swarm: Mapping[str, bool | int | float | str] = field(default_factory=dict)
    watch_table: str = ""
    tables: str = ""


def bridge_manifest(tmp_path: Path, network: Network, terms: BridgeTerms | None = None) -> Path:
    """Write the manifest of a Hive whose swarm listener binds `network`'s host address.

    Args:
        tmp_path: The test's own directory; the manifest, certificate, database and scratch live
            under it.
        network: The container network; its host address is where the swarm listener binds. The
            invite tells a device to dial `e2e.swarm.HIVE_HOSTNAME`, which the device's container
            maps there.
        terms: Further manifest keys and tables; none when omitted.

    Returns:
        The manifest's path. Its listener is on a port that was free a moment ago.
    """
    chosen = terms or BridgeTerms()
    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    material = certificate_for(tls_dir, HIVE_HOSTNAME)
    host = network.gateway
    port = free_port(host)
    listener: dict[str, bool | int | float | str] = {
        "listen_host": host,
        "listen_port": port,
        "advertise_url": f"wss://{HIVE_HOSTNAME}:{port}",
        "tls_cert": str(material.cert_path),
        "tls_key": str(material.key_path),
        "heartbeat_interval_s": BRIDGE_BEAT_S,
    }
    # A Drone on a container is granted the container's free cores, and a container reports the
    # load average of the machine under it, which a scenario does not control. These scenarios are
    # about the swarm, not about how busy the machine running them is, so the Drone costs no core.
    tuning = ManifestTuning(watch_table=chosen.watch_table, drone_cpu_cores=0.0)
    path = tuned_swarm_manifest(tmp_path, tuning, **{**listener, **chosen.swarm})
    if chosen.tables:
        path.write_text(f"{path.read_text('utf-8')}\n{chosen.tables}\n", encoding="utf-8")
    return path


def bridge_hive(
    tmp_path: Path, network: Network, responder: Responder, terms: BridgeTerms | None = None
) -> Hive:
    """Build a Hive that the containers on `network` can dial, its model scripted.

    Args:
        tmp_path: The test's own directory (see `bridge_manifest`).
        network: The container network; its host address is where the swarm listener binds.
        responder: Answers every model request of the Hive (the Queen's plan, the Drone's turns,
            the Patrol), as `Script.responder` or `PatrolModel.responder` does.
        terms: Further manifest keys and tables; none when omitted.

    Returns:
        A Hive, not yet started, with `Hive.swarm` set.
    """
    manifest = load_manifest(bridge_manifest(tmp_path, network, terms), {})
    return build_hive(manifest, environ={}, clock=SystemClock(), responders={"fake": responder})


@dataclass(frozen=True, slots=True)
class BridgeHive:
    """A whole Hive as a `Door`: the invite, the registry and the link wait a container needs."""

    hive: Hive

    @property
    def swarm(self) -> SwarmAccess:
        """The Hive's Swarm: its door, registry and listener."""
        assert self.hive.swarm is not None, "the Hive was built with [swarm] enabled = false"
        return self.hive.swarm

    async def mint(
        self, access: AccessLevel, shield: CombShieldLevel, label: str | None = None
    ) -> MintedInvite:
        """Mint an invite at the Hive's own door, as `hive swarm invite` does."""
        return await self.swarm.door.mint_invite(access, shield, label)

    @property
    def registry(self) -> SwarmRegistry:
        """The Hive's registry of enrolled devices."""
        return self.swarm.registry

    async def wait_for(self, device_id: DeviceId, timeout_s: float) -> DeviceLink:
        """Wait for the device's packet to dial the Hive's listener and verify."""
        return await self.swarm.listener.hub.wait_for(device_id, timeout_s)
