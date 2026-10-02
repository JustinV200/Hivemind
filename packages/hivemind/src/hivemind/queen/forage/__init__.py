"""Divide the Hive's shared Forage pool among Wardens on the Queen's behalf.

This package is queen-scoped, distinct from the Layer 1 `hivemind.forage` package, and sets
ceilings per Warden plus hosting plans for where each model runs. Roadmap step 4.7 gave it its
first real modules: `ledger` (the live book: every Cell's latest capacity, every Warden's local
report, every live grant, the Royal Reserve and the headroom they leave -- see that sub-package's
own docstring), `grants` (a grant's own lease -- issue, renew on heartbeat, shrink, revoke, expire
-- every edge through `hivemind.forage.grant_state`) and `requests` (`ForageRequest` handling:
within headroom by autopilot, contested by awake). Roadmap step 4.8 adds `hosting`
(`write_hosting_plan`, a Cell's `HostingPlan` from the map and ledger) and `ceilings`
(`set_ceilings`/`change_ceilings`, a Warden's local-pool bounds), and extends the ledger with
shared-seat, per-goal-spend, hosting-plan and ceilings tables `requests` now reads for its
SHARED_SEATS and SPEND checks. Roadmap step 8.3 adds `evict` (`evict_source`, the Queen's one order
to a Warden's model server, only to reclaim seats she exported); the Warden's reports of what its
server loaded are handled in `hivemind.queen.ticks.sources`. Roadmap 11.9 adds `resend`
(`resend_state`, what the ledger holds for a Warden that re-attaches, sent to it again).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles how the Queen divides the shared Forage pool among Wardens. Called by
    `hivemind.queen.ticks.forage` and `hivemind.queen.ticks.liveness`; calls into sibling packages
    at Layer 6 or below, never back up into queen's other sub-packages directly.

Key invariants:
    - Every grant this package's `grants` module hands to the ledger has already left GrantState.
      ISSUED (`grants.activate`): the ledger only ever holds a grant `hivemind.queen.forage.grants.
      revoke`/`sweep_expired` can legally revoke, per `hivemind.forage.grant_state.TRANSITIONS`.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/roadmap.md step 4.7 for the work that first populates it.
    - .claude/roadmap.md step 4.8 for hosting plans, ceilings and the ledger's own additions.

Public API (roadmap steps 4.7-4.8):
    - ForageLedger, Headroom, LocalPoolReport, SeatBook, SpendBook, DecisionBook, LedgerRecorder,
      LedgerStore, InMemoryLedgerStore, SqliteLedgerStore, apply_ledger_migrations: the live book,
      its three sub-books, its Fanner-facing feed and its persistence (ledger).
    - activate, revise, renew_grants_for_warden, revoke, release_finished, sweep_expired: a
      grant's own lease (grants).
    - ForageRequestOutcome, grant_wanted, handle_forage_request_for_kind: ForageRequest handling
      (requests; renamed from handle_sub_bee_request, roadmap step 4.7's own leftover).
    - PlanReason, write_hosting_plan: a Cell's HostingPlan (hosting).
    - NightVeilPlanError, night_veil_local_only, restrict_to_local: Night Veil's local-only hosting
      plan checks (roadmap step 5.7a; night_veil).
    - set_ceilings, change_ceilings, initial_ceilings: a Warden's local-pool bounds, and the
      first ones a newly attached Warden gets (ceilings).
    - evict_source: take back seats she exported from a Warden's model server (evict; roadmap 8.3).
    - Resent, resend_state: the ceilings, plan and live grants a re-attached Warden is sent again
      (resend; roadmap 11.9).
    - redeliver_owed: send an attached Warden its ceilings and plan in force again, until its
      link takes them (deliveries; phase-7 handoff, section 8 item 5).
"""

from hivemind.queen.forage.ceilings import change_ceilings, initial_ceilings, set_ceilings
from hivemind.queen.forage.deliveries import redeliver_owed
from hivemind.queen.forage.evict import evict_source
from hivemind.queen.forage.grants import (
    activate,
    release_finished,
    renew_grants_for_warden,
    revise,
    revoke,
    sweep_expired,
)
from hivemind.queen.forage.hosting import PlanReason, write_hosting_plan
from hivemind.queen.forage.ledger import (
    DecisionBook,
    ForageLedger,
    Headroom,
    InMemoryLedgerStore,
    LedgerRecorder,
    LedgerStore,
    LocalPoolReport,
    SeatBook,
    SpendBook,
    SqliteLedgerStore,
    apply_ledger_migrations,
)
from hivemind.queen.forage.night_veil import (
    NightVeilPlanError,
    night_veil_local_only,
    restrict_to_local,
)
from hivemind.queen.forage.requests import (
    ForageRequestOutcome,
    grant_wanted,
    handle_forage_request_for_kind,
)
from hivemind.queen.forage.resend import Resent, resend_state

__all__ = [
    "DecisionBook",
    "ForageLedger",
    "ForageRequestOutcome",
    "Headroom",
    "InMemoryLedgerStore",
    "LedgerRecorder",
    "LedgerStore",
    "LocalPoolReport",
    "NightVeilPlanError",
    "PlanReason",
    "Resent",
    "SeatBook",
    "SpendBook",
    "SqliteLedgerStore",
    "activate",
    "apply_ledger_migrations",
    "change_ceilings",
    "evict_source",
    "grant_wanted",
    "handle_forage_request_for_kind",
    "initial_ceilings",
    "night_veil_local_only",
    "redeliver_owed",
    "release_finished",
    "renew_grants_for_warden",
    "resend_state",
    "restrict_to_local",
    "revise",
    "revoke",
    "set_ceilings",
    "sweep_expired",
    "write_hosting_plan",
]
