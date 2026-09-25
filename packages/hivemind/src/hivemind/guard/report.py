"""Define GuardReport: what the Guard Bee saw, and what it asks the Queen to do about it.

ADR-0043: the Guard Bee watches the Pheromone Trail from the Queen's process and acts alone only
to narrow the whole Hive (raise a Capping tier's sampled-audit rate, order the Entrance Reducer).
Anything aimed at one Cell or one bee (isolate a Cell, quarantine a bee, Sting Cut a Cell) is a
**request** the Queen decides on. Every report, acted on alone or filed as a request, carries the
same facts: the rule that fired, the trail events that made it fire, what it touches (the Cell,
the bees, their tasks and grants), the action it recommends and how sure the rule is. The facts
are ids, a rule key and a summary the rule writes from ids and counts, never content: the trail
carries no prompt text, so neither does a report built from it. `GuardRequestDoor` is the one
seam a request crosses to reach the Queen: she implements it in her own process (Layer 6), the
Guard Bee calls it (Layer 4), and neither imports the other.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard`. Built by the
    Guard Bee (roadmap step 10.6), read by the Queen's decision on a `GUARD_REQUEST` and by Cell
    isolation (roadmap step 10.6a), and shown to the human through a `SECURITY` Alarm. Calls into
    pydantic and waggle (the clock, ids and ULIDs) only.

Key invariants:
    - A report cites at least one trail event: no rule fires on nothing.
    - A request aimed at a Cell (`ISOLATE_CELL`, `STING_CUT`) names the Cell; `QUARANTINE_BEE`
      names a bee or a task. A report that only narrows the Hive, or only records, needs neither.
    - Every field is bounded, so a burst of events can never make a report unbounded.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md, "It acts alone
      only to narrow the whole Hive".
    - .claude/roadmap.md steps 10.6 (the Guard Bee) and 10.6a (Cell isolation).
"""

from __future__ import annotations

import secrets
from enum import Enum
from typing import Annotated, NewType, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waggle.clock import Clock
from waggle.messages.base import UtcDatetime
from waggle.ulid import RANDOMNESS_BYTES, encode_ulid

_ULID = "[0-9A-HJKMNP-TV-Z]{26}"  # Crockford base32, as every Hive id's ULID half is written.
GUARD_REPORT_ID_PREFIX = "guardrep_"  # No waggle IdKind names a report: it never crosses Waggle.
GUARD_REPORT_ID_PATTERN = rf"^guardrep_{_ULID}$"
MAX_CITED_EVENTS = 64  # A rule cites the events that made it fire, never a whole window of them.
MAX_TARGETS = 32  # Bees, tasks or grants one report may name.
MAX_RULE_CHARS = 64  # Rule keys are short data keys ("injection_then_denial"), never prose.
MAX_SUMMARY_CHARS = 280  # One sentence for the human, built by the rule from ids and counts.

GuardReportId = NewType("GuardReportId", str)

_EventRef = Annotated[str, Field(pattern=rf"^event_{_ULID}$")]
_BeeRef = Annotated[str, Field(pattern=rf"^(worker|warden)_{_ULID}$")]
_TaskRef = Annotated[str, Field(pattern=rf"^task_{_ULID}$")]
_GrantRef = Annotated[str, Field(pattern=rf"^grant_{_ULID}$")]

__all__ = [
    "GUARD_REPORT_ID_PATTERN",
    "GUARD_REPORT_ID_PREFIX",
    "MAX_CITED_EVENTS",
    "MAX_SUMMARY_CHARS",
    "MAX_TARGETS",
    "REQUEST_ACTIONS",
    "GuardAction",
    "GuardConfidence",
    "GuardReport",
    "GuardReportId",
    "GuardRequestDoor",
    "new_guard_report_id",
]


class GuardAction(Enum):
    """What a report recommends; only the three `REQUEST_ACTIONS` ask the Queen for anything."""

    ISOLATE_CELL = "isolate_cell"  # A request: the Queen isolates one Cell (roadmap 10.6a).
    QUARANTINE_BEE = "quarantine_bee"  # A request: the Queen quarantines one bee (10.6c).
    STING_CUT = "sting_cut"  # A request: cut one Cell off entirely (phase 13 carries it out).
    RAISE_AUDIT_RATE = "raise_audit_rate"  # Narrows the whole Hive: the Guard Bee does it alone.
    REDUCE_ENTRANCE = "reduce_entrance"  # Narrows the whole Hive: the Entrance Reducer (ADR-0041).
    OBSERVE = "observe"  # Nothing to do: an alert kept for the record and the human's view.


# The actions aimed at one Cell or one bee: never the Guard Bee's to take, only to ask for.
REQUEST_ACTIONS = frozenset(
    {GuardAction.ISOLATE_CELL, GuardAction.QUARANTINE_BEE, GuardAction.STING_CUT}
)


class GuardConfidence(Enum):
    """How sure the rule that fired is, lowest first; `[guard] request_confidence` is a floor."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """This tier's place in the order, so two tiers compare without string tricks."""
        return _CONFIDENCE_ORDER.index(self)

    def at_least(self, floor: GuardConfidence) -> bool:
        """Say whether this tier reaches `floor` (a request is filed only at or above it)."""
        return self.rank >= floor.rank


# Declaration order is the confidence order; kept beside the enum so the two cannot drift.
_CONFIDENCE_ORDER = tuple(GuardConfidence)


class GuardReport(BaseModel):
    """One Guard Bee finding: the rule, its evidence, its targets, and the action it recommends.

    Attributes:
        id: The report's own id (`guardrep_<ULID>`), cited by `guard.alert`, `cell.isolated` and
            the Alarm that shows it to the human.
        rule: The key of the rule that fired, as the Guard Bee's rule data names it.
        event_ids: The trail events that made the rule fire, oldest first; isolation taints
            memory from the first of them on (ADR-0043).
        cell_id: The Cell the finding is about, when it is about one.
        bee_ids: The Wardens and Workers it implicates.
        task_ids: Their tasks.
        grant_ids: The grants they hold, so a decision can see what access is at stake.
        recommended: What the rule recommends; a request only for `REQUEST_ACTIONS`.
        confidence: How sure the rule is.
        filed_at: When the Guard Bee wrote it.
        summary: One sentence for the human, written by the rule from ids and counts only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: GuardReportId = Field(pattern=GUARD_REPORT_ID_PATTERN)
    rule: str = Field(pattern=r"^[a-z][a-z0-9_.]*$", max_length=MAX_RULE_CHARS)
    event_ids: tuple[_EventRef, ...] = Field(min_length=1, max_length=MAX_CITED_EVENTS)
    cell_id: Annotated[str, Field(pattern=rf"^cell_{_ULID}$")] | None = None
    bee_ids: tuple[_BeeRef, ...] = Field(default=(), max_length=MAX_TARGETS)
    task_ids: tuple[_TaskRef, ...] = Field(default=(), max_length=MAX_TARGETS)
    grant_ids: tuple[_GrantRef, ...] = Field(default=(), max_length=MAX_TARGETS)
    recommended: GuardAction
    confidence: GuardConfidence
    filed_at: UtcDatetime
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)

    @model_validator(mode="after")
    def _names_its_target(self) -> Self:
        """Refuse a request that does not name what it is aimed at (module invariants)."""
        # A Cell-level lever with no Cell, or a quarantine with no bee and no task, could only be
        # carried out by guessing, and a guess is never how the Hive narrows one bee's access.
        cell_level = self.recommended in {GuardAction.ISOLATE_CELL, GuardAction.STING_CUT}
        if cell_level and self.cell_id is None:
            raise ValueError(f"A {self.recommended.value} report must name its Cell.")
        if self.recommended is GuardAction.QUARANTINE_BEE and not (self.bee_ids or self.task_ids):
            raise ValueError("A quarantine_bee report must name a bee or a task.")
        return self

    @property
    def is_request(self) -> bool:
        """Say whether this report asks the Queen to act on one Cell or one bee."""
        return self.recommended in REQUEST_ACTIONS


class GuardRequestDoor(Protocol):
    """The Queen's door for a Guard report: a request to decide, or a CRITICAL report to show.

    The Queen implements it in her own process; the Guard Bee calls it. `file_guard_request` is
    the one way a request reaches her inbox: durable before it returns, so a request filed just
    before a restart is still decided after it, and it decides nothing itself (she decides on her
    own tick, where a request outranks every Alarm and every human message). `report_to_human`
    is how a CRITICAL report reaches the human (ADR-0043: a report at CRITICAL confidence is shown
    to the human whatever it recommends): a SECURITY Alarm naming the report id, pushed to every
    enrolled device, shown at most once per report id, by whichever path shows it first (a
    CRITICAL request is shown once, by the Queen's decision on it, never also when filed).
    """

    async def file_guard_request(self, report: GuardReport) -> None:
        """Record `report` for the Queen's next tick and wake her.

        Args:
            report: A report whose `is_request` is True.

        Raises:
            ValueError: `report` is not a request; a finding that asks for nothing is recorded
                as `guard.alert` by the Guard Bee alone and never enters the Queen's inbox.
        """
        ...

    async def report_to_human(self, report: GuardReport) -> None:
        """Show a CRITICAL report to the human as a SECURITY Alarm naming its id, once.

        Durable before it returns (the Alarm's `alarm.escalated` row and its chat line are
        committed), and idempotent by report id: a report already shown, by this call or by the
        Queen's decision on it, is not shown again. A CRITICAL request is filed instead of shown
        here, so the one Alarm the human gets for it also says what the Queen did about it.

        Args:
            report: Any report whose confidence is CRITICAL, a request or not.

        Raises:
            ValueError: `report` is below CRITICAL confidence; the Guard Bee records such a
                finding as `guard.alert` alone, and a request goes through `file_guard_request`.
        """
        ...


def new_guard_report_id(clock: Clock) -> GuardReportId:
    """Mint a fresh `guardrep_`-prefixed ULID, timestamped by `clock`.

    Args:
        clock: Injected clock so the id's timestamp is deterministic in tests.

    Returns:
        A `"guardrep_<26-char ULID>"` string matching `GUARD_REPORT_ID_PATTERN`.
    """
    # Milliseconds, not seconds: waggle.ulid's encoding assumes millisecond resolution.
    timestamp_ms = int(clock.now().timestamp() * 1000)
    ulid = encode_ulid(timestamp_ms, secrets.token_bytes(RANDOMNESS_BYTES))
    return GuardReportId(f"{GUARD_REPORT_ID_PREFIX}{ulid}")
