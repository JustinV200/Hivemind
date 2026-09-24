"""Re-export build_claimed_outcome and build_handoff_outcome from the shared bounded-loop outcome.

Roadmap step 6.9 moved both functions to `hivemind.workers.roles.bounded_loop.outcome`, once
neither was found to be Drone-specific. This module keeps the Drone's own import path alive (this
package's own tests import these names from here directly) with no logic of its own (codingrules
section 5.4).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone.outcome`. Used by
    `hivemind.workers.roles.drone.Drone.run`. Calls into `hivemind.workers.roles.bounded_loop.
    outcome` only.

Key invariants:
    - None beyond `hivemind.workers.roles.bounded_loop.outcome`'s own (see that module).

See Also:
    - hivemind.workers.roles.bounded_loop.outcome for both functions' canonical home and docstring.
"""

from __future__ import annotations

from hivemind.workers.roles.bounded_loop.outcome import (
    MAX_SUMMARY_CHARS,
    build_claimed_outcome,
    build_handoff_outcome,
)

__all__ = ["MAX_SUMMARY_CHARS", "build_claimed_outcome", "build_handoff_outcome"]
