"""Re-export ToolCallRecord and classify_error from the shared bounded-loop records module.

Roadmap step 6.9 moved every name here to `hivemind.workers.roles.bounded_loop.records`, once
nothing in this module was found to be Drone-specific. This module keeps the Drone's own import
path alive (this package's own tests import these names from here directly) with no logic of its
own (codingrules section 5.4).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone.outcome`. Read by
    `hivemind.workers.roles.drone.outcome.executor` and `.fields`. Calls into `hivemind.workers.
    roles.bounded_loop.records` only.

Key invariants:
    - None beyond `hivemind.workers.roles.bounded_loop.records`'s own (see that module).

See Also:
    - hivemind.workers.roles.bounded_loop.records for every name's canonical home and docstring.
"""

from __future__ import annotations

from hivemind.workers.roles.bounded_loop.records import (
    ALLOWLIST_FAILED_MARK,
    CAPABILITY_DENIAL_PREFIXES,
    SIDE_EFFECT_TOOLS,
    SIZE_CAP_FAILED_MARK,
    ToolCallRecord,
    classify_error,
    is_lasting,
    target_for,
)

__all__ = [
    "ALLOWLIST_FAILED_MARK",
    "CAPABILITY_DENIAL_PREFIXES",
    "SIDE_EFFECT_TOOLS",
    "SIZE_CAP_FAILED_MARK",
    "ToolCallRecord",
    "classify_error",
    "is_lasting",
    "target_for",
]
