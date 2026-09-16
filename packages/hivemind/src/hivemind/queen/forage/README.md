# hivemind.queen.forage

The forage package (queen-scoped, distinct from the Layer 1 `hivemind.forage` package) is how the
Queen divides the Hive's shared Forage pool: the live ledger, a grant's own lease, and
`ForageRequest` handling. Ceilings per Warden and hosting plans for where each model runs are a
later roadmap phase's own addition to this package.

## Public API (roadmap step 4.7)

- **`ledger`**: `ForageLedger` -- the live book. Owns three in-memory tables (mirroring
  `hivemind.forage.map.ForageMap`'s own shape): every Cell's latest `ForageCapacity`
  (`report_capacity`), every Warden's own `LocalPoolReport` (`report_local_pool`; reported, never
  granted -- codingrules 8.10), and every live `ForageGrant` (`record_grant`, which drops one the
  moment its own `GrantState` becomes terminal). `headroom()` computes v1's one live figure,
  `Headroom(sub_bees=...)`: shared totals, less the Royal Reserve's own headroom-fraction margin
  and its `seats`, less every live grant's `max_sub_bees` (scoped to the sub-bee dimension only --
  see `model.py`'s own docstring for why spend and tokens are tracked per grant but not
  headroom-checked in v1). `set_reserve` replaces the reserve and persists it; `restore` rebuilds
  every in-memory table from the store, for a fresh Queen process. `LedgerStore`
  (`InMemoryLedgerStore` for tests, `SqliteLedgerStore` for durability -- Appendix C: "Forage
  ledger (SQLite) | Yes") is the persistence seam every mutation writes through.
- **`grants`**: `activate`, `revise`, `renew_grants_for_warden`, `revoke`, `sweep_expired` -- a
  grant's own lease, every edge through `hivemind.forage.grant_state.assert_transition`. `activate`
  moves a freshly issued grant (`hivemind.forage.allocate.grant` always starts one at `ISSUED`)
  to `ACTIVE`, since a task dispatch or a granted `ForageRequest` both mean the holder is about to
  draw on it at once; `revise` commits a grown/shrunk revision (same state, a new `GrantIssued`
  revision per ADR-0014); `renew_grants_for_warden` extends `expires_at` on a Heartbeat, no state
  change; `revoke` moves `ACTIVE`/`EXHAUSTED` to `REVOKED`, records `forage.revoked`
  (`hivemind.queen.trail.record_forage_event`) and drops the grant from the ledger's own headroom;
  `sweep_expired` revokes (cause `EXPIRED`) every live grant whose `expires_at` has passed.
  `hivemind.queen.dispatcher` also calls `activate` for every task-dispatch grant, so the ledger
  is the one place that ever sees a live shared grant, task-dispatched or request-driven alike.
- **`requests`**: `ForageRequestOutcome`, `handle_sub_bee_request` -- answer a `SUB_BEES`
  `ForageRequest` against the ledger's own headroom, through
  `hivemind.queen.autopilot.forage.decide_forage_request`: `GRANT` grows the existing grant by the
  wanted delta and commits it (`grants.revise`); `DENY`/`NEEDS_JUDGEMENT` mutate nothing. A
  `SHARED_SEATS`, `SPEND` or `BINDING` request is always denied with a reason naming v1's own
  scope (no shared spend or per-source seat ceiling exists yet to check headroom against).

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/forage -q
```

The synthetic test roadmap step 4.7 asks for -- fifty Wardens contending for a small shared
capacity, asserting the sum of live grants never exceeds capacity minus reserve at every moment,
not only at the end -- is `tests/unit/queen/forage/test_fifty_wardens.py`.
