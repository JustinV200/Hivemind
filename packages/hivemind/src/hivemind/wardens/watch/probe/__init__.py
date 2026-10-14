"""Provide the watch probes: how a watching Warden looks at its Cell, read-only.

A Warden (the per-Cell supervisor) with no bees working watches its Cell (a unit of compute)
instead of sitting idle (ADR-0054). What it looks at comes from a `WatchProbe`, which returns one
`waggle.messages.swarm.Observation`. `SessionWatchProbe` is the probe for a Warden that runs beside
its Cell: it looks through the Warden's own session with two fixed commands (`commands`), parsed by
`system` (processes and resources) and `files` (log lines and file changes). A Warden that runs on
the Hive Stand with only a Pollen Packet on its device uses `hivemind.swarm.PollenWatchProbe`
instead, which satisfies the same protocol by structure.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Called by `hivemind.wardens.watch.observer`; built by the composition roots
    (`hivemind.cli.compose`). Calls into `hivemind.cell` and waggle only.

Key invariants:
    - A probe never writes, never opens a lease, and runs only fixed commands.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for what may be observed.
    - hivemind.guard.watch for the observations each access level permits.

Public API (roadmap step 11.10):
    - PROBE_FAILURES, WatchProbe: the contract and the errors a probe raises on purpose (protocol).
    - SessionWatchProbe: the probe that looks through the Warden's own session (session).
"""

from hivemind.wardens.watch.probe.protocol import PROBE_FAILURES, WatchProbe
from hivemind.wardens.watch.probe.session import SessionWatchProbe

__all__ = ["PROBE_FAILURES", "SessionWatchProbe", "WatchProbe"]
