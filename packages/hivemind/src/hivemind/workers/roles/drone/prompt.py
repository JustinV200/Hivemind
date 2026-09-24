"""Name the Drone's own knobs, and re-export brief_for and select_counter for its own tests.

Roadmap step 6.9 moved every prompt-assembly function to `hivemind.workers.roles.bounded_loop.
prompt`, generalised to take a role's own knobs as plain parameters (`assemble_role_prompt`,
`build_request`, `initial_budget`) instead of closing over the Drone's constants -- `hivemind.
workers.roles.drone.role.Drone` now builds a `RoleProfile` from those constants and delegates the
whole attempt to `hivemind.workers.roles.bounded_loop.runner.run_bounded_loop`, so this module no
longer calls the generalised builders itself. `brief_for` and `select_counter` carried no
Drone-specific behaviour at all and moved unchanged; this module still re-exports both, at their
original path, so this package's own tests (which import `brief_for` from here directly) see no
change at all.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone`. `DRONE_ROLE`,
    `DRONE_BUDGET_FRACTION` and `DRONE_OUTPUT_RESERVE_TOKENS` are read by `hivemind.workers.roles.
    drone.role.Drone.__init__` to build its own `RoleProfile`. Calls into `hivemind.workers.roles.
    bounded_loop.prompt` only.

Key invariants:
    - `DRONE_BUDGET_FRACTION` and `DRONE_OUTPUT_RESERVE_TOKENS` are unchanged from before roadmap
      step 6.9 (0.6 and 4,096): the Drone's own prompt snapshot and every existing test must stay
      unchanged, and both depend on these two figures.

See Also:
    - hivemind.workers.roles.bounded_loop.prompt for brief_for, select_counter and the generic
      builders `hivemind.workers.roles.bounded_loop.runner.run_bounded_loop` now calls directly.
    - hivemind.workers.roles.drone.role for Drone, this module's one reader.
"""

from __future__ import annotations

from hivemind.workers.roles.bounded_loop.prompt import brief_for, select_counter

DRONE_ROLE = "drone"  # Principal.role for every Drone episode; matches the manifest key ("drone").
# Mirrors the manifest's [memory] budget_fraction/output_reserve_tokens defaults (docs/manifests):
# WorkerContext carries no manifest reference for a role to read these from directly (this
# package's own module docstring), so they are fixed here rather than threaded through from the
# composition root.
DRONE_BUDGET_FRACTION = 0.6
DRONE_OUTPUT_RESERVE_TOKENS = 4_096

__all__ = [
    "DRONE_BUDGET_FRACTION",
    "DRONE_OUTPUT_RESERVE_TOKENS",
    "DRONE_ROLE",
    "brief_for",
    "select_counter",
]
