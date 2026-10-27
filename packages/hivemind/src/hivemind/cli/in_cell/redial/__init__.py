"""Mend a Virtual Cell's link to the Queen when it drops; a Night Veil Cell's only through Tor.

A Virtual Cell's Waggle link to the Hive Stand can drop under it. A Night Veil Cell's is a Tor
circuit to her hidden service (codingrules 8.7), and Tor can die under it while the Cell's VPN and
default route stay up; the promise of the tier is that the Cell then does not fall back to a direct
or VPN-only connection: it is cut off, and clusters. And the Queen herself can die under any
Cell's link and come back (Requeening, ADR-0062), and the Cell's work must outlive her. So every
Virtual Cell's Warden is built over a mending link, the one a colonized device's Warden has
(`hivemind.wardens.offline`, ADR-0053). `dial` holds its two dialers (through the SOCKS proxy for
Night Veil, direct for every other tier), its announcer, which says who the Cell is on each new
connection, and the step that records a reconnection; `build` makes the link, connects it, and
gives the Warden an `Outage` and the Hive's offline limit so that it goes OFFLINE when the link
does and clusters fully past the limit.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), a sub-package of `hivemind.cli.in_cell`, which
    builds every Virtual Cell's Warden over it in `run_in_cell_warden`. Calls into
    `hivemind.cell`, `hivemind.cli.in_cell`, `hivemind.memory`, `hivemind.pheromone`,
    `hivemind.wardens` and waggle only.

Key invariants:
    - A Night Veil Cell's link reaches the Queen only through the SOCKS proxy its bootstrap names;
      no code path here dials it without one, and it holds no direct address to dial.
    - A Warden built over it is cut off when the link is, and its tasks are held at a Handoff, not
      lost; past `HIVEMIND_MAX_OFFLINE_S` it clusters fully.
    - Nothing here reads `os.environ`; it is given the already-built `InCellRuntimeConfig`.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the offline rules.
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for why every Cell's link mends.
    - .claude/codingrules.md sections 8.7 and 8.8 for the tier and the disconnected Warden.
    - hivemind.cli.in_cell.borrowed for the colonized device's counterpart.

Public API:
    - connect_redialing, RedialingLink, OUTBOX_DIR: the link and its deps (build).
    - dialer_for, ProxyDialer, DirectDialer, CellAnnouncer, AfterRedial: the link's
      collaborators (dial).
"""

from hivemind.cli.in_cell.redial.build import OUTBOX_DIR, RedialingLink, connect_redialing
from hivemind.cli.in_cell.redial.dial import (
    AfterRedial,
    CellAnnouncer,
    DirectDialer,
    ProxyDialer,
    dialer_for,
)

__all__ = [
    "OUTBOX_DIR",
    "AfterRedial",
    "CellAnnouncer",
    "DirectDialer",
    "ProxyDialer",
    "RedialingLink",
    "connect_redialing",
    "dialer_for",
]
