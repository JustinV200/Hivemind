"""Divide the Hive's shared Forage pool among Wardens on the Queen's behalf.

This package is queen-scoped, distinct from the Layer 1 `hivemind.forage` package, and sets
ceilings per Warden plus hosting plans for where each model runs. Roadmap step 4.7 gives it its
first real modules: `ledger` (the live book: every Cell's latest capacity, every Warden's local
report, every live grant, the Royal Reserve and the headroom they leave -- see that sub-package's
own docstring), `grants` (a grant's own lease -- issue, renew on heartbeat, shrink, revoke, expire
-- every edge through `hivemind.forage.grant_state`) and `requests` (`ForageRequest` handling:
within headroom by autopilot, contested by awake).

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

Public API (roadmap step 4.7):
    - ForageLedger, Headroom, LocalPoolReport, LedgerStore, InMemoryLedgerStore,
      SqliteLedgerStore, apply_ledger_migrations: the live book and its persistence (ledger).
    - activate, revise, renew_grants_for_warden, revoke, sweep_expired: a grant's own lease
      (grants).
    - ForageRequestOutcome, handle_sub_bee_request: ForageRequest handling (requests).
"""

from hivemind.queen.forage.grants import (
    activate,
    renew_grants_for_warden,
    revise,
    revoke,
    sweep_expired,
)
from hivemind.queen.forage.ledger import (
    ForageLedger,
    Headroom,
    InMemoryLedgerStore,
    LedgerStore,
    LocalPoolReport,
    SqliteLedgerStore,
    apply_ledger_migrations,
)
from hivemind.queen.forage.requests import ForageRequestOutcome, handle_sub_bee_request

__all__ = [
    "ForageLedger",
    "ForageRequestOutcome",
    "Headroom",
    "InMemoryLedgerStore",
    "LedgerStore",
    "LocalPoolReport",
    "SqliteLedgerStore",
    "activate",
    "apply_ledger_migrations",
    "handle_sub_bee_request",
    "renew_grants_for_warden",
    "revise",
    "revoke",
    "sweep_expired",
]
