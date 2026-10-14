"""Build the WatchDeps a colonized device's Warden watches its own machine with.

Watch mode (ADR-0054) is what every Real Cell's Warden does instead of sitting idle: with no bees
working it looks at the machine read-only, keeps what it saw in its Bee Bread (labelled C2: it
describes the operator's own machine), and on a schedule reviews that in one awake episode, the
Patrol. A Level 0 device is watched by the Warden on the Hive Stand, which asks the device's packet
(`hivemind.swarm.watch`); the Warden that lives on a colonized device (Level 1 and 2, roadmap step
11.10) is local to the machine, so it looks the way the Hive Stand's own Warden does, through a
session and fixed read-only scripts (`SessionWatchProbe`). The difference is the session. This
Warden gives its lease back whenever it has nothing to supervise (so the machine keeps no scratch
between tasks), and a lease opened for each look would put an open and a release on its trail every
few minutes for as long as the device is idle; so it looks through an `ObserverSession`, which
belongs to no lease and can run a command and do nothing else, standing in its own state
directory. The numbers it watches by (how often it looks, how long it keeps what it saw, how often
it reviews that, when a process or the disk is worth a note) are the device manifest's
`[supervision.watch]`; the directories it reads logs and file changes from are the operator's,
named for this device (`hive swarm watch`) and handed over by the packet, held to what the
device's access level lets a Warden read.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.borrowed`. Called by
    `hivemind.cli.in_cell.borrowed.deps`. Calls into `hivemind.cell.local` (the observer session),
    `hivemind.cli.compose.deps.watch` (the settings and the Observation's device id, which the Hive
    Stand's Warden uses too), `hivemind.swarm.enrolment` (what an access level lets a Warden read),
    `hivemind.wardens.watch` and waggle only.

Key invariants:
    - Nothing here writes to the device: the probe's only session is an `ObserverSession`, which
      refuses every call but running a command, and the observations go to the Warden's own
      stores under its state directory.
    - The directories the Warden reads are the operator's, less any the device's own granted
      capabilities do not cover (the rule the packet and the Queen apply: `readable_under`).
    - Which probe the Warden has depends on where it runs, never on what kind of Cell it watches.
    - The Observation's device id is derived from the Cell's id, as for the Hive Stand's own
      Warden: nothing downstream of the probe reads it, so it need not be the registry's.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for watch mode and its amendment
      for a device's own Warden.
    - hivemind.cell.local.observer for the session this looks through.
    - hivemind.cli.compose.deps.watch for the Hive Stand's and Level 0 devices' counterparts.
"""

from __future__ import annotations

from hivemind.cell.local import ObserverSession
from hivemind.cli.compose.deps.watch import device_id_of_cell, watch_settings
from hivemind.cli.in_cell.borrowed.config import BorrowedConfig
from hivemind.swarm.enrolment import granted_capabilities, readable_under
from hivemind.wardens.watch import SessionWatchProbe, WatchDeps
from waggle.clock import Clock

__all__ = ["device_watch", "readable_watch_roots"]


def readable_watch_roots(config: BorrowedConfig) -> tuple[str, ...]:
    """Return the directories the Warden may read: those it was handed that its level allows.

    Args:
        config: The device Warden's configuration.

    Returns:
        The directories of `config.watch_roots` that the capabilities for the device's access
        level cover, in order. The Queen applies the same rule before she sends them, so this
        drops nothing she sent; it is the Warden holding itself to what it may read.
    """
    granted = granted_capabilities(config.cell.access_level, config.runtime.spawn_config.cell_id)
    return readable_under(granted, config.watch_roots)


def device_watch(config: BorrowedConfig, clock: Clock) -> WatchDeps:
    """Build the WatchDeps of a colonized device's Warden: it looks at its own machine.

    Args:
        config: The device Warden's configuration.
        clock: Stamps every observation and times every command the observer session runs.

    Returns:
        WatchDeps over a `SessionWatchProbe` that holds an `ObserverSession` standing in the
        state directory, with the device manifest's numbers and the directories it may read.
    """
    probe = SessionWatchProbe(device_id_of_cell(config.runtime.spawn_config.cell_id), clock)
    # Held for the Warden's whole life and never lent by a lease (`bind_session` stays None): the
    # Warden's own session comes and goes with each task, and this one looks even between them.
    probe.use_session(ObserverSession(config.state_dir, clock))
    return WatchDeps(
        probe=probe, settings=watch_settings(config.watch, readable_watch_roots(config))
    )
