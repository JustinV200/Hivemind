"""Define Investigation: the one record of a Warden's search for why its link to the Queen went.

A search (roadmap 11.9a) belongs to one outage and outlives it by a moment, so the Warden keeps it
beside its `Outage` rather than inside the tick that drives it: which phase it is in, how many
Drones it has sent, the Drone in flight, what each one found, and when the link was first seen
back. The tick that owns the `Outage` is the only task that changes it, level-triggered on every
pass, so it needs no lock and a restart of the tick loses nothing it could not look at again.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline.diagnose sub-package. Held by `hivemind.wardens.offline.outage.Outage` (as a type
    only) and written by `.step` and `.drone`. Calls into `hivemind.wardens.spawn` (LocalRun),
    `.decide` and waggle only.

Key invariants:
    - `cut_since` names the outage the search belongs to; a search whose `cut_since` is not the
      outage's is left over from an earlier one and is closed before a new one starts.
    - Owned by one Warden's tick: nothing else reads or writes it.
    - A search that is OVER has recorded its verdict, or never sent a Drone; it is never begun
      again for the same outage.

See Also:
    - hivemind.wardens.offline.diagnose.decide for the phases and the moves between them.
    - hivemind.wardens.offline.outage for the `Outage` that holds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hivemind.wardens.offline.diagnose.decide import Finding, Phase
from hivemind.wardens.spawn.sub_bee import LocalRun
from waggle.ids import WorkerId

__all__ = ["Investigation"]


@dataclass(slots=True)
class Investigation:
    """One outage's search for why the link went, and everything it has learned so far.

    Owns mutable state in place (codingrules 8.5, documented): the tick of the Warden that holds
    the `Outage` is the only task that changes it.

    Attributes:
        cut_since: When the link went: the start of the outage, and of the budget's clock.
        phase: Where the search is.
        attempts: How many Drones have been sent.
        run: The Drone in flight, as its row keeps it; None when none is.
        worker_id: The Drone in flight's id, to find its row by.
        findings: What each Drone that ended reported, in order.
        link_up_since: When the link was first seen back, or None while it has not been.
        verifying_since: When a Drone named a cause and the Warden began waiting for the link.
        starved: Whether a Drone was wanted and every slot was busy when it was; a search that ends
            with no Drone sent says so only if this is set.
    """

    cut_since: datetime
    phase: Phase = Phase.IDLE
    attempts: int = 0
    run: LocalRun | None = None
    worker_id: WorkerId | None = None
    findings: list[Finding] = field(default_factory=list)
    link_up_since: datetime | None = None
    verifying_since: datetime | None = None
    starved: bool = False

    @property
    def cause(self) -> str | None:
        """The cause a Drone named, or None when none has (the latest one wins)."""
        return next((f.cause for f in reversed(self.findings) if f.cause is not None), None)
