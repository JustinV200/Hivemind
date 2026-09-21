"""Define PathClass, LeaveVerdict, LeaveRequest and LeaveCellFacts: the leave policy's own data.

Roadmap step 5.0c: `decide(request, cell, declared) -> ALLOW | ASK | DENY`. `PathClass` is one of
the four buckets a leaving's path falls into (`keep_root`, `home`, `system` or `startup`, plus
`other` for a path this Hive Stand's classifier does not recognise as any of the three named
locations -- a conservative catch-all, never treated more permissively than `system`);
`LeaveVerdict` is the policy's own three-way answer. `LeaveRequest` bundles the one path's own facts
(its class, size and whether it looks executable) and `LeaveCellFacts` the Cell's own already-known
facts (`hivemind.cell.tiers.AccessLevel`, `hivemind.cell.tiers.CombShieldLevel`, and whether this is
the Hive Stand rather than a borrowed device) -- both plain frozen dataclasses (codingrules section
8.5), never pydantic models, because neither ever crosses a JSON or wire boundary; they are built
fresh, in-process, by whoever calls `decide` (`hivemind.supervision.capping.apply`, once this
package's 5.0c dispatch wires it in). `LeaveHumanVerdict` is roadmap step 5.0d's own addition: the
human's closed answer (keep / keep for this whole goal / discard) once an ASK verdict is actually
raised as a Question, read by `hivemind.supervision.capping.checks.human.HumanCheck` and carried on
`hivemind.supervision.capping.leave.persist.LeaveDecisionRecord.human_answer`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    leave`. Read and built by `hivemind.supervision.capping.leave.classify` (produces `PathClass`),
    `.policy` (`decide`, the pure function these models feed) and `hivemind.supervision.capping.
    apply` (builds `LeaveRequest`/`LeaveCellFacts` from a proposal and its Cell). Calls into
    `hivemind.cell` (AccessLevel, CombShieldLevel) only.

Key invariants:
    - `LeaveRequest.path` is always the resolved, absolute path as text, host-native separators,
      the same string `hivemind.supervision.capping.leave.classify.classify_path` was given to
      produce `path_class` -- `decide` itself never re-parses it, only echoes it onto the trail.
    - `LeaveCellFacts` never carries `cell.kind`: only `AccessLevel`, `CombShieldLevel` and
      `is_hive_stand`, matching roadmap step 5.0c's own "reads capabilities and levels, never
      `cell.kind`."

See Also:
    - .claude/roadmap.md step 5.0c for the policy's own inputs, verbatim.
    - hivemind.supervision.capping.leave.policy for `decide`, the pure function these feed.
    - hivemind.supervision.capping.leave.table for `LeavePolicyTable`, decide's fourth input.
    - hivemind.cell.tiers for AccessLevel and CombShieldLevel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hivemind.cell import AccessLevel, CombShieldLevel

__all__ = ["LeaveCellFacts", "LeaveHumanVerdict", "LeaveRequest", "LeaveVerdict", "PathClass"]


class PathClass(Enum):
    """Which of the four named locations (or neither) a leaving's resolved path falls into."""

    KEEP_ROOT = "KEEP_ROOT"  # Under the manifest's [hive_stand] keep_root (roadmap step 5.0e).
    HOME = "HOME"  # Under the Cell's own home directory, and none of the three below.
    STARTUP = "STARTUP"  # A Windows Startup folder, a systemd unit dir, a shell rc file, etc.
    SYSTEM = "SYSTEM"  # /etc, /usr, /bin, C:\\Windows, C:\\Program Files*, and the like.
    OTHER = "OTHER"  # Neither home, system, startup nor keep_root; treated as conservatively
    # as SYSTEM (roadmap step 5.0c does not name this bucket, so it is this dispatch's own
    # conservative default -- see the leave package README's "Design choices" section).


class LeaveVerdict(Enum):
    """The leave policy's own three-way answer for one path."""

    ALLOW = "ALLOW"  # persist=True, approved_by=POLICY -- no one is asked.
    ASK = "ASK"  # Roadmap step 5.0d: raise a Question and block on it.
    DENY = "DENY"  # persist=False -- the write still applies, but is restored on release.


class LeaveHumanVerdict(Enum):
    """The human's own closed answer to an ASK-verdict leaving (roadmap step 5.0d).

    `hivemind.supervision.capping.checks.human.HumanCheck` raises a Question with exactly these
    three options, in this order (`KEEP_OPTION`, `KEEP_FOR_GOAL_OPTION`, `DISCARD_OPTION` there
    match `Answer.chosen_option`'s index into them onto this enum's own declaration order).
    """

    KEEP = "KEEP"  # Persist this one path; approved_by=HUMAN.
    KEEP_FOR_GOAL = "KEEP_FOR_GOAL"  # Persist this and every later leaving for this goal+Cell.
    DISCARD = "DISCARD"  # Do not persist; also what an unanswered or non-HUMAN answer means.


@dataclass(frozen=True, slots=True)
class LeaveRequest:
    """One leaving candidate's own already-known facts, per roadmap step 5.0c's input list."""

    path: str  # The resolved, absolute path, as text (see module docstring's own invariant).
    path_class: PathClass  # From hivemind.supervision.capping.leave.classify.classify_path.
    size: int  # Bytes the content about to be written at `path` will occupy.
    is_executable: bool  # From hivemind.supervision.capping.leave.executable.looks_executable.


@dataclass(frozen=True, slots=True)
class LeaveCellFacts:
    """The Cell's own already-known facts `decide` reads; never `cell.kind` (module docstring)."""

    access_level: AccessLevel  # READ_ONLY and SCRATCH always DENY.
    comb_shield: CombShieldLevel  # NIGHT_VEIL always DENY.
    is_hive_stand: bool  # True for the Hive Stand itself, False for a borrowed device.
