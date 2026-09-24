"""Dispatcher: place, grant and assign a ready task, over Real or Virtual Cells alike.

Roadmap step 3.20 built `dispatch_ready`/`redispatch` as one `dispatcher.py` module over v0's
Real-only placement; roadmap step 5.7 (ADR-0028) adds a genuine Virtual side (provision a fresh
Cell, or resume an Overwintered one) and outgrew codingrules section 5.1's 300-line file cap, so
this is now a package: `ready` (the public `dispatch_ready`/`redispatch`/`resume_paused` themselves,
plus the grant-and-assign wire send both share), `snapshot` (the pure `Inventory`/`ForageView` I/O
helper `hivemind.queen.placement.decide` reads) and `acquire` (`resolve_link`, the seam that turns
a Virtual `Placement` into a `WardenLink` through `QueenDeps.virtual_provider`, with the
retry-once-with-zeroed-headroom path ADR-0028's own Consequences call for). The zero-grant fix adds
`sizing` (`size_grant`: every grant sized from the Cell's capacity as it stands, when its link can
read it live) and `zero_grant` (what a grant that runs no bee does: wait PENDING for a passing
shortfall to pass, within `[forage] zero_grant_patience_s`, or be denied at once). This file is the
package's face: every name below is exactly what `hivemind.queen.dispatcher.py` used to export, so
every existing caller (`hivemind.queen.queen`, `hivemind.queen.ticks.results`, `hivemind.queen.
cluster.protocol`) imports it unchanged.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen` package. Called
    unconditionally at the end of every `hivemind.queen.queen.Queen` tick, and once more
    immediately after `submit_goal` and after every `COMPLETE_TASK` decision. Calls into
    `hivemind.queen.placement` (Placement, PlacementError, decide), `hivemind.queen.deps`
    (QueenDeps, WardenLink, VirtualCellProvider) and everything `ready`/`snapshot`/`acquire` each
    name in their own module docstrings.

Key invariants:
    - `GrantIssued` is always sent before `TaskAssign`, on the same Warden link, for the same
      task, whether from a fresh dispatch or a retry (`ready._send_grant_and_assign`'s own order).
    - A Virtual `Placement` is acquired through `acquire.resolve_link` before any chamber
      transition or wire send: a failed acquire never leaves a task half-assigned.
    - A fresh task's grant is sized before its chamber transition, and nothing is sent to its
      Warden before that transition: a task left waiting for a grant stays PENDING, unsent.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the Placement union this package
      turns into a wire send.
    - .claude/roadmap.md step 5.7 for the exit criteria this package's own split satisfies.
    - hivemind.queen.placement for decide, this package's one placement call.

Public API:
    - dispatch_ready, redispatch, resume_paused: place, grant and (re-)assign a task (ready).
"""

from hivemind.queen.dispatcher.ready import dispatch_ready, redispatch, resume_paused

__all__ = ["dispatch_ready", "redispatch", "resume_paused"]
