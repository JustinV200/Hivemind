"""Compose and run the in-Cell Warden: the composition root the base-ubuntu image's ENTRYPOINT runs.

Roadmap step 5.5's own entry point. `main()` is what `images/base-ubuntu/Dockerfile`'s ENTRYPOINT
invokes (via the `hivemind-in-cell` console script, `packages/hivemind/pyproject.toml`): read
`os.environ` exactly once here (codingrules section 13), turn it into an `InCellRuntimeConfig`
(`hivemind.cli.in_cell.config`), build a `hivemind.wardens.spawn.in_cell.InCellSpawnSource` and
probe this Cell's own one `Cell` from it, wire a signed `WebSocketClientTransport` to the Queen's
Waggle URL, and run a `hivemind.cli.in_cell.link.CellLink` until it stops. `run_in_cell_warden` is
the async half, taking an already-read environment mapping and an injected `Clock` so a test drives
the whole sequence without touching the real environment or a real timer.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cell.source` (CellIdentity), `hivemind.cli.in_cell.config`/`.link`,
    `hivemind.cli.version`, `hivemind.common.logging`, `hivemind.manifest.env` (read_in_cell_env),
    `hivemind.pheromone.trail.memory` (this Cell's own local trail segment) and
    `hivemind.wardens.spawn` (InCellSpawnSource) only.

Key invariants:
    - `os.environ` is read exactly once, in `main()`; `run_in_cell_warden` and everything it calls
      take an already-read mapping (codingrules section 13).
    - `main()` never returns while `run_in_cell_warden` is running; it returns once `CellLink` has
      stopped (a Shutdown or CellTeardownRequest arrived, or the link ended) and its transport is
      closed.

See Also:
    - .claude/roadmap.md step 5.5 for this entry point's own description.
    - images/base-ubuntu/README.md for the image layer that runs this module.
    - hivemind.cli.in_cell.config for InCellRuntimeConfig, this module's own composition input.
    - hivemind.cli.in_cell.link for CellLink/CellLinkDeps, what this module builds and runs.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping

from hivemind.cell.source import CellIdentity
from hivemind.cli.in_cell.config import build_runtime_config
from hivemind.cli.in_cell.link import CellLink, CellLinkDeps
from hivemind.cli.version import collect_version_info
from hivemind.common.logging import configure_logging, get_logger
from hivemind.manifest.env import read_in_cell_env
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.wardens.spawn import InCellSpawnSource
from waggle.clock import Clock, SystemClock
from waggle.codec import Codec
from waggle.transport.websocket_client import WebSocketClientTransport

__all__ = ["main", "run_in_cell_warden"]

_LOG = get_logger(__name__)


async def run_in_cell_warden(environ: Mapping[str, str], clock: Clock) -> None:
    """Read `environ`, connect this Cell to the Queen, and run until Shutdown/CellTeardownRequest.

    Args:
        environ: The process's own environment mapping, read exactly once here (already read by
            the caller, never `os.environ` itself -- codingrules section 13).
        clock: Injected time source for every id minted, every sleep and every timestamp.
    """
    config = build_runtime_config(read_in_cell_env(environ), clock)
    codec = Codec(signer=config.signer, verifier=config.verifier)
    transport = WebSocketClientTransport(config.queen_waggle_url, codec, clock)
    # This Cell's own local Pheromone Trail segment (codingrules section 12: "A Warden that is
    # offline writes to its local segment; on reconnection the segment merges into the Queen's
    # trail"): the merge itself is a later roadmap step (5.4/5.6's own Queen-side work), so this
    # is where `cell.leased`/`cell.released` land once a real Warden leases through `source` below.
    trail = MemoryPheromoneTrail(clock)
    identity = CellIdentity(
        hive_id=config.hive_id, node_id=config.node_id, actor=str(config.warden_id)
    )
    source = InCellSpawnSource(config.spawn_config, identity, trail, clock)
    cell = (await source.cells())[0]

    link = CellLink(
        CellLinkDeps(
            transport=transport,
            cell=cell,
            warden_id=config.warden_id,
            hive_id=config.hive_id,
            node_id=config.node_id,
            clock=clock,
            heartbeat_interval_s=config.heartbeat_interval_s,
            runtime_version=collect_version_info().hivemind_version,
        )
    )
    _LOG.info("cell.link.connecting", queen_waggle_url=config.queen_waggle_url, cell_id=cell.id)
    await link.announce()
    _LOG.info("cell.link.announced", cell_id=cell.id, warden_id=config.warden_id)
    try:
        await link.run()
    finally:
        await link.close()
        _LOG.info("cell.link.stopped", cell_id=cell.id)


_DEFAULT_LOG_LEVEL = "INFO"  # Used when HIVEMIND_LOG_LEVEL is not set.


def main() -> None:
    """The image's ENTRYPOINT: build and run the in-Cell Warden until it stops.

    `os.environ` itself is never read outside `hivemind.manifest.env` (codingrules section 13):
    this function reads it exactly once, through `read_in_cell_env`, and passes the raw mapping
    on to `run_in_cell_warden` (which reads it again, the same sanctioned way, to build the rest
    of its config) rather than threading an already-parsed `InCellEnv` through two call shapes.
    """
    log_level = read_in_cell_env(os.environ).log_level or _DEFAULT_LOG_LEVEL
    # json_output=True: a container's stdout is normally scraped by the platform's own log
    # collector, never a human's terminal (unlike the operator-facing `hive` CLI commands).
    configure_logging(json_output=True, level=log_level)
    asyncio.run(run_in_cell_warden(os.environ, SystemClock()))


if __name__ == "__main__":
    main()
