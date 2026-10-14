"""The in-Cell Warden entry point: what `images/base-ubuntu`'s ENTRYPOINT runs (roadmap step 5.3).

A Virtual Cell exposes no inbound port (codingrules section 15), so the Warden that owns it cannot
be reached -- it has to reach out. This package is the composition root that makes that connection
real, and runs a genuine task-executing Warden over it: `config` turns the container's `HIVEMIND_*`
environment (read once, in `hivemind.manifest.env`, never here) into typed ids, Ed25519 keys and a
probed Cell description, and carries `rewrite_loopback_base_url`/`gateway_host` for pointing a
loopback-addressed LLM provider at the Hive Stand instead; `link` is the small module that dials the
Queen's Waggle URL and sends the three frames that must go out before a Warden exists at all -- a
signed `CellReady`, a `CapacityReport`, then one `CellHeartbeat` (so `hivemind.queen.cell_gate.
listener.CellListener`'s own readiness gate resolves); `deps` composes a real `hivemind.wardens.
warden.WardenDeps` around that same transport, `hivemind.wardens.spawn.in_cell.InCellSpawnSource`
and a per-Cell Pheromone Trail segment that a `hivemind.wardens.trail_sync.WaggleTrailSync` ships
back to the Queen; `providers` builds that Warden's own model door (the operator's own provider
table when the Queen shipped one, else a scriptable fake); `fanner` builds the Cell's own Fanner,
the seat meter every model call in the Cell passes through, recording each `llm.call` to the Cell's
own trail segment so it reaches the Queen with the rest; `main` wires all of it together as
`main()`, the console-script target `packages/hivemind/pyproject.toml` registers and the
Dockerfile's ENTRYPOINT invokes: announce, then build and run a real `hivemind.wardens.warden.
Warden` until a Queen-sent `Shutdown`/`CellTeardownRequest` stops it.
Roadmap step 11.6a: with `HIVEMIND_CELL_LEASE=borrowed` the same entry point runs the Warden of a
colonized Swarm device instead (`borrowed`, a sub-package): the Warden the Hive Stand ran for the
device at Level 0, now on the device, leasing the machine itself and keeping its memory and trail
in SQLite files so a Handoff survives a restart. A Virtual Cell's path is the default, unchanged.
Roadmap step 10.3a: the Cell's tier comes from its bootstrap (`HIVEMIND_COMB_SHIELD`, validated
against its link by `config`), a Night Veil Cell dials the Queen's onion service only through
the Tor SOCKS proxy the bootstrap names and attests that link on `CellReady` (`link`), a Propolis
Cell (accepted now that the Hive attests its image, docs/adr/0057) dials the ordinary endpoint
and reports the tunnel checks it makes of itself on `CellReady` (`propolis`), and
`hive_stand` names the Hive Stand as this Cell reaches it (its host, and the addresses that host
resolved to once at start; an onion is never resolved) for the Warden's floors.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). The one composition root for a Virtual Cell's own
    process; nothing above it. Calls into `hivemind.cell`, `hivemind.common`, `hivemind.manifest`,
    `hivemind.pheromone`, `hivemind.llm` (the Fanner), `hivemind.wardens` (Warden, WardenDeps,
    spawn, trail_sync) and waggle only.

Key invariants:
    - `os.environ` is read exactly once per process start (`hivemind.cli.in_cell.main.main`),
      always through `hivemind.manifest.env.read_in_cell_env` (codingrules section 13).
    - Signing is mandatory: `hivemind.cli.in_cell.config.build_runtime_config` refuses to start
      without both this Cell's own signing key and the Queen's verify key (roadmap step 1.7).
    - The first frame this process ever sends is `CellReady`, unconditionally before anything
      else touches the transport (ADR-0027; `hivemind.queen.cell_gate.listener.CellListener`
      accepts nothing else first).

See Also:
    - .claude/roadmap.md step 5.3 for this package's own roadmap bullet.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the connection
      direction and the "signed CellReady first" order this package implements.
    - images/base-ubuntu/README.md for the image layer that runs `main()`.
    - hivemind.wardens.spawn.in_cell for InCellSpawnSource, the `in_cell` Warden spawn strategy
      this package's own composition root builds and probes.
    - hivemind.queen.trail.sync for TrailSegmentReceiver, the Queen-side half of the trail sync
      this package's Warden sends (`hivemind.queen.cell_gate.listener.CellListener` hands it
      every chunk).

Public API:
    - InCellRuntimeConfig, build_runtime_config, rewrite_loopback_base_url, gateway_host: turn
      InCellEnv into typed config, and rewrite a loopback provider URL for a Virtual Cell (config).
    - CellLinkDeps, announce, send_cell_ready, send_capacity_report, send_cell_heartbeat: the
      three frames sent before a Warden exists (link), and again on each redial of a device's
      Warden; control_link_attestation, a Night Veil Cell's link checks.
    - hive_stand_addresses, hive_stand_names: the Hive Stand as this Cell reaches it
      (hive_stand).
    - build_in_cell_warden_deps: compose a real WardenDeps for this Cell (deps).
    - build_in_cell_provider_registry: this Warden's own model door (providers).
    - build_in_cell_fanner: this Cell's own Fanner, every model call's seat meter (fanner).
    - main, run_in_cell_warden: the console-script entry point and its async body (main).
    - The `borrowed` sub-package (roadmap step 11.6a): `run_borrowed_warden`,
      `build_borrowed_config` and the rest of a colonized device's Warden; its face lists them.
"""

from hivemind.cli.in_cell.config import (
    InCellRuntimeConfig,
    build_runtime_config,
    gateway_host,
    rewrite_loopback_base_url,
)
from hivemind.cli.in_cell.deps import build_in_cell_warden_deps
from hivemind.cli.in_cell.fanner import build_in_cell_fanner
from hivemind.cli.in_cell.link import (
    CellLinkDeps,
    announce,
    send_capacity_report,
    send_cell_heartbeat,
    send_cell_ready,
)
from hivemind.cli.in_cell.main import main, run_in_cell_warden
from hivemind.cli.in_cell.providers import build_in_cell_provider_registry

__all__ = [
    "CellLinkDeps",
    "InCellRuntimeConfig",
    "announce",
    "build_in_cell_fanner",
    "build_in_cell_provider_registry",
    "build_in_cell_warden_deps",
    "build_runtime_config",
    "gateway_host",
    "main",
    "rewrite_loopback_base_url",
    "run_in_cell_warden",
    "send_capacity_report",
    "send_cell_heartbeat",
    "send_cell_ready",
]
