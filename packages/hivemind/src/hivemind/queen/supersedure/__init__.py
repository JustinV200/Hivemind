"""Provide Supersedure: move the Hive Stand to another machine without two Queens ever active.

Roadmap 13.2a, ADR-0065. The Hive Stand is the machine the Queen runs on; moving it means freezing
her, copying her stores across, and handing over to a Queen started from the copy, who stays held
until the old one has stood down and acknowledged her. `preflight` is the candidate test,
`copy` the transfer, `handover` and `rollback` the two endings, `run` the driver that reaches
exactly one of them, `record` the trail events that carry the invariant across restarts, `gate`
the start rules each node's own trail imposes, `ports` what all of it acts through, and `door`
the human's lever on the running Queen. This file is the package's face.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by the Hive Entrance through `QueenDoor` (the lever), by the composition root (the start
    rules and the candidate's hold) and by `hive supersede --settle`. Calls into the sibling
    modules listed above, which reach `hivemind.common.backup`, `hivemind.guard`,
    `hivemind.pheromone`, `hivemind.queen.cluster` and waggle.

Key invariants:
    - This file holds re-exports and `__all__` only.
    - Never two Queens: see `gate` and docs/adr/0065-supersedure-moving-the-hive-stand.md.

See Also:
    - docs/runbooks/supersedure.md for the operator's walk-through.

Public API:
    - SupersedureDoor, SupersedureStarted, QueenLevers, FREEZE_KEY, stop_supersedure: the lever,
      the levers, and ending a move as the Queen stops (door).
    - CandidateLink, NoticeSender, QueenControls, OldQueenProbe, Endpoint, SupersedureWiring,
      SupersedureBook, DEFAULT_GRACE_S, DEFAULT_POLL_S: what it acts through (ports).
    - check_candidate, facts_for, CandidateFacts, HiveNeeds, ProbeReport, PreflightReport,
      PreflightCheck, CheckOutcome, MAX_CLOCK_SKEW_S, HEADROOM_FRACTION: the candidate test
      (preflight).
    - SupersedurePlan, SupersedurePorts, begin, finish: the driver (run).
    - Announcement, Handover, Rollback, relocation_notice: the two endings (handover,
      rollback).
    - StartRule, start_rule, check_start, settle, CandidateHold: never two Queens (gate).
    - Recorder, SupersedureRecord, AbortCause, latest, was_ready, next_sequence and the four
      kind names: the trail (record).
"""

from hivemind.queen.supersedure.door import (
    FREEZE_KEY,
    QueenLevers,
    SupersedureDoor,
    SupersedureStarted,
    stop_supersedure,
)
from hivemind.queen.supersedure.gate import (
    CandidateHold,
    StartRule,
    check_start,
    settle,
    start_rule,
)
from hivemind.queen.supersedure.handover import Announcement, Handover, relocation_notice
from hivemind.queen.supersedure.ports import (
    DEFAULT_GRACE_S,
    DEFAULT_POLL_S,
    CandidateLink,
    Endpoint,
    NoticeSender,
    OldQueenProbe,
    QueenControls,
    SupersedureBook,
    SupersedureWiring,
)
from hivemind.queen.supersedure.preflight import (
    HEADROOM_FRACTION,
    MAX_CLOCK_SKEW_S,
    CandidateFacts,
    CheckOutcome,
    HiveNeeds,
    PreflightCheck,
    PreflightReport,
    ProbeReport,
    check_candidate,
    facts_for,
)
from hivemind.queen.supersedure.record import (
    ABORTED_KIND,
    READY_KIND,
    STARTED_KIND,
    SUPERSEDED_KIND,
    AbortCause,
    Recorder,
    SupersedureRecord,
    latest,
    next_sequence,
    was_ready,
)
from hivemind.queen.supersedure.rollback import Rollback
from hivemind.queen.supersedure.run import SupersedurePlan, SupersedurePorts, begin, finish

__all__ = [
    "ABORTED_KIND",
    "DEFAULT_GRACE_S",
    "DEFAULT_POLL_S",
    "FREEZE_KEY",
    "HEADROOM_FRACTION",
    "MAX_CLOCK_SKEW_S",
    "READY_KIND",
    "STARTED_KIND",
    "SUPERSEDED_KIND",
    "AbortCause",
    "Announcement",
    "CandidateFacts",
    "CandidateHold",
    "CandidateLink",
    "CheckOutcome",
    "Endpoint",
    "Handover",
    "HiveNeeds",
    "NoticeSender",
    "OldQueenProbe",
    "PreflightCheck",
    "PreflightReport",
    "ProbeReport",
    "QueenControls",
    "QueenLevers",
    "Recorder",
    "Rollback",
    "StartRule",
    "SupersedureBook",
    "SupersedureDoor",
    "SupersedurePlan",
    "SupersedurePorts",
    "SupersedureRecord",
    "SupersedureStarted",
    "SupersedureWiring",
    "begin",
    "check_candidate",
    "check_start",
    "facts_for",
    "finish",
    "latest",
    "next_sequence",
    "relocation_notice",
    "settle",
    "start_rule",
    "stop_supersedure",
    "was_ready",
]
