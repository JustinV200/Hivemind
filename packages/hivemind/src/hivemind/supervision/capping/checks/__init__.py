"""Provide the Capping gate's check-ladder seam, this phase's deterministic checks and the judge.

`Check` and `CheckContext` (`base.py`) are the seam every rung of a risk tier's ladder implements
(codingrules section 8.1); `deterministic.py` supplies this phase's autopilot rungs -- schema,
path and command allowlists, diff size cap -- and the registry `deterministic_checks()` a
composition root wires into `hivemind.supervision.capping.gate.GateDeps`. `judge.py` supplies the
independent-review rung (`JudgeCheck`, `CheckKind.JUDGE`) behind the same `Check` Protocol, plus
the `JudgeReviewer` seam a Warden-layer dispatch satisfies with a real model call later; `rubrics.
py` loads the per-tier rubric a judge reviews against; `fake.py` a scripted `JudgeReviewer` for
tests; `human.py` supplies `HumanCheck` (roadmap step 5.0d), the HUMAN rung a leave-policy ASK
verdict calls directly, never through the generic ladder (that module's own docstring). A later
phase adds a sandbox-test rung the same way, without touching the gate.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping`.
    Built by whichever composition root constructs a `GateDeps` (a Warden, roadmap step 3.19);
    run by `hivemind.supervision.capping.gate.CappingGate`.

Key invariants:
    - Whatever is not re-exported here is private to this sub-package (codingrules section 5.4).

See Also:
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule this sub-package
      follows.
    - .claude/codingrules.md section 8.12 for the judge-independence rule judge.py implements.
    - hivemind.supervision.capping.checks.base for Check and CheckContext.
    - hivemind.supervision.capping.checks.deterministic for this phase's four concrete checks.
    - hivemind.supervision.capping.checks.judge for JudgeCheck, JudgeReviewer and JudgeVerdict.

Public API:
    - Check, CheckContext, CheckResultRecord: the check-ladder seam (base).
    - SchemaCheck, PathAllowlistCheck, CommandAllowlistCheck, GuiAllowlistCheck, DiffSizeCapCheck,
      deterministic_checks: this phase's autopilot rungs (deterministic).
    - JudgeCheck, JudgeOutcome, JudgeRequest, JudgeReviewer, JudgeVerdict, judge_checks: the
      independent-review rung and deterministic_checks()'s sibling registry (judge).
    - JudgeRubric, load_judge_rubrics: the per-tier rubric a judge reviews against (rubrics).
    - FakeJudgeReviewer: a scripted JudgeReviewer for tests and demo paths (fake).
    - HumanCheck, LEAVE_QUESTION_OPTIONS, KEEP_OPTION, KEEP_FOR_GOAL_OPTION, DISCARD_OPTION: the
      HUMAN rung a leave-policy ASK verdict calls directly (human).
"""

from hivemind.supervision.capping.checks.base import Check, CheckContext, CheckResultRecord
from hivemind.supervision.capping.checks.deterministic import (
    CommandAllowlistCheck,
    DiffSizeCapCheck,
    GuiAllowlistCheck,
    PathAllowlistCheck,
    SchemaCheck,
    deterministic_checks,
)
from hivemind.supervision.capping.checks.fake import FakeJudgeReviewer
from hivemind.supervision.capping.checks.human import (
    DISCARD_OPTION,
    KEEP_FOR_GOAL_OPTION,
    KEEP_OPTION,
    LEAVE_QUESTION_OPTIONS,
    HumanCheck,
)
from hivemind.supervision.capping.checks.judge import (
    JudgeCheck,
    JudgeOutcome,
    JudgeRequest,
    JudgeReviewer,
    JudgeVerdict,
    judge_checks,
)
from hivemind.supervision.capping.checks.rubrics import JudgeRubric, load_judge_rubrics

__all__ = [
    "DISCARD_OPTION",
    "KEEP_FOR_GOAL_OPTION",
    "KEEP_OPTION",
    "LEAVE_QUESTION_OPTIONS",
    "Check",
    "CheckContext",
    "CheckResultRecord",
    "CommandAllowlistCheck",
    "DiffSizeCapCheck",
    "FakeJudgeReviewer",
    "GuiAllowlistCheck",
    "HumanCheck",
    "JudgeCheck",
    "JudgeOutcome",
    "JudgeRequest",
    "JudgeReviewer",
    "JudgeRubric",
    "JudgeVerdict",
    "PathAllowlistCheck",
    "SchemaCheck",
    "deterministic_checks",
    "judge_checks",
    "load_judge_rubrics",
]
