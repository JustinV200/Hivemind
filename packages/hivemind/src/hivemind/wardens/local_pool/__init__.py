"""Allocate a Warden's own sub-bee slots against its current grant's `max_sub_bees`.

A Warden's local pool (codingrules section 8.10) is everything physically on its own Cell; roadmap
step 3.19 populates only the sub-bee-slot half of it (`LocalPool`), a bare counter against
`GrantIssued.max_sub_bees` the Warden never asks the Queen to check for it. Allocating a Nuc's own
model-server resources (VRAM, disk, seats) is `hosting.py`, a later roadmap phase (8).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles allocating a Warden's own sub-bee slots. Owned by one `hivemind.wardens.warden.Warden`
    instance; consulted by `hivemind.wardens.ticks.assign` before every spawn.

Key invariants:
    - `LocalPool.acquire()` never lets `in_use` exceed `capacity` (hivemind.wardens.local_pool.pool
      for the full contract).

See Also:
    - .claude/codingrules.md section 8.10 for "a Warden divides its local pool under ceilings the
      Queen set once".
    - .claude/roadmap.md phase 3 step 3.19 for the work that first populates this package; phase 8
      for the model-server half (`hosting.py`).

Public API (roadmap step 3.19):
    - LocalPool: a bare sub-bee-slot counter against a grant's max_sub_bees (pool).
"""

from hivemind.wardens.local_pool.pool import LocalPool

__all__ = ["LocalPool"]
