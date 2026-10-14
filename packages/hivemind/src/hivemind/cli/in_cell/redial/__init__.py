"""Mend a Night Veil Cell's link to the Queen when Tor drops it, never by any other route.

A Night Veil Cell's Waggle link to the Hive Stand is a Tor circuit to her hidden service
(codingrules 8.7). Tor can die under it while the Cell's VPN and default route stay up, and the
promise of the tier is that the Cell then does not fall back to a direct or VPN-only connection:
it is cut off, and clusters. The bare transport every other Cell uses gives up when its connection
drops, so a Night Veil Cell's Warden is built over a mending link instead, the one a colonized
device's Warden has (`hivemind.wardens.offline`, ADR-0053). `dial` is its dialer, which can only
dial through the SOCKS proxy, its announcer, which says who the Cell is on each new connection, and
the step that records a reconnection; `build` makes the link, connects it, and gives the Warden an
`Outage` and the Hive's offline limit so that it goes OFFLINE when the link does and clusters fully
past the limit. Every other Cell is untouched by any of this.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), a sub-package of `hivemind.cli.in_cell`, which
    selects it in `run_in_cell_warden` for a Night Veil Cell. Calls into `hivemind.cell`,
    `hivemind.cli.in_cell`, `hivemind.memory`, `hivemind.pheromone`, `hivemind.wardens` and waggle
    only.

Key invariants:
    - The link reaches the Queen only through the SOCKS proxy the Cell's bootstrap names; there is
      no code path here that dials without it, and no direct address to dial.
    - A Warden built over it is cut off when the link is, and its tasks are held at a Handoff, not
      lost; past `HIVEMIND_MAX_OFFLINE_S` it clusters fully.
    - Nothing here reads `os.environ`; it is given the already-built `InCellRuntimeConfig`.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the offline rules.
    - .claude/codingrules.md sections 8.7 and 8.8 for the tier and the disconnected Warden.
    - hivemind.cli.in_cell.borrowed for the colonized device's counterpart.

Public API:
    - connect_redialing, redials, RedialingLink, OUTBOX_DIR: the link and its deps (build).
    - ProxyDialer, CellAnnouncer, AfterRedial: the link's collaborators (dial).
"""

from hivemind.cli.in_cell.redial.build import (
    OUTBOX_DIR,
    RedialingLink,
    connect_redialing,
    redials,
)
from hivemind.cli.in_cell.redial.dial import AfterRedial, CellAnnouncer, ProxyDialer

__all__ = [
    "OUTBOX_DIR",
    "AfterRedial",
    "CellAnnouncer",
    "ProxyDialer",
    "RedialingLink",
    "connect_redialing",
    "redials",
]
