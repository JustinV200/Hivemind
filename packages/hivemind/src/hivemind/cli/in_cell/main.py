"""Compose and run the in-Cell Warden: the composition root the base-ubuntu image's ENTRYPOINT runs.

Roadmap step 5.5/5.3's own entry point. `main()` is what `images/base-ubuntu/Dockerfile`'s
ENTRYPOINT invokes (via the `hivemind-in-cell` console script, `packages/hivemind/pyproject.toml`):
read `os.environ` exactly once here (codingrules section 13), turn it into an `InCellRuntimeConfig`
(`hivemind.cli.in_cell.config`), build a `hivemind.wardens.spawn.in_cell.InCellSpawnSource` and
probe this Cell's own one `Cell` from it, wire a signed `WebSocketClientTransport` to the Queen's
Waggle URL, send the three frames that must go out before a `hivemind.wardens.warden.Warden`
exists at all (`hivemind.cli.in_cell.link`'s own `announce`/`send_capacity_report`/
`send_cell_heartbeat`, in that order -- `hivemind.queen.cell_gate.listener.CellListener`'s own
readiness gate waits for the first and last of those, unmodified by this dispatch), then build and
run a real, task-executing `Warden` over that same transport until it stops.
`run_in_cell_warden` is the async half, taking an already-read environment mapping and an injected
`Clock` so a test drives the whole sequence without touching the real environment or a real timer.
Roadmap step 10.3a: the transport dials through the SOCKS proxy the bootstrap names (a Night Veil
Cell's Tor SOCKS port; never directly once one is named), a Night Veil Cell's `CellReady` carries
its control-link checks, a Propolis Cell's the tunnel checks it makes of its own routes and
resolvers (`hivemind.cli.in_cell.propolis`), and the Hive Stand's addresses are resolved once,
before the Warden is built, so its floors know them (`hivemind.cli.in_cell.hive_stand`; an onion
host never is). A Night Veil Cell's link is one that mends (`hivemind.cli.in_cell.redial`): Tor can
die under it, and its Warden is then cut off and clusters past the Hive's offline limit, redialing
through the proxy until Tor answers, and never reaching the Hive Stand any other way.

Roadmap step 11.6a: with `HIVEMIND_CELL_LEASE=borrowed` the same entry point runs the Warden of a
colonized Swarm device instead (`hivemind.cli.in_cell.borrowed`); a Virtual Cell's path is the
default and unchanged.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cell.source` (CellIdentity), `hivemind.cli.in_cell.config`/`.deps`/`.link`,
    `hivemind.cli.in_cell.borrowed`, `hivemind.cli.in_cell.redial`,
    `hivemind.cli.version`, `hivemind.common.logging`, `hivemind.manifest.env` (read_in_cell_env),
    `hivemind.pheromone.trail.memory` (this Cell's own local trail segment),
    `hivemind.wardens.deps` (WardenDeps, `on_deps_built`'s own parameter type),
    `hivemind.wardens.spawn` (InCellSpawnSource), `hivemind.wardens.warden` (Warden) and
    `hivemind.wardens.state` (WardenState) only.

Key invariants:
    - `os.environ` is read exactly once, in `main()`; `run_in_cell_warden` and everything it calls
      take an already-read mapping (codingrules section 13).
    - `main()` never returns while `run_in_cell_warden` is running; it returns once the Warden has
      stopped (a Shutdown or CellTeardownRequest arrived and it stopped itself, or `run()` ended
      for any other reason) and its Queen transport is closed.
    - `Warden.stop()` is called at most once: a Queen-sent Shutdown/CellTeardownRequest already
      stops the Warden from inside its own tick (`hivemind.wardens.autopilot.table`'s `STOP`
      action, `hivemind.wardens.ticks.control.handle_stop`), so `run_in_cell_warden` only calls it
      again itself when `run()` ended some other way -- calling an already-stopped Warden's
      `stop()` a second time would re-release its lease and double-record `warden.stopped`.
    - Once the link is built it is closed on every way out of `run_in_cell_warden`, whatever
      became of the Warden's stop (it overran its grace, raised, or the Warden was cancelled
      before it ran its first tick): a Night Veil Cell's link mends itself, so one left open
      would go on redialing Tor for a Cell that no longer exists.
    - `on_deps_built`, when given, is called exactly once, with the freshly built `WardenDeps`,
      after `_connect_and_build` has already announced this Cell (`CellReady`/`CapacityReport`/
      `CellHeartbeat`) but strictly before `Warden.start()`/`.run()` -- so a caller can script
      `deps.bound.provider` (the same seam `hivemind.cli.in_cell.deps`'s own test module,
      `test_deps.py`, reaches by calling `build_in_cell_warden_deps` directly) without this
      function needing a global or environment-driven way to reach the in-Cell provider registry.

See Also:
    - .claude/roadmap.md step 5.5 for this entry point's own description.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for "the image's
      entry point starts a Warden, and the Warden dials out... sends a signed CellReady... then
      heartbeats" -- the sequence this module now runs for real.
    - images/base-ubuntu/README.md for the image layer that runs this module.
    - hivemind.cli.in_cell.config for InCellRuntimeConfig, this module's own composition input.
    - hivemind.cli.in_cell.link for announce/send_capacity_report/send_cell_heartbeat, the three
      frames this module sends before a Warden exists.
    - hivemind.cli.in_cell.deps for build_in_cell_warden_deps, what this module builds the Warden
      itself from.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
from collections.abc import Callable, Mapping

from hivemind.cell.source import CellIdentity
from hivemind.cell.tiers import CombShieldLevel
from hivemind.cli.in_cell import link as cell_link
from hivemind.cli.in_cell.borrowed import is_borrowed, run_borrowed_warden, stop_on_signals
from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.cli.in_cell.deps import build_in_cell_warden_deps
from hivemind.cli.in_cell.hive_stand import hive_stand_addresses
from hivemind.cli.in_cell.propolis import own_tunnel_attestation
from hivemind.cli.in_cell.redial import connect_redialing, redials
from hivemind.cli.version import collect_version_info
from hivemind.common.logging import configure_logging, get_logger
from hivemind.manifest.env import InCellEnv, read_in_cell_env
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.spawn import InCellSpawnSource
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden
from waggle.clock import Clock, SystemClock
from waggle.codec import Codec
from waggle.messages.cell.status import AttestationCheck
from waggle.transport.base import Transport
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport

__all__ = ["main", "run_in_cell_warden"]

_LOG = get_logger(__name__)


async def run_in_cell_warden(
    environ: Mapping[str, str],
    clock: Clock,
    *,
    on_deps_built: Callable[[WardenDeps], None] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Read `environ`, connect this Cell to the Queen, and run a real Warden until it stops.

    Args:
        environ: The process's own environment mapping, read exactly once here (already read by
            the caller, never `os.environ` itself -- codingrules section 13).
        clock: Injected time source for every id minted, every sleep and every timestamp.
        on_deps_built: Injected, never global (module docstring's own key invariant): called once
            with the freshly built `WardenDeps`, after this Cell has announced itself but before
            `Warden.start()`/`.run()`. `None` (the default, every production caller) skips it;
            an e2e test uses it to script `deps.bound.provider` (a `FakeLLMProvider`) the same
            way `hivemind.cli.in_cell.deps`'s own test module reaches it, without this module
            needing to expose a second, lower-level entry point.
        stop: Set to end a Warden that borrows its machine (roadmap step 11.6a), which the Pollen
            Packet withdraws with a signal: it stops, leaves the device as found and returns. A
            Virtual Cell's Warden ignores it, as its backend destroys the Cell instead.
    """
    env = read_in_cell_env(environ)
    # Roadmap step 11.6a: a Warden on a device the Hive borrows is composed in its own package;
    # everything below is a Virtual Cell's, exactly as it was.
    if is_borrowed(env):
        await run_borrowed_warden(env, clock, on_deps_built=on_deps_built, stop=stop)
        return
    config = build_runtime_config(env, clock)
    # Once, at start: the Hive Stand's addresses for this Cell's floors (never an onion's).
    config = dataclasses.replace(config, hive_stand_addresses=await hive_stand_addresses(config))
    transport, deps = await _connect_and_build(config, clock)
    warden = Warden(config.warden_id, deps)
    # From here the link is open, so every way out closes it, one that comes before the Warden has
    # run its first tick included: a Cell the Queen tears down at once (it failed its attestation)
    # is cancelled while its Warden is still starting.
    try:
        if on_deps_built is not None:
            on_deps_built(deps)
        await warden.start()
        _LOG.info("cell.warden.started", warden_id=config.warden_id)
        await warden.run()
    finally:
        await _shut_down(warden, transport)
        _LOG.info("cell.warden.stopped", warden_id=config.warden_id)


# How long a cancelled or failing in-Cell Warden may spend stopping sub-bees, releasing its lease
# and shipping its last trail segment, and then (as its own step) closing the link, before the
# Cell is abandoned to its backend's destroy. The backend destroys the Cell either way; this only
# keeps the process from hanging on a peer that has already gone (the Queen closed the socket
# first, say).
SHUTDOWN_GRACE_S = 3.0


async def _shut_down(warden: Warden, transport: Transport) -> None:
    """Stop `warden` (unless a Queen-sent Shutdown already did), then close the link, each bounded.

    Runs from `run_in_cell_warden`'s `finally`, so also on cancellation: everything here is
    best-effort against a peer that may already be gone, and `SHUTDOWN_GRACE_S` bounds each step
    so the task always completes its cancellation.
    """
    try:
        async with asyncio.timeout(SHUTDOWN_GRACE_S):
            if warden.state is not WardenState.STOPPED:
                # run() ended some other way (an exception propagating out, say) rather than
                # through a Queen-sent Shutdown/CellTeardownRequest, which already calls stop()
                # itself (module docstring's own invariant on why this is not unconditional).
                await warden.stop()
    except TimeoutError:
        _LOG.warning("cell.warden.shutdown_timed_out", grace_s=SHUTDOWN_GRACE_S)
    finally:
        # Whatever became of the stop (it ran out of grace, it raised, it was cancelled again) the
        # link is closed: a Night Veil Cell's link mends itself, so one left open goes on redialing
        # Tor for a Cell that no longer exists, and a bare transport left open holds the Queen's
        # end of its connection until the process dies. The Warden of a colonized device closes
        # its link the same way (`hivemind.cli.in_cell.borrowed.run`).
        await _close_link(transport)


async def _close_link(transport: Transport) -> None:
    """Close the Queen link within a grace of its own, one a slow stop could not have spent."""
    try:
        async with asyncio.timeout(SHUTDOWN_GRACE_S):
            await transport.close()
    except TimeoutError:
        _LOG.warning("cell.link.close_timed_out", grace_s=SHUTDOWN_GRACE_S)


def _spawn_source(
    config: InCellRuntimeConfig, trail: MemoryPheromoneTrail, clock: Clock
) -> InCellSpawnSource:
    """Build the one-Cell source this Warden leases its Cell from, recording to `trail`."""
    identity = CellIdentity(
        hive_id=config.hive_id, node_id=config.node_id, actor=str(config.warden_id)
    )
    return InCellSpawnSource(config.spawn_config, identity, trail, clock)


async def _connect_and_build(
    config: InCellRuntimeConfig, clock: Clock
) -> tuple[Transport, WardenDeps]:
    """Connect this Cell to the Queen, announce it, and build its Warden's deps over that link.

    Returns:
        The Warden's link to the Queen (the bare transport, or for a Night Veil Cell the link that
        mends) and the deps built over it.
    """
    # This Cell's own local Pheromone Trail segment (codingrules section 12: "A Warden that is
    # offline writes to its local segment; on reconnection the segment merges into the Queen's
    # trail"): `build_in_cell_warden_deps` wires a `WaggleTrailSync` that ships it over the link
    # on this Warden's own heartbeat cadence and once more from `stop()`.
    trail = MemoryPheromoneTrail(clock)
    source = _spawn_source(config, trail, clock)
    if not redials(config):
        transport = await _connect_and_announce(config, source, clock)
        return transport, build_in_cell_warden_deps(config, source, transport, trail, clock)
    # Roadmap step 10.3a: Tor can die under a Night Veil Cell's link, and the Warden must then go
    # cut off and cluster, never fall back to another route (`hivemind.cli.in_cell.redial`).
    redialing = await connect_redialing(config, source, trail, clock)
    deps = build_in_cell_warden_deps(config, source, redialing.link, trail, clock)
    return redialing.link, redialing.equip(deps)


async def _connect_and_announce(
    config: InCellRuntimeConfig, source: InCellSpawnSource, clock: Clock
) -> WebSocketClientTransport:
    """Dial the Queen and send CellReady, CapacityReport, then one CellHeartbeat (module docstring).

    Returns:
        This Cell's own connected transport.
    """
    codec = Codec(signer=config.signer, verifier=config.verifier)
    # The gateway carve-out matches build_runtime_config's own validation of this URL: from inside
    # a container, loopback is the Cell itself, so the Queen is reached through the host gateway.
    # Roadmap step 10.3a: with a SOCKS proxy named, every dial goes through it and none directly.
    transport = WebSocketClientTransport(
        config.queen_waggle_url,
        codec,
        clock,
        options=DialOptions(
            allow_virtual_cell_gateway_host=True, socks_proxy_url=config.socks_proxy_url
        ),
    )
    cell = (await source.cells())[0]

    link_deps = cell_link.CellLinkDeps(
        transport=transport,
        cell=cell,
        warden_id=config.warden_id,
        hive_id=config.hive_id,
        node_id=config.node_id,
        clock=clock,
        heartbeat_interval_s=config.heartbeat_interval_s,
        runtime_version=collect_version_info().hivemind_version,
        attestation=_attestation(cell.comb_shield, transport),
    )
    _LOG.info("cell.link.connecting", queen_waggle_url=config.queen_waggle_url, cell_id=cell.id)
    await cell_link.announce(link_deps)
    _LOG.info("cell.link.announced", cell_id=cell.id, warden_id=config.warden_id)
    await cell_link.send_capacity_report(link_deps, config.spawn_config.capacity)
    await cell_link.send_cell_heartbeat(link_deps)
    _LOG.info("cell.link.ready", cell_id=cell.id)
    return transport


def _attestation(
    tier: CombShieldLevel, transport: WebSocketClientTransport
) -> tuple[AttestationCheck, ...]:
    """Return the checks this Cell's CellReady carries, by its tier (CellReady's own rule)."""
    if tier is CombShieldLevel.NIGHT_VEIL:
        return cell_link.control_link_attestation(transport.uri, transport.socks_proxy)
    if tier is CombShieldLevel.PROPOLIS:
        # The tunnel checks, made of its own routes and resolvers: a tiered Cell with no checks
        # would not even construct its CellReady. The Hive's attestation decides readiness.
        return own_tunnel_attestation()
    return ()  # MEADOW attests nothing.


_DEFAULT_LOG_LEVEL = "INFO"  # Used when HIVEMIND_LOG_LEVEL is not set.


def main() -> None:
    """The image's ENTRYPOINT: build and run the in-Cell Warden until it stops.

    `os.environ` itself is never read outside `hivemind.manifest.env` (codingrules section 13):
    this function reads it exactly once, through `read_in_cell_env`, and passes the raw mapping
    on to `run_in_cell_warden` (which reads it again, the same sanctioned way, to build the rest
    of its config) rather than threading an already-parsed `InCellEnv` through two call shapes.
    """
    env = read_in_cell_env(os.environ)
    # json_output=True: a container's stdout is normally scraped by the platform's own log
    # collector, never a human's terminal (unlike the operator-facing `hive` CLI commands).
    configure_logging(json_output=True, level=env.log_level or _DEFAULT_LOG_LEVEL)
    asyncio.run(_run_process(os.environ, env))


async def _run_process(environ: Mapping[str, str], env: InCellEnv) -> None:
    """Run the Warden as this process: a borrowed one is ended by a signal, a Virtual Cell's not."""
    stop = asyncio.Event()
    # A Warden on a borrowed device must leave it as it was found, so the packet's SIGTERM is a
    # request to stop; a Virtual Cell's process is simply destroyed with its Cell, as it always was.
    handlers = (
        stop_on_signals(asyncio.get_running_loop(), stop)
        if is_borrowed(env)
        else contextlib.nullcontext()
    )
    with handlers:
        await run_in_cell_warden(environ, SystemClock(), stop=stop)


if __name__ == "__main__":
    main()
