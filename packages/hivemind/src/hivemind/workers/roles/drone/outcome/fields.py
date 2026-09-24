"""Re-export every Handoff-field-deriving function from the shared bounded-loop fields module.

Roadmap step 6.9 moved every function here to `hivemind.workers.roles.bounded_loop.fields`, once
none of them was found to be Drone-specific. This module keeps the Drone's own import path alive
(this package's own tests import these names from here directly) with no logic of its own
(codingrules section 5.4).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone.outcome`. Called by
    `hivemind.workers.roles.drone.outcome.build.build_handoff_outcome`. Calls into `hivemind.
    workers.roles.bounded_loop.fields` only.

Key invariants:
    - None beyond `hivemind.workers.roles.bounded_loop.fields`'s own (see that module).

See Also:
    - hivemind.workers.roles.bounded_loop.fields for every function's canonical home and
      docstring.
"""

from __future__ import annotations

from hivemind.workers.roles.bounded_loop.fields import (
    MAX_DECISION_TEXT_CHARS,
    MAX_DECISIONS_KEPT,
    MAX_DERIVED_LIST_ITEMS,
    MAX_LIST_ITEM_CHARS,
    constraint_lines,
    decisions_from_records,
    do_not_redo_lines,
    next_step_lines,
    open_thread_lines,
    pinned_fact_lines,
    summarise_progress,
    tried_and_failed_lines,
)

__all__ = [
    "MAX_DECISIONS_KEPT",
    "MAX_DECISION_TEXT_CHARS",
    "MAX_DERIVED_LIST_ITEMS",
    "MAX_LIST_ITEM_CHARS",
    "constraint_lines",
    "decisions_from_records",
    "do_not_redo_lines",
    "next_step_lines",
    "open_thread_lines",
    "pinned_fact_lines",
    "summarise_progress",
    "tried_and_failed_lines",
]
