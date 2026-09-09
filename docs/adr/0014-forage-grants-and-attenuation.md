# ADR-0014: Forage grants narrow capacity, they never widen it

- Status: Accepted
- Date: 2026-09-08

## Context

The Queen (the central orchestrator) divides the Hive's shared capacity among many Wardens (one
per Cell, the always-on supervisor of a unit of compute), but a Warden must be able to spend
within its allowance without asking the Queen before every model call, and the Queen must still be
able to shrink or recall that allowance when Forage (the Hive's capacity, modelled as data) comes
under pressure. This is the same shape Guard's `CapabilitySet.attenuate` already solves for tool
scope: a holder gets a subset of authority it can act on freely, and authority only ever narrows on
the way down, never widens. Roadmap step 3.12 asks for a pure `grant(cell_capacity, role, tempo,
map, reserve, manifest) -> ForageGrant` computing `max_sub_bees` as the minimum of several
independent limits and an `allowed` set filtered by a tempo-derived grade floor and cost, with the
Royal Reserve (what the Queen holds back for herself, the Attendant and the House Bees) subtracted
first. The computation has to be deterministic and testable without a database, a running Queen, or
a live model call, because codingrules section 8.3 treats decision logic as a pure core.

## Decision

`ForageGrant` is a frozen pydantic value naming exactly what a Warden may draw on: `AllowedBinding`s
(a model slot, a Forage map source id, a maximum effort), `SeatReservation`s bounding concurrent
draws on shared sources, and hard `token_budget`/`spend_budget` ceilings, computed by one pure
function, `hivemind.forage.allocate.grant`, from a single grouped input (`GrantInputs`, a frozen
dataclass holding the Cell's capacity, the role's footprint, the task's tempo, the Forage map, the
Royal Reserve, the goal's budgets, and the holder/cell/task/grant ids plus `now` and a TTL). The
allocator only ever narrows: it starts from the Cell's raw capacity, subtracts the Royal Reserve's
seats and memory, then applies a further headroom-fraction margin on top, and computes
`max_sub_bees` as the minimum across five independent constraints (the Cell's own cap, free CPU
over the role's footprint, free memory over the footprint, reachable model seats, and the goal's
remaining sub-bee cap) so that any single tight resource caps the whole grant, exactly the way
`CapabilitySet.attenuate` lets the tightest of two capability sets win rather than either alone. A
grant's lifecycle state (`GrantState`: `ISSUED`, `ACTIVE`, `EXHAUSTED`, `REVOKED`) lives in its own
single transition table in `forage/grant_state.py`, separate from the grant's terms, so growing or
shrinking a grant — a new `GrantIssued` revision at the wire layer — never itself requires a state
transition; only becoming exhausted, recovering from exhaustion (a top-up), or being revoked moves
this state. Every grant carries a `reason` string recording how each figure was derived, because a
Warden that receives less than the Cell's raw numbers would otherwise suggest needs to know why,
and the Pheromone Trail (the Hive's append-only audit log) needs a human-readable justification for
the decision, not just the resulting numbers.

## Consequences

Positive: a Warden can reason about its ceiling entirely locally, without contacting the Queen
before every model call — the grant itself is the contract, and codingrules 8.10's "grants are
computed, then metered" separates *deciding* the terms (this ADR) from *enforcing* them per call
(the Fanner, ADR-0015). Because `grant()` is a pure function over plain data, it is cheap to
property-test with hypothesis: the whole allocator runs in-process with no I/O, so a test can
assert "a grant never exceeds capacity minus reserve" across thousands of generated inputs in
milliseconds. Because every dimension narrows independently through a single `min()`, a bug in one
part of the computation (say, the memory arithmetic) cannot silently let a grant exceed the cap on
an unrelated dimension (seats) — each constraint is computed and applied on its own.

Negative: a v0 grant is coarse. Every allowed binding in this phase resolves to `ModelSlot.WORKER`,
because a per-role slot mapping and a per-Cell hosting plan (`HostingPlan`, ADR-0016) are Queen
decisions a later phase 3 step writes, not something this allocator can determine on its own yet; a
future dispatch that adds real per-slot bindings will need to extend `grant()`'s slot assignment.
The allocator also has no notion of concurrent claims: each call to `grant()` computes as though it
were the only claim against the Forage map's live figures, with no ledger tracking what other
grants have already reserved from the same shared sources — the roadmap explicitly defers that live
ledger to phase 4, so two grants issued in quick succession against the same tight source could,
for now, both believe the same seats are available.

## Alternatives considered

A single scalar "sub-bee budget" instead of five independent dimensions: simpler to compute and to
serialise, but it would hide which resource is actually the bottleneck, making the trail's `reason`
field meaningless and denying an operator the ability to see, for instance, "you are seat-bound,
not memory-bound," which is exactly the diagnostic codingrules 8.10's "capacity in several
dimensions, never one number" principle exists to preserve.

A boolean "active"/"revoked" flag instead of a four-state `GrantState` machine with `EXHAUSTED`:
would conflate "temporarily out of budget, expected to top up on the next heartbeat" with
"permanently taken back by the Queen," losing a distinction a Warden's own retry and backoff logic
needs to behave sensibly when it hits its ceiling mid-task.

Letting the allocator mutate a shared ledger in place, rather than remain a pure function returning
a fresh value: would tie every test of the allocation logic to a database or an in-memory store with
its own setup and teardown, and would make property-based testing far harder to write and reason
about, directly contradicting codingrules section 8.3's "pure core, effectful edges."
