"""The Guard Bee: the Hive's security watcher, run on the Queen's tick (roadmap step 10.6).

The Guard Bee (a Worker specialised in security and monitoring) watches the central Pheromone Trail
(the Hive's append-only audit log) from the Queen's process on the Hive Stand, with deterministic
rules shipped as data, and turns every finding into a `hivemind.guard.GuardReport`, a `guard.alert`
trail event and a C2 deposit for the Honey browser. It acts alone only to narrow the whole Hive (it
raises a Capping tier's sampled-audit rate, and it orders the Entrance Reducer through the trail);
anything aimed at one Cell or one bee it files as a request through the Queen's door, and she
decides (ADR-0043). Rules that ask for judgement get one awake episode on the judge slot, beside
the Queen's tick. The package splits by responsibility (codingrules 5.2): `rules` (the rule data and
its loader), `facts` (what an event becomes, the joins between events, and who may have recorded
it), `watch` (reading the trail and every living Night Veil Cell's segment: rebuilt on start,
followed per node, only attributed facts counted), `evaluate` (the pure decision), `judge` and
`lane` (the awake episode and the lane it runs in), `requests` (the request floor, coalescing and
cap), `respond` (report, act, record), `sink` (the C2 seam) and `bee` (the round, and
`build_guard_bee`).
This face only re-exports (codingrules 5.4).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles`. Built by the composition
    root through `build_guard_bee` and held on `hivemind.queen.deps.QueenDeps.guard_bee`; ticked
    by `hivemind.queen.ticks.guard_bee`. Never imports `hivemind.wardens` or `hivemind.queen`.

Key invariants:
    - It never touches a Cell, never widens anything, and never awaits a model inside a round.
    - Every finding is a report and a `guard.alert`; only a request reaches the Queen's inbox.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - docs/guard/guard-bee.md for every shipped rule.
    - hivemind.guard.report for GuardReport and GuardRequestDoor, the contract it builds against.

Public API (roadmap 10.6):
    - GuardBee, GuardBeeInputs, GuardBeeParts, build_guard_bee, GUARD_BEE_ROLE, JUDGE_NEED: the
      Guard Bee and how a composition root builds it (bee).
    - GuardRules, GuardRule, Matcher, RuleShape, GroupKey, Measure, load_guard_rules,
      NARROWING_ACTIONS, RULES_FILENAME, SUBJECT_FORGED_KIND, DERIVED_KINDS: the rules as data,
      and the one kind the Guard Bee derives rather than reads (rules).
    - TrailFact, EpisodeIndex, Attribution, fact_from_event, INDEX_KINDS, BINDING_KINDS,
      OWNED_KINDS: what it reads, joins and attributes (facts).
    - TrailWatch, TrailReader, read_pages, ALERT_KIND, INDEX_HORIZON_S, OWNERSHIP_HORIZON_S,
      LATE_LAG_S: how it reads the trail and the Night Veil segments (watch).
    - Finding, Mark, Sighting, Targets, evaluate, targets_of, allowed_actions, HIVE_KEY: the
      decision, and how far a rule's findings are reported (evaluate).
    - GuardJudge, ModelGuardJudge, JudgeCase, JudgeReply, Verdict, render_case: the awake episode
      (judge); JudgeLane, DEFAULT_LANE_CAPACITY: the lane it runs in (lane).
    - RequestLedger, Disposition, request_target: the request limits (requests).
    - FindingResponder, ResponderDeps, AuditRaise, Response, build_report, REDUCE_ORDERED_KIND:
      reporting and acting (respond).
    - GuardReportSink, InMemoryGuardReportSink, GuardDeposit, HIVE_SCOPE: the C2 seam (sink).
    - GuardBeeError, GuardRulesError: its errors (hivemind.workers.errors).
"""

from hivemind.workers.errors import GuardBeeError, GuardRulesError
from hivemind.workers.roles.guard_bee.bee import (
    GUARD_BEE_ROLE,
    JUDGE_NEED,
    GuardBee,
    GuardBeeInputs,
    GuardBeeParts,
    build_guard_bee,
)
from hivemind.workers.roles.guard_bee.evaluate import (
    HIVE_KEY,
    Finding,
    Mark,
    Sighting,
    Targets,
    allowed_actions,
    evaluate,
    targets_of,
)
from hivemind.workers.roles.guard_bee.facts import (
    BINDING_KINDS,
    INDEX_KINDS,
    OWNED_KINDS,
    Attribution,
    EpisodeIndex,
    TrailFact,
    fact_from_event,
)
from hivemind.workers.roles.guard_bee.judge import (
    GuardJudge,
    JudgeCase,
    JudgeReply,
    ModelGuardJudge,
    Verdict,
    render_case,
)
from hivemind.workers.roles.guard_bee.lane import DEFAULT_LANE_CAPACITY, JudgeLane
from hivemind.workers.roles.guard_bee.requests import Disposition, RequestLedger, request_target
from hivemind.workers.roles.guard_bee.respond import (
    REDUCE_ORDERED_KIND,
    AuditRaise,
    FindingResponder,
    ResponderDeps,
    Response,
    build_report,
)
from hivemind.workers.roles.guard_bee.rules import (
    DERIVED_KINDS,
    NARROWING_ACTIONS,
    RULES_FILENAME,
    SUBJECT_FORGED_KIND,
    GroupKey,
    GuardRule,
    GuardRules,
    Matcher,
    Measure,
    RuleShape,
    load_guard_rules,
)
from hivemind.workers.roles.guard_bee.sink import (
    HIVE_SCOPE,
    GuardDeposit,
    GuardReportSink,
    InMemoryGuardReportSink,
)
from hivemind.workers.roles.guard_bee.watch import (
    ALERT_KIND,
    INDEX_HORIZON_S,
    LATE_LAG_S,
    OWNERSHIP_HORIZON_S,
    TrailReader,
    TrailWatch,
    read_pages,
)

__all__ = [
    "ALERT_KIND",
    "BINDING_KINDS",
    "DEFAULT_LANE_CAPACITY",
    "DERIVED_KINDS",
    "GUARD_BEE_ROLE",
    "HIVE_KEY",
    "HIVE_SCOPE",
    "INDEX_HORIZON_S",
    "INDEX_KINDS",
    "JUDGE_NEED",
    "LATE_LAG_S",
    "NARROWING_ACTIONS",
    "OWNED_KINDS",
    "OWNERSHIP_HORIZON_S",
    "REDUCE_ORDERED_KIND",
    "RULES_FILENAME",
    "SUBJECT_FORGED_KIND",
    "Attribution",
    "AuditRaise",
    "Disposition",
    "EpisodeIndex",
    "Finding",
    "FindingResponder",
    "GroupKey",
    "GuardBee",
    "GuardBeeError",
    "GuardBeeInputs",
    "GuardBeeParts",
    "GuardDeposit",
    "GuardJudge",
    "GuardReportSink",
    "GuardRule",
    "GuardRules",
    "GuardRulesError",
    "InMemoryGuardReportSink",
    "JudgeCase",
    "JudgeLane",
    "JudgeReply",
    "Mark",
    "Matcher",
    "Measure",
    "ModelGuardJudge",
    "RequestLedger",
    "ResponderDeps",
    "Response",
    "RuleShape",
    "Sighting",
    "Targets",
    "TrailFact",
    "TrailReader",
    "TrailWatch",
    "Verdict",
    "allowed_actions",
    "build_guard_bee",
    "build_report",
    "evaluate",
    "fact_from_event",
    "load_guard_rules",
    "read_pages",
    "render_case",
    "request_target",
    "targets_of",
]
