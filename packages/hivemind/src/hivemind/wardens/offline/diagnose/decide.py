"""Decide the search for a lost link: when to send a Drone, what it found, and how it all ended.

A Warden (the always-on supervisor of one Cell, a unit of compute) that is cut off from the Queen
(the Hive's central orchestrator) and holds a model of its own sends a short-lived Drone to find
out why, inside a budget carved out of the offline limit (roadmap 11.9a, ADR-0056). This module is
every judgement that search makes, as pure functions of data so each is a table to test. Whether
there is a model to send it with (`local_binding`): a `[llm.slots]` key that serves the Drone's
slot, whose whole fallback chain runs on this machine, and whose model a server of this Warden's
is serving right now. What a Drone said (`read_finding`): by convention its closing text opens with
one line, `CAUSE: <the cause>` or `CAUSE: none found`. What to do next (`decide_move`): wait, send
a Drone, collect one that has ended, stop one that has run out of time, or finish. And how it ended
(`decide_verdict`), judged by the link and not by the Drone's word: FIXED when the link is back
and a cause was named, DIAGNOSED when a cause is known and the link is still down, NO_CAUSE
otherwise.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline.diagnose sub-package. Called by `hivemind.wardens.offline.diagnose.step` and `.drone`.
    Calls into `hivemind.forage` (slots and bindings), `hivemind.wardens.offline.outage` (the
    budget) and the standard library only.

Key invariants:
    - Pure: no I/O and no clock; the same facts always give the same answer.
    - A Drone is judged by whether the link returned, never by what it claimed: a Drone that says
      it fixed the link while it is still down has diagnosed it at best.
    - A named cause that did not bring the link back ends the search; a Drone that named none is
      followed by another while attempts and time remain.
    - The search never outlasts its budget: past `wall_clock_s` the only moves are to stop and
      to finish.

See Also:
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for the decision.
    - hivemind.wardens.offline.diagnose.step for the tick that applies these moves.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from hivemind.forage import ModelSlot, ModelSource, slot_for_binding
from hivemind.forage.map import SlotBinding
from hivemind.wardens.offline.outage import ReconnectBudget

CAUSE_PREFIX = "cause"  # The word a finding's first line opens with, before its colon.
# What a Drone writes after the colon when it found nothing; compared in lower case, no full stop.
NO_CAUSE_WORDS = frozenset({"none", "none found", "no cause found", "not found", "unknown"})
# How long, once a Drone has named a cause, the Warden waits for the link to come back before it
# says the cause is known but not fixed: the redial schedule's longest wait (30 s) and its dial.
VERIFY_S = 45.0
# How long a Drone may go on, once the link is back, to write up what it did before it is stopped:
# its slot is wanted by the tasks the Warden resumes.
LINGER_S = 30.0

__all__ = [
    "CAUSE_PREFIX",
    "LINGER_S",
    "NO_CAUSE_WORDS",
    "VERIFY_S",
    "Finding",
    "LocalBinding",
    "Move",
    "Phase",
    "Snapshot",
    "Verdict",
    "decide_move",
    "decide_verdict",
    "local_binding",
    "read_finding",
]


class Verdict(Enum):
    """How a search for a lost link ended; the outcome `warden.diagnosed` records."""

    FIXED = "FIXED"  # The link is back and a Drone named a cause.
    DIAGNOSED = "DIAGNOSED"  # A cause is known and the link is still down.
    NO_CAUSE = "NO_CAUSE"  # No cause was found, or the link came back with no Drone's help.


class Phase(Enum):
    """Where a search is: nothing in flight, a Drone working, a cause waiting to be proved, over."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    OVER = "OVER"


class Move(Enum):
    """What the Warden does next in a search."""

    WAIT = "WAIT"  # Nothing is due: look again next tick.
    START = "START"  # Send a Drone.
    COLLECT = "COLLECT"  # A Drone has ended: read what it said and retire it.
    STOP = "STOP"  # A Drone has run out of time: stop it, then finish.
    FINISH = "FINISH"  # Judge the search and record it.


@dataclass(frozen=True, slots=True)
class Finding:
    """What one Drone reported: the cause it named, if any, and everything it wrote."""

    cause: str | None
    text: str


@dataclass(frozen=True, slots=True)
class LocalBinding:
    """The model a Drone runs on: the `[llm.slots]` key, and the loaded source behind it."""

    key: str
    source: ModelSource


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The facts `decide_move` reads, taken once per tick (codingrules 5.1's argument group).

    Attributes:
        phase: Where the search is.
        attempts: How many Drones have been sent.
        budget: What the search may spend.
        elapsed_s: Seconds since the link went.
        link_up_for_s: Seconds the link has been back, or None while it is down.
        ended: A Drone is in flight and has said how it ended.
        verifying_s: Seconds since a Drone named a cause, while VERIFYING.
    """

    phase: Phase
    attempts: int
    budget: ReconnectBudget
    elapsed_s: float
    link_up_for_s: float | None
    ended: bool
    verifying_s: float


def read_finding(text: str) -> Finding:
    """Read a Drone's closing text for the cause it named.

    Args:
        text: What the Drone ended with.

    Returns:
        The finding: its cause when the first non-empty line is `CAUSE: <something>` and that
        something is not one of the ways of saying nothing was found; no cause otherwise.
    """
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    head, _, rest = first.partition(":")
    cause = rest.strip()
    named = head.strip().lower() == CAUSE_PREFIX and cause.lower().rstrip(".") not in NO_CAUSE_WORDS
    return Finding(cause=cause if named and cause else None, text=text)


def decide_move(facts: Snapshot) -> Move:
    """Return what the search does next.

    Args:
        facts: The search's state this tick.

    Returns:
        The move; applying it and asking again settles a tick, until it is WAIT.
    """
    over = facts.elapsed_s >= facts.budget.wall_clock_s
    back = facts.link_up_for_s is not None
    if facts.phase is Phase.RUNNING:
        if facts.ended:
            return Move.COLLECT
        lingered = facts.link_up_for_s is not None and facts.link_up_for_s >= LINGER_S
        return Move.STOP if over or lingered else Move.WAIT
    if facts.phase is Phase.VERIFYING:
        done = back or over or facts.verifying_s >= VERIFY_S
        return Move.FINISH if done else Move.WAIT
    if facts.phase is Phase.IDLE:
        done = back or over or facts.attempts >= facts.budget.attempts
        return Move.FINISH if done else Move.START
    return Move.WAIT  # OVER: decided and recorded, nothing more to do.


def decide_verdict(*, link_up: bool, cause: str | None) -> tuple[Verdict, str]:
    """Judge a search by the link and the cause, and give the stable word for why.

    Args:
        link_up: Whether the link to the Queen is back.
        cause: The cause a Drone named, if any.

    Returns:
        The verdict, and `link_back`, `link_returned`, `cause_known` or `no_cause`.
    """
    if link_up:
        # A link that returned with no cause named came back by itself: no Drone is thanked.
        return (Verdict.FIXED, "link_back") if cause else (Verdict.NO_CAUSE, "link_returned")
    if cause:
        return Verdict.DIAGNOSED, "cause_known"
    return Verdict.NO_CAUSE, "no_cause"


def local_binding(
    bindings: Sequence[SlotBinding],
    loaded: Sequence[ModelSource],
    is_local: Callable[[str], bool],
) -> LocalBinding | None:
    """Find the model a Drone can run on from this Warden's own pool, if there is one.

    Args:
        bindings: Every `[llm.slots]` row, in manifest order.
        loaded: The sources this Warden's servers are serving now.
        is_local: Whether a key's whole fallback chain runs on this machine.

    Returns:
        The first key that serves the WORKER slot (a Drone's own), runs entirely locally, and
        names a provider and model one of the loaded sources serves; None when there is none, which
        is what a Warden with no model of its own looks like.
    """
    served = {(source.spec.provider, source.spec.model): source for source in loaded}
    for row in bindings:
        source = served.get((row.provider, row.model))
        if source is None or not is_local(row.key):
            continue
        if slot_for_binding(row.key, bindings) is ModelSlot.WORKER:
            return LocalBinding(row.key, source)
    return None
