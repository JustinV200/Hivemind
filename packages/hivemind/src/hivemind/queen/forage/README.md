# hivemind.queen.forage

The forage package (queen-scoped, distinct from the Layer 1 `hivemind.forage` package) is how the
Queen divides the Hive's shared Forage pool: the live ledger, a grant's own lease, `ForageRequest`
handling, hosting plans and Ceilings per Warden.

## Public API (roadmap steps 4.7-4.8)

- **`ledger`**: `ForageLedger` -- the live book. Owns three in-memory tables directly (mirroring
  `hivemind.forage.map.ForageMap`'s own shape): every Cell's latest `ForageCapacity`
  (`report_capacity`), every Warden's own `LocalPoolReport` (`report_local_pool`; reported, never
  granted -- codingrules 8.10), and every live `ForageGrant` (`record_grant`, which drops one the
  moment its own `GrantState` becomes terminal). Three more sub-books, one field each on the
  ledger itself (roadmap step 4.8): `ledger.seats` (`SeatBook`) -- a shared source's declared seat
  capacity (`set_capacity`) and its live in-flight usage (`mark_started`/`mark_finished`, fed by
  the Fanner through `LedgerRecorder`, never persisted); `ledger.spend` (`SpendBook`) -- spend
  recorded per goal (`record`) and its headroom against a caller-supplied cap (`headroom`);
  `ledger.decisions` (`DecisionBook`) -- each Cell's written `HostingPlan` (`record_plan`) and
  each Warden's set `Ceilings` (`record_ceilings`, which also numbers the wire `CeilingsSet`'s own
  revision). `headroom()` computes `Headroom(sub_bees=..., shared_seats=...)`: `sub_bees` is
  shared totals less the Royal Reserve's own headroom-fraction margin and its `seats`, less every
  live grant's `max_sub_bees`; `shared_seats` is `ledger.seats.total_capacity()` less the reserve's
  `seats`, less every live grant's own `SeatReservation.seats`. `record_spend` is the one place
  that keeps a goal's running total (`ledger.spend`) and its grant's own `spent` field
  (`hivemind.forage.ForageGrant.spent`) in step from one write. `set_reserve` replaces the reserve
  and persists it; `restore` rebuilds every in-memory table -- its own three plus each sub-book's
  -- from the store, for a fresh Queen process. `LedgerRecorder` implements
  `hivemind.llm.fanner.LlmEventRecorder` without this package importing `hivemind.llm.fanner`
  itself in the wrong direction (queen may import llm; `record` reads an `llm.call` payload's
  `usage.cost_usd`, `grant_id` and `goal_id` -- present only when the calling `FannerLane` carried
  them, see that class's own docstring on the per-lane attribution grain -- and forwards to
  `record_spend`; `call_started`/`call_finished` forward straight to `ledger.seats`). `LedgerStore`
  (`InMemoryLedgerStore` for tests, `SqliteLedgerStore` for durability -- Appendix C: "Forage
  ledger (SQLite) | Yes") is the persistence seam every mutation writes through, including all
  four of the roadmap step 4.8 tables (its own `0002_add_seats_spend_plans_ceilings.sql`
  migration).
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
- **`requests`**: `ForageRequestOutcome`, `handle_sub_bee_request` -- answer a Warden's
  `ForageRequest` against the ledger's own headroom, through
  `hivemind.queen.autopilot.forage.decide_forage_request`. `SUB_BEES` (roadmap step 4.7) grows the
  existing grant's `max_sub_bees`; `SHARED_SEATS` (step 4.8) grows or adds a `SeatReservation` on
  the named source, checked against `headroom().shared_seats`; `SPEND` (step 4.8) grows
  `spend_budget` within `ledger.spend.headroom(goal_id, deps.budgets.spend_cap_usd)` -- never
  `NEEDS_JUDGEMENT`, since a goal's own cap is not a shared pool another grant could be shrunk to
  relieve. `BINDING` is always denied: picking a higher-grade binding is routing's job (phase 8),
  not a ledger headroom check. `GRANT` commits through `grants.revise`; `DENY`/`NEEDS_JUDGEMENT`
  mutate nothing. The function keeps its roadmap-4.7 name, `handle_sub_bee_request`, because
  `hivemind.queen.ticks.forage` (outside this package's own file list) imports it by that exact
  name; see this package's own dispatch report for the rename a future edit there should make.
- **`hosting`**: `PlanReason`, `write_hosting_plan(cell, deps, warden=None) -> HostingPlan`
  (roadmap step 4.8) -- per Cell, ranks every `[llm.slots]` slot's candidate Forage map sources
  (local-first, VRAM-checked, seat-headroom-aware, cost-capped, sorted by measured distance) and
  writes the result: `_decide_plan` is pure, `write_hosting_plan` is the effectful edge (ledger
  write, then, when `warden` is given, a `waggle.messages.forage.PlanWritten` send over its own
  link, then `forage.plan_written`). The wiring pass (roadmap step 4.10) added the `warden`
  parameter and its send -- mirroring `ceilings.set_ceilings`'s own write-then-send-then-record
  order -- since the module originally recorded the plan but never sent it; `None` (every
  pre-wiring caller and any test that only cares about the decision itself) skips the send. Reads
  `cell.capabilities.can_host_model` as the disconnection-survival proxy roadmap step 4.8 asks for
  (`hivemind.cell.needs.TaskNeeds` carries no such field, and codingrules forbids branching on
  `cell.kind` directly); when true, or when the shared pool has no seat headroom, every non-local
  candidate is excluded outright rather than merely ranked behind, so the plan's own chain never
  names a source the Cell cannot reach while genuinely disconnected. `hivemind.queen.dispatcher.
  _ensure_warden_provisioned` is the one production caller that passes `warden`, on a newly
  attached Warden's first dispatch.
- **`ceilings`**: `set_ceilings`, `change_ceilings` (roadmap step 4.8) -- write a Warden's
  `hivemind.forage.Ceilings` to the ledger (`ledger.decisions.record_ceilings`, which numbers the
  revision), send `waggle.messages.forage.CeilingsSet` over its `WardenLink`, and record
  `forage.ceilings_set` with the old and new `max_sub_bees`, mirroring
  `hivemind.queen.dispatcher`'s own "write, then send, then record" ordering for a grant.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/forage -q
```

The synthetic test roadmap step 4.7 asks for -- fifty Wardens contending for a small shared
capacity, asserting the sum of live grants never exceeds capacity minus reserve at every moment,
not only at the end -- is `tests/unit/queen/forage/test_fifty_wardens.py`.
