"""Define the shared labels and value models that ride on messages of more than one family.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and its
messages are grouped into families, one module each under ``waggle.messages``. A closed set or a
small value that several families carry would force one family module to import another, so it
lives here instead and the family files stay independent of each other. The enums are the wire
forms of labels the rest of the Hive reasons with: ``HoneyClearance`` (the data-sensitivity
label every memory tier and every message that quotes data carries), ``AccessLevel`` (how much
of a Real Cell, an existing device borrowed for a task and left exactly as found, the Hive may
touch), ``CombShieldLevel`` (a Cell's security tier), ``AlarmSeverity`` (how bad an Alarm, an
issue a bee escalates because it cannot resolve it, is), ``Urgency`` (whether a stop may
checkpoint first), ``AccuracyBar`` and ``OsFamily``, plus ``PostconditionKind``, the assertions
the Capping gate (the quality gate that checks every side effect before it lands) can verify.
The value models are ``Tempo`` (a task's speed-against-accuracy setting), ``Postcondition`` (one
such assertion, or one acceptance criterion a planner attaches to a task), ``HandoffRef`` (a
pointer to a Handoff, the document a bee writes before its context is reset) and
``PlannedLeaving`` (roadmap step 5.0b: one path a task's plan declares should stay on its Cell
after the lease is released -- the same shape rides on a ``TaskDraft``, a stored ``Task`` and a
``task.assign``). The four host and capability report models of the same spec section live in
``waggle.messages.reports``, split out by responsibility so each file stays under the codingrules
5.1 size limit. ``hivemind`` mirrors these enums (``cell.tiers``, ``forage.tempo``) and a test
there keeps them in sync.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by the family modules under waggle.messages
    that carry these labels (task, supervision, cell, capping, control, ...) and by
    waggle.messages.reports; calls into waggle.messages.base only.

Key invariants:
    - No family module is imported here, so no family ever imports another through this file.
    - HoneyClearance and AccessLevel are totally ordered by declaration, and ``rank`` is the
      only thing a validator compares; member values are wire strings, never compared as such.
    - Every value model is frozen and forbids extras through VALUE_MODEL_CONFIG, exactly like a
      message, but none subclasses WaggleMessage, so none can ever be registered as a kind.
    - ``PlannedLeaving.pattern`` is always absolute or ``~``-rooted, never a bare root or drive,
      and never carries a ``..`` segment (its own validator); a plan-time rule on top of that
      (a pattern may not fall inside a Cell's own scratch) needs the manifest's own
      ``[hive_stand] scratch_root`` and so is not checked here -- see
      ``hivemind.queen.planner.schema.PlannedTask`` for that rule.

See Also:
    - docs/waggle/spec.md section 8.1 for the normative list of members, fields and bounds.
    - waggle.messages.reports for PlatformReport, CellCapabilitiesReport, GpuReport and
      HostCapacityReport, the rest of spec section 8.1.
    - waggle.messages.base for VALUE_MODEL_CONFIG, the shared bounds and the field aliases.
    - .claude/roadmap.md step 5.0b for "the plan declares what stays".
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from waggle.messages.base import (
    MAX_PATH_CHARS,
    MAX_REASON_CHARS,
    VALUE_MODEL_CONFIG,
    EventIdField,
    UtcDatetime,
)

MIN_SUBJECT_CHARS = 1  # An assertion is always about something; an empty subject checks nothing.
MAX_ARGV_ITEMS = 32  # A program plus its flags; a longer command line belongs in a script file.
MAX_ARGV_ITEM_CHARS = 1_024  # One argument; a path (MAX_PATH_CHARS) is the only thing longer.
MAX_EXPECTED_CHARS = 4_000  # An HTTP status, an element's text or a judge's rubric, never a page.
# A Windows drive root, either slash style ("C:\" or "C:/"): the anchor PlannedLeaving recognises
# alongside a POSIX "/" root, a UNC "\\\\" root, and a "~" home.
_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")

__all__ = [
    "MAX_ARGV_ITEMS",
    "MAX_ARGV_ITEM_CHARS",
    "MAX_EXPECTED_CHARS",
    "MIN_SUBJECT_CHARS",
    "AccessLevel",
    "AccuracyBar",
    "AlarmSeverity",
    "CombShieldLevel",
    "HandoffRef",
    "HoneyClearance",
    "OsFamily",
    "PlannedLeaving",
    "Postcondition",
    "PostconditionKind",
    "Tempo",
    "Urgency",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class HoneyClearance(Enum):
    """The data-sensitivity label every memory tier and every message that quotes data carries.

    Totally ordered C0 < C1 < C2 by declaration; compare ``rank``, never the values. Any detail
    about the operator or a Real Cell, a first name or a habit included, is C2.
    """

    C0 = "C0"  # Wildflower: public.
    C1 = "C1"  # Apiary: internal, non-personal.
    C2 = "C2"  # Royal: personal or sensitive; anything describing the operator or a Real Cell.

    @property
    def rank(self) -> int:
        """Position in the total order C0 < C1 < C2, for validators that compare clearances.

        Returns:
            0 for C0, 1 for C1, 2 for C2; a higher rank is more sensitive.
        """
        # Declaration order is the total order, so a member's index is its rank; comparing ranks
        # rather than values leaves the wire strings free to be whatever reads best.
        return list(HoneyClearance).index(self)


class AccessLevel(Enum):
    """How much of a Real Cell the Hive may touch; a Virtual Cell is always FULL.

    Totally ordered READ_ONLY < SCRATCH < FULL by declaration; compare ``rank``, never the
    values. Caps every capability set granted for that Cell.
    """

    READ_ONLY = "READ_ONLY"  # Observe only; nothing is written anywhere on the Cell.
    SCRATCH = "SCRATCH"  # Writes stay inside the Hive's own scratch directory.
    FULL = "FULL"  # The whole Cell, within the tier's other controls.

    @property
    def rank(self) -> int:
        """Position in the total order READ_ONLY < SCRATCH < FULL, for validators to compare.

        Returns:
            0 for READ_ONLY, 1 for SCRATCH, 2 for FULL; a higher rank permits more.
        """
        # Same rule as HoneyClearance.rank: declaration order is the total order.
        return list(AccessLevel).index(self)


class CombShieldLevel(Enum):
    """A Cell's security tier; a Real Cell is never NIGHT_VEIL (that tier is virtual-only)."""

    MEADOW = "MEADOW"  # Tier 0: the baseline controls.
    PROPOLIS = "PROPOLIS"  # Tier 1: VPN-only networking.
    NIGHT_VEIL = "NIGHT_VEIL"  # Tier 2: virtual-only, VPN plus Tor, teardown-only.


class AlarmSeverity(Enum):
    """How bad an Alarm (an issue a bee escalates because it cannot resolve it) is."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Urgency(Enum):
    """Whether a stop may checkpoint first; used by cell.teardown_request and control.shutdown."""

    GRACEFUL = "GRACEFUL"  # Bees may checkpoint and release their leases before stopping.
    IMMEDIATE = "IMMEDIATE"  # Kill now, no checkpoint: Sting Cut (per-Cell) and Absconding (all).


class AccuracyBar(Enum):
    """The accuracy half of a Tempo: the minimum model grade and how much checking a task gets."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class OsFamily(Enum):
    """The operating-system family a device or Cell runs; a new member later is a minor bump."""

    LINUX = "LINUX"
    WINDOWS = "WINDOWS"
    MACOS = "MACOS"


class PostconditionKind(Enum):
    """What a Postcondition asserts: the Capping gate's machine checks plus the judge's rubric."""

    FILE_EXISTS = "FILE_EXISTS"
    FILE_ABSENT = "FILE_ABSENT"
    COMMAND_EXITS_ZERO = "COMMAND_EXITS_ZERO"
    TEST_PASSES = "TEST_PASSES"
    HTTP_STATUS = "HTTP_STATUS"
    ELEMENT_TEXT = "ELEMENT_TEXT"
    JUDGE_RUBRIC = "JUDGE_RUBRIC"  # What no machine can check; a judge model scores it.


# The kinds whose check runs a command, so they are the only ones that carry an argv.
_COMMAND_KINDS = frozenset({PostconditionKind.COMMAND_EXITS_ZERO, PostconditionKind.TEST_PASSES})
# The kinds whose check compares against a stated value, so `expected` is required for them.
_COMPARISON_KINDS = frozenset(
    {PostconditionKind.HTTP_STATUS, PostconditionKind.ELEMENT_TEXT, PostconditionKind.JUDGE_RUBRIC}
)


# ──────────────────────────────────────────────────────────────────────────────
# Value models
# ──────────────────────────────────────────────────────────────────────────────


class Tempo(BaseModel):
    """A task's speed-against-accuracy setting, as it travels on the wire.

    Crosses the wire on task and Forage messages; in hivemind, ``forage.tempo.Tempo`` mirrors it
    and the Attendant (inbox triage), routing, Forage allocation and Capping read it. It never
    overrides safety.
    """

    model_config = VALUE_MODEL_CONFIG

    latency_budget_s: Annotated[float, Field(gt=0)] | None = Field(
        description="The latency the task can tolerate, in seconds; None means no budget. "
        "Greater than 0 when set."
    )
    accuracy: AccuracyBar = Field(
        description="The accuracy bar; sets the minimum model grade and how much checking the "
        "task gets."
    )


class Postcondition(BaseModel):
    """One assertion a bee states before acting, or one acceptance criterion a planner sets.

    The same shape serves both task.assign (acceptance criteria) and
    capping.proposal_submitted (postconditions), so the Capping gate checks them alike.
    """

    model_config = VALUE_MODEL_CONFIG

    kind: PostconditionKind = Field(description="What is asserted.")
    subject: str = Field(
        min_length=MIN_SUBJECT_CHARS,
        max_length=MAX_PATH_CHARS,
        description="The path, URL, selector or test id the assertion is about, or a short "
        "label for JUDGE_RUBRIC.",
    )
    argv: tuple[Annotated[str, Field(max_length=MAX_ARGV_ITEM_CHARS)], ...] = Field(
        max_length=MAX_ARGV_ITEMS,
        description="The command as an argument list, never a shell string; non-empty only for "
        "COMMAND_EXITS_ZERO and TEST_PASSES.",
    )
    expected: Annotated[str, Field(max_length=MAX_EXPECTED_CHARS)] | None = Field(
        description="The HTTP status, the element text or the rubric text; None for kinds with "
        "nothing to compare. Required for HTTP_STATUS, ELEMENT_TEXT and JUDGE_RUBRIC."
    )

    @model_validator(mode="after")
    def _argv_only_for_command_kinds(self) -> Postcondition:
        """Reject an argv on a kind whose check runs no command."""
        # A command line on a FILE_EXISTS check would never run, so its presence means the sender
        # confused two assertions; refusing it keeps the gate's inputs unambiguous.
        if self.argv and self.kind not in _COMMAND_KINDS:
            raise ValueError(
                f"A {self.kind.value} postcondition carries no argv; only COMMAND_EXITS_ZERO "
                "and TEST_PASSES run a command."
            )
        return self

    @model_validator(mode="after")
    def _expected_required_for_comparison_kinds(self) -> Postcondition:
        """Reject a comparison kind with nothing to compare against."""
        # HTTP_STATUS, ELEMENT_TEXT and JUDGE_RUBRIC are meaningless without the value or rubric
        # the checker compares to, so None is refused for them and allowed everywhere else.
        if self.kind in _COMPARISON_KINDS and self.expected is None:
            raise ValueError(
                f"A {self.kind.value} postcondition requires `expected`: the value or rubric "
                "its check compares against."
            )
        return self


class HandoffRef(BaseModel):
    """A reference to a Handoff (the document a bee writes before its context is reset).

    No Handoff id kind exists, so the reference is the trail event that recorded it, plus what a
    bee must know before fetching it: when it was written and its clearance.
    """

    model_config = VALUE_MODEL_CONFIG

    event_id: EventIdField = Field(
        description="The memory.checkpoint trail event, the lookup key in Bee Bread (the warm "
        "memory tier)."
    )
    written_at: UtcDatetime = Field(description="When the Handoff was written.")
    clearance: HoneyClearance = Field(
        description="The Handoff's label, visible before it is fetched so a bee never resumes "
        "from a Handoff above its own clearance."
    )


class PlannedLeaving(BaseModel):
    """One path a task's plan declares should stay on its Cell once the lease is released.

    Roadmap step 5.0b: declared by the plan, decided by policy, asked of the human only when
    policy says so (later steps; a bee never decides it alone). The same shape rides on a
    TaskDraft, a stored Task and a task.assign, unchanged, so what a Drone is told matches
    exactly what the plan itself declared -- a Drone cannot widen it, only raise a Question.
    """

    model_config = VALUE_MODEL_CONFIG

    pattern: str = Field(
        min_length=1,
        max_length=MAX_PATH_CHARS,
        description="An absolute (/..., C:\\..., \\\\server\\share\\...) or ~-rooted path naming "
        "what must remain; never a bare root, drive or home, and never a `..` segment (validator).",
    )
    reason: str = Field(
        min_length=1,
        max_length=MAX_REASON_CHARS,
        description="One line: why the goal itself needs this path to remain.",
    )

    @model_validator(mode="after")
    def _pattern_is_a_well_formed_whole_path(self) -> PlannedLeaving:
        """Reject a relative pattern, a bare root/drive/home, or any `..` segment."""
        parts = _leaving_pattern_parts(self.pattern)
        if parts is None:
            raise ValueError(
                f"PlannedLeaving.pattern {self.pattern!r} must be absolute (starting with `/`, "
                "a drive like `C:\\`, or `\\\\server\\share`) or `~`-rooted."
            )
        if not parts:
            # Empty means nothing survives past the root/drive/home itself: the whole tree.
            raise ValueError(
                f"PlannedLeaving.pattern {self.pattern!r} names a bare root, drive or home "
                "directory; a plan may only declare specific paths, never a whole tree."
            )
        if any(char in parts[0] for char in "*?["):
            # `~/*` or `C:\**` is the bare home or drive again, spelt as a glob: it would declare
            # (and make writable) the whole tree the rule above exists to refuse.
            raise ValueError(
                f"PlannedLeaving.pattern {self.pattern!r} starts with a wildcard segment; name "
                "at least one real directory or file before any `*`."
            )
        if ".." in parts:
            raise ValueError(
                f"PlannedLeaving.pattern {self.pattern!r} may not contain a `..` segment."
            )
        return self


def _leaving_pattern_parts(pattern: str) -> tuple[str, ...] | None:
    """Split `pattern` into its segments after the root/drive/home, or None if not rooted.

    Uses PureWindowsPath/PurePosixPath, never the host's Path (codingrules section 5): a wire
    model must validate a Windows-shaped pattern on a POSIX Hive Stand and vice versa. Neither
    flavour resolves `..` on its own, so it survives as an ordinary part for the caller to see
    and refuse.

    Args:
        pattern: The candidate `PlannedLeaving.pattern` text, already stripped of surrounding
            whitespace by the caller's own model field.

    Returns:
        The parts after the root/drive/home (empty for a bare one), or None when `pattern` is
        neither absolute nor `~`-rooted.
    """
    stripped = pattern.strip()
    if stripped.startswith("~"):
        rest = stripped[1:]
        if rest and rest[0] not in "\\/":
            return None  # "~bob/foo" names another user's home; not supported.
        # PureWindowsPath splits both slash styles alike; a POSIX-written "~/x" parses the same.
        return PureWindowsPath(rest).parts[1:] if rest else ()
    if _DRIVE_PATTERN.match(stripped) or stripped.startswith("\\\\"):
        path = PureWindowsPath(stripped)
        return path.parts[1:] if path.is_absolute() else None
    if stripped.startswith("/"):
        return PurePosixPath(stripped).parts[1:]
    return None  # A relative pattern names nothing fixed enough to leave in place.
