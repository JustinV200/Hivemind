"""Build the Hive's Guard policy from a manifest, with the Hive's own state named on it.

Roadmap step 10.3 builds the Guard policy from `[guard]` (its policy file, or the shipped one,
overlaid); ADR-0033 adds what no bee may touch or reach, whatever its set says: the Hive's own
state. `hive_state` names it from the manifest and this machine: the resolved `[hive] db` file
(with its SQLite siblings), the resolved `[hive] secrets_dir` and everything under it, the
manifest file itself, and every address this machine's own interfaces answer on (the Hive
Stand's own addresses), so the Hive-state floor refuses a bee `fs:read`/`fs:write` on any of them
and `net` to any of them. `guard_policy` is the one policy the Hive's Enforcer (`build_enforcer`,
shared by the Queen and the Hive Stand's Warden) decides against.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.build_hive` (through `hivemind.cli.compose.deps`, which re-exports
    `build_enforcer`). Calls into `hivemind.cell` (CellIdentity), `hivemind.guard` (the policy
    loader, `Enforcer`, `HiveState`, the address helpers), `hivemind.manifest`,
    `hivemind.pheromone`, `psutil` (this machine's interface addresses; the standard library
    cannot list them portably), waggle and the standard library only.

Key invariants:
    - Reading the interfaces never fails the Hive's start: a machine whose interfaces cannot be
      listed names no own address, and the loopback forms stay refused regardless.
    - Every path is resolved against the manifest's own directory, exactly as the stores that
      open them resolve it, so the floor names the very files the Hive writes.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Bees never touch
      the Hive's own state".
    - hivemind.guard.policy.hive_state for HiveState, what this builds.
"""

from __future__ import annotations

import dataclasses
import socket
from pathlib import Path

import psutil

from hivemind.cell import CellIdentity
from hivemind.guard import Enforcer, GuardPolicy, load_guard_policy
from hivemind.guard.net import IPAddress, ip_literal
from hivemind.guard.policy import HiveState
from hivemind.manifest import HiveManifest
from hivemind.pheromone import PheromoneTrail
from waggle.clock import Clock

# The address families an interface can be reached on over IP; link-layer entries are not.
_IP_FAMILIES = frozenset({socket.AF_INET, socket.AF_INET6})

__all__ = ["build_enforcer", "guard_policy", "hive_state", "interface_addresses"]


def build_enforcer(manifest: HiveManifest, trail: PheromoneTrail, clock: Clock) -> Enforcer:
    """Build the Hive's one Guard Enforcer over `guard_policy(manifest)` (roadmap step 10.3).

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        trail: Where every refusal's `guard.denied` row lands.
        clock: Mints each refusal's id and timestamp.

    Returns:
        An Enforcer recording as `actor="system"`, like every identity a composition root builds.
    """
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    return Enforcer(guard_policy(manifest), trail, clock, identity)


def guard_policy(manifest: HiveManifest) -> GuardPolicy:
    """Build the Guard policy from `[guard]`, with the Hive's own state named on it.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`; `[guard]` and
            `[hive]` are read.

    Returns:
        The policy file's policy (or the shipped one), overlaid by `[guard]`, carrying
        `hive_state(manifest)`.
    """
    section = manifest.guard
    # An empty policy_file means the policy shipped in `hivemind.guard.defaults`; any other
    # resolves against the manifest's own directory like every other manifest path.
    path = manifest.resolve_path(Path(section.policy_file)) if section.policy_file else None
    return dataclasses.replace(load_guard_policy(path, section), hive_state=hive_state(manifest))


def hive_state(manifest: HiveManifest) -> HiveState:
    """Name the Hive's own state: its database, its secrets, its manifest and its addresses.

    Args:
        manifest: The loaded manifest; `[hive] db`, `[hive] secrets_dir` and its own source path
            are read.

    Returns:
        A HiveState over the resolved paths and this machine's interface addresses.
    """
    hive = manifest.hive
    return HiveState.of(
        db=manifest.resolve_path(hive.db),
        secrets_dir=manifest.resolve_path(hive.secrets_dir),
        manifest=manifest.source_path.resolve() if manifest.source_path is not None else None,
        own_addresses=interface_addresses(),
    )


def interface_addresses() -> tuple[IPAddress, ...]:
    """Return every IP address this machine's own network interfaces answer on.

    Returns:
        Each interface's IPv4 and IPv6 addresses, zone ids dropped; empty when the interfaces
        cannot be listed (module docstring: this never fails the Hive's start).
    """
    try:
        interfaces = psutil.net_if_addrs()
    except OSError:
        return ()  # An unlistable machine names no own address; loopback stays refused anyway.
    found = {
        literal
        for entries in interfaces.values()
        for entry in entries
        if entry.family in _IP_FAMILIES and (literal := ip_literal(entry.address)) is not None
    }
    return tuple(sorted(found, key=lambda address: (address.version, int(address))))
