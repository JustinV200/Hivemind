"""Implement Forager: the bounded see/act Worker role that gathers over a Cell's Exoskeleton.

Roadmap step 6.9. A Forager is given one `TaskAssign` whose Cell carries an Exoskeleton and drives
it -- reading and acting on a display, pointer, keyboard and browser -- through the same bounded
tool loop the Drone runs on (`hivemind.workers.roles.bounded_loop`), depositing every page it
reads as Nectar along the way. This face only re-exports (codingrules section 5.4); `role.py` is
`Forager` itself and its own refusal error, `nectar.py` the one `on_tool_result` hook that makes
the deposits.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles`. Constructed by a Warden's
    `worker_factory` (`hivemind.workers.roles.worker_for`) and run once per attempt by `hivemind.
    workers.runtime.WorkerRuntime`. Calls into `hivemind.workers.roles.bounded_loop`, `hivemind.
    workers.tools` and waggle only; never `hivemind.wardens` or `hivemind.queen` (codingrules
    section 4: "a Worker never imports its Warden").

Key invariants:
    - `Forager.run` never marks a task SUCCEEDED (codingrules section 8.7).
    - Never calls the model without an attached Exoskeleton (`hivemind.workers.roles.forager.
      role.ForagerRequiresExoskeletonError`).

See Also:
    - .claude/roadmap.md step 6.9 for the bullet this package implements.
    - hivemind.workers.roles.bounded_loop for the shared machinery this role reuses.
    - hivemind.workers.roles.drone for the sibling role this one is shaped after.

Public API:
    - Forager, FORAGER_MAX_ROUNDS, ForagerRequiresExoskeletonError: the role itself, its default
      tool-loop round cap, and the error it raises with no attached Exoskeleton
      (hivemind.workers.roles.forager.role).
    - deposit_nectar: the `on_tool_result` hook that deposits every page read as Nectar
      (hivemind.workers.roles.forager.nectar).
"""

from hivemind.workers.roles.forager.nectar import deposit_nectar
from hivemind.workers.roles.forager.role import (
    FORAGER_MAX_ROUNDS,
    Forager,
    ForagerRequiresExoskeletonError,
)

__all__ = [
    "FORAGER_MAX_ROUNDS",
    "Forager",
    "ForagerRequiresExoskeletonError",
    "deposit_nectar",
]
