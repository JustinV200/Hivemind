"""Run the Guard Bee on the Queen's tick: the queen's side of roadmap step 10.6.

A package of one module because `hivemind.queen.ticks` already holds the ten modules codingrules
5.6 allows; its import path is the one a tick module of this name would have had.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.ticks.housekeeping.run_housekeeping` on every tick.

Key invariants:
    - A Queen with no Guard Bee on her deps runs exactly as she did before one existed.

See Also:
    - hivemind.workers.roles.guard_bee for the Guard Bee itself.
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.

Public API (roadmap 10.6):
    - run_guard_bee: one Guard Bee round, when one is due, with a failed round contained (tick).
"""

from hivemind.queen.ticks.guard_bee.tick import run_guard_bee

__all__ = ["run_guard_bee"]
