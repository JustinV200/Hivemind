"""Run the Hive through chaos: dead Cells, cut links, bad models, floods, forgeries.

Roadmap step 13.6. Each `test_chaos_*` module beside this one breaks one thing on purpose against a
whole Hive built from a real manifest (real SQLite, the Hive Stand's real lease, fakes only for the
model and the Cell runtime), then holds the run to one bar from `rig`: every goal finishes or fails
explicitly, nothing hangs, and no lease, grant or Virtual Cell is left open. The suite is marked
`chaos` (and `e2e`), so the push CI leaves it to the nightly job; a leg that needs Docker or a Nuc
is marked `integration` too and skips without a daemon.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - .claude/roadmap.md step 13.6 and phase 13's exit criteria.
    - docs/runbooks/chaos.md for what each scenario breaks and how to run the suite.

Public API:
    - HANG_S, bounded, hive_on: running a scenario under the "nothing hangs" bound.
    - assert_explicit_ends, assert_nothing_left_open, open_grants, open_leases: the bar.
"""

from e2e.chaos.rig import (
    HANG_S,
    assert_explicit_ends,
    assert_nothing_left_open,
    bounded,
    hive_on,
    open_grants,
    open_leases,
)

__all__ = [
    "HANG_S",
    "assert_explicit_ends",
    "assert_nothing_left_open",
    "bounded",
    "hive_on",
    "open_grants",
    "open_leases",
]
