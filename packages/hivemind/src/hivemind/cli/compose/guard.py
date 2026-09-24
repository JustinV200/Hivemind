"""Build the Hive's Guard policy from a manifest, with the Hive's own state named on it.

Roadmap step 10.3 builds the Guard policy from `[guard]` (its policy file, or the shipped one,
overlaid); ADR-0033 adds what no bee may touch or reach, whatever its set says: the Hive's own
state. `hive_state` names it from the manifest and this machine: the resolved `[hive] db` file
(with its SQLite siblings), the resolved `[hive] secrets_dir` and everything under it, the
manifest file itself, and every address this machine's own interfaces answer on (the Hive
Stand's own addresses), so the Hive-state floor refuses a bee `fs:read`/`fs:write` on any of them
and `net` to any of them. `guard_policy` is the one policy the Hive's Enforcer (`build_enforcer`,
shared by the Queen and the Hive Stand's Warden) decides against. Roadmap step 10.6a:
`build_guard_deps` builds the Queen's side of a Guard request (`QueenDeps.guard`): her durable
request table on the `[hive] db` file, `[guard] dire_patterns`, and the egress seam isolation cuts
a Virtual Cell's network through (the Hive's own `CellLifecycle`, when it has a Virtual side).
Roadmap step 10.6: `with_guard` gives the Queen that side and the Guard Bee (the Hive's security
watcher, run on her tick) together. The Guard Bee files through her own door, and she is built
after it (it rides on her deps), so it is handed a `GuardDoorRelay` the composition root binds to
her the moment she exists, before anything ticks; `close_guard_bee` cancels an awake episode still
in flight once she has stopped. Its model calls go through her own call gate, a Fanner lane with
no grant: they take the Royal Reserve's seats, are recorded as `llm.call` on the judge slot with
no grant id, and never draw on a Worker's grant.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.build_hive` (through `hivemind.cli.compose.deps`, which re-exports
    `build_enforcer`). Calls into `hivemind.cell` (CellIdentity), `hivemind.guard` (the policy
    loader, `Enforcer`, `HiveState`, the address helpers), `hivemind.manifest`,
    `hivemind.pheromone`, `hivemind.hive` (CellLifecycle, LifecycleEgress), `hivemind.queen.
    guard_requests` (GuardDeps), `hivemind.queen` (QueenDeps), `hivemind.supervision.capping`
    (TierTable), `hivemind.workers.roles.guard_bee` (build_guard_bee), `hivemind.cli.stores`
    (open_guard_requests), `psutil` (this machine's interface addresses; the standard library
    cannot list them portably), waggle and the standard library only.

Key invariants:
    - Reading the interfaces never fails the Hive's start: a machine whose interfaces cannot be
      listed names no own address, and the loopback forms stay refused regardless.
    - Every path is resolved against the manifest's own directory, exactly as the stores that
      open them resolve it, so the floor names the very files the Hive writes.
    - A `GuardDoorRelay` forwards every call to the one door it was bound to, and refuses a call
      made before it was bound rather than drop a request.

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
from hivemind.cli.stores import open_guard_requests
from hivemind.guard import (
    Enforcer,
    GuardPolicy,
    GuardReport,
    GuardRequestDoor,
    load_guard_policy,
)
from hivemind.guard.net import IPAddress, ip_literal
from hivemind.guard.policy import HiveState
from hivemind.hive import LifecycleEgress
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.manifest import HiveManifest
from hivemind.pheromone import PheromoneTrail
from hivemind.queen import QueenDeps
from hivemind.queen.guard_requests import GuardDeps
from hivemind.supervision.capping import TierTable
from hivemind.workers.roles.guard_bee import GuardBeeInputs, build_guard_bee
from waggle.clock import Clock

# The address families an interface can be reached on over IP; link-layer entries are not.
_IP_FAMILIES = frozenset({socket.AF_INET, socket.AF_INET6})

__all__ = [
    "GuardDoorRelay",
    "build_enforcer",
    "build_guard_deps",
    "close_guard_bee",
    "guard_policy",
    "hive_state",
    "interface_addresses",
    "with_guard",
]


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


def build_guard_deps(manifest: HiveManifest, lifecycle: CellLifecycle | None) -> GuardDeps:
    """Build the Queen's side of a Guard request from the manifest (roadmap step 10.6a).

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`; `[hive] db` and
            `[guard] dire_patterns` are read.
        lifecycle: The Hive's Virtual Cell lifecycle, when `[virtual_cells] backend` is set; None
            leaves isolation with no egress to cut (every Cell is then Real, left as found).

    Returns:
        GuardDeps over a SQLite request table on the Hive's own file, durable across a restart.
    """
    egress = LifecycleEgress(lifecycle) if lifecycle is not None else None
    return GuardDeps(
        requests=open_guard_requests(manifest.resolve_path(manifest.hive.db)),
        dire_patterns=frozenset(manifest.guard.dire_patterns),
        egress=egress,
    )


class GuardDoorRelay:
    """The Queen's `GuardRequestDoor` for a Guard Bee built before her: bound once she exists."""

    def __init__(self) -> None:
        """Start unbound; the composition root binds the Queen before her first tick."""
        self._door: GuardRequestDoor | None = None

    def bind(self, door: GuardRequestDoor) -> None:
        """Forward every call to `door` (the running Queen) from now on."""
        self._door = door

    async def file_guard_request(self, report: GuardReport) -> None:
        """Forward to the Queen's door; see `GuardRequestDoor.file_guard_request`."""
        await self._bound().file_guard_request(report)

    async def report_to_human(self, report: GuardReport) -> None:
        """Forward to the Queen's door; see `GuardRequestDoor.report_to_human`."""
        await self._bound().report_to_human(report)

    def _bound(self) -> GuardRequestDoor:
        """Return the bound door; a call before binding is a composition bug, never dropped."""
        if self._door is None:
            raise RuntimeError("The Guard Bee called the Queen's door before she was bound to it.")
        return self._door


def with_guard(
    manifest: HiveManifest,
    queen_deps: QueenDeps,
    lifecycle: CellLifecycle | None,
    tiers: TierTable,
) -> tuple[QueenDeps, GuardDoorRelay]:
    """Give the Queen her side of a Guard request and the Guard Bee that files through it.

    Args:
        manifest: The loaded manifest; `[hive]` and `[guard]` are read.
        queen_deps: The Queen's collaborators, all but these two: the Guard Bee reads her trail,
            clock, Guard policy, slot resolver and call gate (the Royal Reserve's seats).
        lifecycle: The Hive's Virtual Cell lifecycle, when it has a Virtual side.
        tiers: The Hive Stand Warden's Capping tier table, the rates a raise starts from.

    Returns:
        `queen_deps` with `guard` and `guard_bee` set, and the door relay to bind to the Queen.
    """
    door = GuardDoorRelay()
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    inputs = GuardBeeInputs(
        trail=queen_deps.trail,
        clock=queen_deps.clock,
        identity=identity,
        door=door,
        guard=manifest.guard,
        tiers=tiers,
        policy=queen_deps.enforcer.policy,
        bound_for=queen_deps.bound_for,
        call_gate=queen_deps.call_gate,
    )
    guard = build_guard_deps(manifest, lifecycle)
    deps = dataclasses.replace(queen_deps, guard=guard, guard_bee=build_guard_bee(inputs))
    return deps, door


async def close_guard_bee(queen_deps: QueenDeps) -> None:
    """Cancel the Guard Bee's awake episode still in flight, once the Queen has stopped.

    Args:
        queen_deps: The stopped Queen's collaborators; nothing happens when she has no Guard Bee.
    """
    if queen_deps.guard_bee is not None:
        await queen_deps.guard_bee.aclose()


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
