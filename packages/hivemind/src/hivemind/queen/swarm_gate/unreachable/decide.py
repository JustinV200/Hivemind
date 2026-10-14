"""Define Verdict and the pure rules of the hold: when the grace ends, and what a lost device gets.

When a Swarm device is lost the Queen holds its tasks before she judges it (ADR-0053), and what she
decides depends on what the device can do alone. A device at Level 0 has its Warden on the Hive
Stand and only its hands on the device, so with the link gone the work cannot continue: its tasks
are re-placed. A device at Level 1 carries its Warden, but that Warden has no model of its own and
has already clustered its bees: re-placed too. A Nuc carries a model server and keeps thinking on
its own pool, finishing what it started, and its results will arrive in its outbox when it is back:
re-placing its tasks the moment it drops would put two Cells on the same files, so they keep
waiting, up to `[supervision] max_offline_s`, after which it is treated like the others. The two
timings are pure comparisons of how long the hold has lasted against a limit; they live here so
the table of verdicts and the edges of the two limits are tested row by row, with no clock.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.unreachable`. Called by `.devices` when it weighs a hold. Calls into
    `hivemind.swarm` (the runtime ladder's rungs) only.

Key invariants:
    - Pure: no I/O, no clock; the same level and durations give the same answer.
    - Every rung has a verdict; a rung added to the ladder without one fails at import.
    - A limit is reached at its own value, not after it: a grace of 30 s ends at 30 s.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the decision this encodes.
    - hivemind.queen.swarm_gate.unreachable.devices for the hold that applies it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.swarm import RuntimeLevel

__all__ = ["Verdict", "grace_over", "offline_over", "verdict_for"]


class Verdict(Enum):
    """What the Queen does with a lost device's tasks once the grace has ended."""

    REPLACE = "replace"  # Take the tasks back and place them afresh, from their last Handoff.
    WAIT = "wait"  # Keep holding them: the device is still working on its own pool.


# One verdict per rung (codingrules section 9's one-table rule): the Nuc is the only rung that
# keeps working with no link, so it is the only one whose tasks wait.
_VERDICTS: Mapping[RuntimeLevel, Verdict] = {
    RuntimeLevel.GATEWAY: Verdict.REPLACE,  # Its hands are cut off from a Warden on the Hive Stand.
    RuntimeLevel.COLONIZED: Verdict.REPLACE,  # Its Warden has no model and has clustered its bees.
    RuntimeLevel.NUC: Verdict.WAIT,  # Its model server keeps its sub-bees working.
}


def verdict_for(level: RuntimeLevel) -> Verdict:
    """Return what the Queen does with the tasks of a device on `level` when its grace has ended.

    Args:
        level: The rung the lost device is on.

    Returns:
        REPLACE for a Level 0 or Level 1 device, WAIT for a Nuc.
    """
    return _VERDICTS[level]


def grace_over(held_s: float, grace_s: float) -> bool:
    """Whether a hold that has lasted `held_s` seconds has used up its grace of `grace_s`."""
    return held_s >= grace_s


def offline_over(held_s: float, max_offline_s: float) -> bool:
    """Whether a Nuc out of reach for `held_s` seconds has outlasted `max_offline_s`."""
    return held_s >= max_offline_s
