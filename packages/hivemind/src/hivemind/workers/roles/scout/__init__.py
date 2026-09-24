"""Implement Scout: the strictly budgeted, read-only recon Worker role.

Roadmap step 6.10. A Scout looks around cheaply, on a strict round budget, and reports what it
found -- feasible or not, with a summary, targets, suggested steps and risks -- so the Queen can
decide whether to commit Foragers to a goal before spending on them. This face only re-exports
(codingrules section 5.4); `role.py` is `Scout` itself, `tools.py` its own narrow tool registry and
`report_findings`, the one tool that ends its loop.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles`. Constructed by a Warden's
    `worker_factory` (`hivemind.workers.roles.worker_for`) and run once per attempt by `hivemind.
    workers.runtime.WorkerRuntime`. Calls into `hivemind.workers.roles.bounded_loop`, `hivemind.
    workers.tools` and waggle only; never `hivemind.wardens` or `hivemind.queen` (codingrules
    section 4: "a Worker never imports its Warden").

Key invariants:
    - `Scout.run` never marks a task SUCCEEDED (codingrules section 8.7).
    - Every `claimed=True` outcome carries a `ScoutReport`, filed or an exhaustion fallback.

See Also:
    - .claude/roadmap.md step 6.10 for the bullet this package implements.
    - hivemind.workers.roles.bounded_loop for the shared machinery this role reuses.
    - waggle.messages.task.recon for ScoutReport and SCOUT_REPORT_FILE.

Public API:
    - Scout, SCOUT_MAX_ROUNDS: the role itself, and its default tool-loop round cap
      (hivemind.workers.roles.scout.role).
    - scout_build_registry, report_findings, REPORT_FINDINGS_DEFINITION, scout_http_get,
      SCOUT_HTTP_DEFINITION: this role's own narrow tool set (hivemind.workers.roles.scout.tools).
"""

from hivemind.workers.roles.scout.role import SCOUT_MAX_ROUNDS, Scout
from hivemind.workers.roles.scout.tools import (
    REPORT_FINDINGS_DEFINITION,
    SCOUT_HTTP_DEFINITION,
    report_findings,
    scout_build_registry,
    scout_http_get,
)

__all__ = [
    "REPORT_FINDINGS_DEFINITION",
    "SCOUT_HTTP_DEFINITION",
    "SCOUT_MAX_ROUNDS",
    "Scout",
    "report_findings",
    "scout_build_registry",
    "scout_http_get",
]
