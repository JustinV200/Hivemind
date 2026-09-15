# ADR-0023: The Forage ledger is a persisted book of reports and grants, headroom is derived from it, and hosting plans and hosted rate limits are written from measurements

- Status: Accepted
- Date: 2026-09-15

## Context

Phase 3's allocator (ADR-0014) computed a grant from one Cell's capacity report and the manifest,
with no memory of what had already been granted; the Queen could therefore hand two Wardens the
same seats. Phase 4 makes Forage (the Hive's capacity, in several dimensions) a live ledger the
Queen divides: every Cell's latest `ForageCapacity`, the Fanner's rolling measurements, seats in
use per server and provider, spend per grant and per goal, the Royal Reserve, and live grants.
Codingrules Appendix C says that ledger survives a Queen crash and is reconciled against fresh
capacity reports on Requeening, and rule 4 of the same appendix says headroom is a derived view
that is never stored. Separately, the Forage map's two hosted rate fields were written by nothing:
a hosted provider's real pressure never reached routing, and a 429 with a `retry-after` was parsed
by both adapters and then dropped. The decisions below fix who owns which numbers.

## Decision

**The ledger is a persisted store of facts; headroom is computed from it on every read.**
`queen/forage/ledger` is built on a `LedgerStore` Protocol with an in-memory implementation and a
SQLite implementation with its own migrations, holding capacity reports, measurements, seat usage,
spend and grants. Headroom (shared totals minus the Royal Reserve minus the sum of live shared
grants) is a function over those rows and is never written down, so a crash can never leave a
stale headroom figure that disagrees with the grants that produced it.

**Local pools are reported, never granted.** A Warden's usage against its `Ceilings`, and any seats
it exports to the shared pool, appear in the ledger as reports. The allocator reads them to size
hosting plans and to know what a Nuc (a Real Cell with its own Warden and model server) can bear;
it never hands local capacity out. This is why `hivemind.wardens.local_pool.LocalPool` is renamed
`SubBeeSlots` in the same step: the ledger is the first module that must hold both meanings in one
file, and `LocalPool` belongs to `forage` (codingrules 6.1).

**Grants are leases with one state table.** A grant is renewed on its Warden's heartbeat, returns
to the pool at `expires_at` when the Warden is dead or offline, can be shrunk or revoked, and every
edge goes through `forage/grant_state.py` and writes a `forage.*` event. A Warden over its grant
gets a `GRANT_EXCEEDED` Alarm, never a crash. A `ForageRequest` within headroom is answered by an
autopilot rule with no model call; a contested one (only satisfiable by shrinking another live
grant) goes to an awake decision.

**Hosting plans and ceilings are written by the Queen from the map and the ledger, with a
reason.** `queen/forage/hosting.py` writes each Cell's `HostingPlan` (per slot a primary source and
a fallback chain, local first wherever the Cell has local sources) from free VRAM against each
model's requirement, seat pressure on the Hive Stand, measured distance against the task's tempo,
the task's need to survive disconnection, and cost caps; recorded as `forage.plan_written`.
`queen/forage/ceilings.py` sets a Warden's `Ceilings` once and records `forage.ceilings_set`; within
them the Warden never asks.

**Hosted headroom is measured from the provider's own headers, and a throttle is a fact on the
map.** `LLMResponse` carries an optional `RateLimitSnapshot` each adapter fills from its response
headers and leaves `None` where the provider publishes none (every local server), and
`ForageMap.set_abundance` writes both halves so an unmetered source keeps its `None`s rather than
invented numbers. A `RateLimitedError`'s `retry_after_s` puts that source's headroom at zero until
the window passes, recorded once as `llm.throttled`; the Fanner spills past a throttled source; the
per-provider rate limiter prefers reported figures over the manifest's configured ones once it has
them.

## Consequences

Positive: the sum of live grants can never exceed capacity minus reserve, and a synthetic test
with fifty Wardens asserts it. A Warden's heartbeat stopping returns its seats without any human
noticing. A provider that says "wait" is not asked again until the wait is over, and the next
source in the chain absorbs the work. Manifest rate limits become starting guesses instead of
permanent ceilings.

Negative: a second SQLite store with its own migrations is more surface to keep in step with the
Brood Chamber and the memory tables; Requeening (phase 13) will have to reconcile it against fresh
reports rather than trust it blindly. Hosting plans are written now but only consumed by routing in
phase 8, and a local source is only selectable once phase 8.3 can start a server on a Cell, so the
plan writer ships ahead of its reader. Provider header names are vendor facts that can change; the
adapters keep them inside `mapping.py` so a change is one file.

## Alternatives considered

An in-memory ledger rebuilt from trail events on start: simpler now, but the trail is an audit
log, not a query store, and replaying every `forage.*` event to recover grants would make Queen
start time grow with Hive age.

Storing headroom as a column updated on every grant: one read instead of a sum, but a crash
between the grant write and the headroom write would leave the two disagreeing, which is exactly
the class of bug a persisted ledger exists to remove.

Letting a Warden request local capacity through the same `ForageRequest` path: uniform, but it
would make the Queen the bottleneck for work that is already on the Warden's own machine, and
codingrules 8.10 says a Warden never asks for what is on its Cell.

Trusting manifest rate limits and retrying on 429 with backoff inside the adapter: hides the
pressure from routing entirely, so every call would keep choosing the throttled provider and
waiting, instead of moving to the next source in the chain.
