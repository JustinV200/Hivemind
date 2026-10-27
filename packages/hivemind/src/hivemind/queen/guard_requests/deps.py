"""Define GuardDeps: the Queen's collaborators for Guard requests and Cell isolation, in one field.

Roadmap step 10.6a (ADR-0043) gives the Queen a table of durable Guard requests, a rule for the
dire patterns `[guard] dire_patterns` names, a seam that cuts a Virtual Cell's egress when she
isolates it, and a bound on how long isolation waits for the Cell's bees to acknowledge their
pause. `GuardDeps` holds those four together so `QueenDeps` gains one defaulted field rather than
four (codingrules 5.1: a frozen dataclass for an argument group): a Queen built without naming it
takes requests into an in-memory table, applies the shipped dire patterns, records that a Cell's
egress stayed as it was, and waits `DEFAULT_PAUSE_TIMEOUT_S` for acknowledgements. The composition
root replaces it with the SQLite table on the Hive's own file, the manifest's patterns and the
`hivemind.hive.LifecycleEgress` over the Hive's `CellLifecycle`. `show_lock` serialises showing
the human a report (`hivemind.queen.guard_requests.show`), so the Guard Bee's `report_to_human`
and her own tick never both show the same report. Roadmap step 13.4a adds the Sting Cut slice:
the dire patterns she cuts a Cell off on after isolating it (`[guard.sting_cut]`), her hourly
cap, and the Virtual side's `CellCut` seam (None in a Hive with no Virtual side).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package. Carried on `hivemind.queen.deps.QueenDeps.guard`; built by
    `hivemind.cli.compose.guard.build_guard_deps`. Calls into `hivemind.hive` (CellEgress),
    `hivemind.manifest` (the default patterns) and the sub-package's own stores only.

Key invariants:
    - Frozen and slotted like `QueenDeps`; the store inside it is the one mutable collaborator.
    - `pause_timeout_s` is never negative; zero means "send the levers and do not wait".

See Also:
    - hivemind.queen.deps for QueenDeps, which carries this bundle.
    - hivemind.queen.isolation for the path that reads `egress` and `pause_timeout_s`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hivemind.hive import CellEgress
from hivemind.manifest import (
    DEFAULT_DIRE_PATTERNS,
    DEFAULT_MAX_AUTONOMOUS_STING_CUTS_PER_HOUR,
    DEFAULT_STING_CUT_PATTERNS,
)
from hivemind.queen.guard_requests.memory import InMemoryGuardRequestStore
from hivemind.queen.guard_requests.protocol import GuardRequestStore

if TYPE_CHECKING:
    # Type only: the sting_cut sub-package imports this one, so it may not be imported back here.
    from hivemind.queen.sting_cut import CellCut

# A bee acknowledges a pause in well under a second on the Hive Stand, and within one trail sync
# from a Virtual Cell; five seconds bounds the Queen's tick well inside the liveness window
# (three missed five-second heartbeats) while giving a slow Cell a real chance to answer.
DEFAULT_PAUSE_TIMEOUT_S = 5.0

__all__ = ["DEFAULT_PAUSE_TIMEOUT_S", "GuardDeps"]


@dataclass(frozen=True, slots=True)
class GuardDeps:
    """What the Queen needs to take Guard requests and to isolate a Cell.

    Attributes:
        requests: The durable table her door files into and her tick drains.
        dire_patterns: The Guard Bee rule keys she acts on by autopilot rule (`[guard]
            dire_patterns`); every other request is judged by an awake episode.
        egress: Cuts and restores a Virtual Cell's egress through its backend; None (a Hive with
            no Virtual side) records every isolation's egress as untouched.
        pause_timeout_s: How long isolation waits for the Cell's bees to acknowledge their pause
            before it pauses their tasks anyway.
        show_lock: Held while a report's SECURITY Alarm is checked and raised, one at a time.
        sting_cut_patterns: The rule keys on which she Sting Cuts a Cell after isolating it.
        max_sting_cuts_per_hour: The most Cells she cuts on her own in an hour; 0 cuts none.
        cut: The Virtual side's Sting Cut seam; None (no Virtual side) leaves every Cell Real.
    """

    requests: GuardRequestStore = field(default_factory=InMemoryGuardRequestStore)
    dire_patterns: frozenset[str] = frozenset(DEFAULT_DIRE_PATTERNS)
    egress: CellEgress | None = None
    pause_timeout_s: float = DEFAULT_PAUSE_TIMEOUT_S
    show_lock: asyncio.Lock = field(default_factory=asyncio.Lock, compare=False)
    sting_cut_patterns: frozenset[str] = frozenset(DEFAULT_STING_CUT_PATTERNS)
    max_sting_cuts_per_hour: int = DEFAULT_MAX_AUTONOMOUS_STING_CUTS_PER_HOUR
    cut: CellCut | None = None

    def __post_init__(self) -> None:
        """Refuse a negative pause bound, which would read as "already timed out" forever."""
        if self.pause_timeout_s < 0:
            raise ValueError(f"pause_timeout_s must be >= 0, got {self.pause_timeout_s}.")
        if self.max_sting_cuts_per_hour < 0:
            raise ValueError(
                f"max_sting_cuts_per_hour must be >= 0: {self.max_sting_cuts_per_hour}"
            )
