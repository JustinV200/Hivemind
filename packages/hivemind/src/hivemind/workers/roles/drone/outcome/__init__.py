"""Re-export HandoffRequestedError, ToolCallRecord and the two outcome builders (codingrules 5.4).

Split by responsibility (codingrules section 5.2): `records.py` is `ToolCallRecord` and how a
call's own result text reads as pass/fail; `executor.py` is `HandoffRequestedError`, the tool
executor that cooperates with pause/cancel/handoff and remembers every call, and
`collect_artifacts`; `fields.py` derives every Handoff guidance field (`do_not_redo`,
`tried_and_failed`, `constraints`, `open_threads`, `pinned_facts`, `next_steps`) from what an
attempt actually did; `build.py` is the two ways one attempt ends. This face only re-exports.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone`. Used by
    `hivemind.workers.roles.drone.role.Drone.run`, this package's one caller outside itself.

Key invariants:
    - None beyond each submodule's own (see records.py, executor.py, fields.py, build.py).

See Also:
    - .claude/codingrules.md section 5.4 for the __init__.py re-export-only contract.
    - hivemind.workers.roles.drone for Drone, this package's one caller.

Public API:
    - HandoffRequestedError: the control exception a checkpoint raises between tool calls.
    - ToolCallRecord: one tool call this attempt made, and what it returned.
    - build_claimed_outcome, build_handoff_outcome: the two ways one attempt ends.
    - collect_artifacts: every `write_file` path still present under scratch, as ArtifactRefs.
"""

from hivemind.workers.roles.drone.outcome.build import (
    MAX_SUMMARY_CHARS,
    build_claimed_outcome,
    build_handoff_outcome,
)
from hivemind.workers.roles.drone.outcome.executor import (
    MAX_RECORDED_CALLS,
    HandoffRequestedError,
    collect_artifacts,
)
from hivemind.workers.roles.drone.outcome.records import ToolCallRecord

__all__ = [
    "MAX_RECORDED_CALLS",
    "MAX_SUMMARY_CHARS",
    "HandoffRequestedError",
    "ToolCallRecord",
    "build_claimed_outcome",
    "build_handoff_outcome",
    "collect_artifacts",
]
