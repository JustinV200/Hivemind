"""Define WatchProbe: how a watching Warden looks at its Cell, read-only, and nothing more.

A Warden (the per-Cell supervisor) with no bees working watches its Cell (a unit of compute, here
a Real Cell: an existing machine the Hive borrows) instead of sitting idle (ADR-0054). What it
looks at comes from a `WatchProbe`, which answers one question: what does the Cell look like now?
The answer is always a `waggle.messages.swarm.Observation`, the one snapshot type every probe
returns, whether the Warden runs beside the Cell (`SessionWatchProbe`, through the Warden's own
session) or on the Hive Stand with only a Pollen Packet on the device (`hivemind.swarm.watch.
PollenWatchProbe`, which asks the packet). The two never share code; they share this contract,
which the packet's does by structure alone (a Protocol, so `hivemind.swarm`, a lower layer, never
imports the Warden that uses it).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Implemented by `hivemind.wardens.watch.probe.session.SessionWatchProbe` and
    `hivemind.swarm.watch.PollenWatchProbe`; called by `hivemind.wardens.watch.observer`. Calls
    into waggle's swarm messages and the error roots only.

Key invariants:
    - A probe never writes: not to the Cell, not to a lease, not to a file. What it runs is a fixed
      list in code, never text from a request, a model or a caller.
    - A probe reports what its Cell's access level allows to be watched and nothing else: the
      caller passes only the kinds `hivemind.guard.watch_permits` allows, and a probe collects
      only the kinds it is asked for.
    - A probe that cannot look (no session, a platform it has no collector for) returns an
      Observation with nothing in it and says why in its `reason`; it does not open a lease.
    - `PROBE_FAILURES` is every error a probe may raise on purpose; the observer catches those and
      nothing broader.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for what may be observed.
    - hivemind.guard.watch for the observations each access level permits.
    - waggle.messages.swarm.watch for Observation, the snapshot every probe returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pydantic import ValidationError

from hivemind.common.errors import HiveMindError
from waggle.errors import WaggleError
from waggle.messages.swarm import Observation, ObservationKind

# Everything a probe may raise on purpose: a Cell or swarm error (a closed session, a timed-out
# command, an unreachable device), a wire error, a snapshot that breaks its own bounds, a timeout
# or an operating-system error. Nothing broader: any other exception is a bug and must surface.
PROBE_FAILURES: tuple[type[Exception], ...] = (
    HiveMindError,
    WaggleError,
    ValidationError,
    TimeoutError,
    OSError,
)

__all__ = ["PROBE_FAILURES", "WatchProbe"]


class WatchProbe(Protocol):
    """Look at one Cell read-only and return what it looks like as an Observation."""

    async def observe(
        self, kinds: Sequence[ObservationKind], roots: Sequence[str], since_at: datetime | None
    ) -> Observation:
        """Take one observation of the Cell.

        Args:
            kinds: What to report; at least one, each at most once. The caller has already
                narrowed them to what the Cell's access level allows.
            roots: Absolute directories to read log files from and to watch for file changes;
                non-empty exactly when `kinds` includes logs or file changes.
            since_at: The file-change cursor: report only changes after this instant. None asks
                for the probe's own recent window.

        Returns:
            The Observation; a kind the probe could not collect is left empty and the `reason`
            names it. Everything in it describes the operator's machine (`C2`).

        Raises:
            Exception: Any of `PROBE_FAILURES`: the Cell or device could not be reached, a command
                timed out, or the snapshot broke its bounds.
        """
        ...
