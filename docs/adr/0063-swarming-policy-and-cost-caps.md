# ADR-0063: Swarming is a pure cap on fresh Virtual Cells, read at every placement

- Status: Proposed
- Date: 2026-10-27

## Context

Swarming is the Hive scaling its fleet up under load and back down after it (README, "Swarming
scales the fleet up within Forage"). Since phase 5 the dispatcher already provisions one fresh
Virtual Cell for a ready task whenever placement picks the Virtual side, beside her tick and
bounded by `_DEFAULT_PROVISIONS_IN_FLIGHT`, and releases it when the task ends (ADR-0028,
ADR-0029). What was missing is the policy over that: nothing said how many Cells may run at once
across every backend, how many any one backend may hold beyond the cap it declares, what they may
cost an hour together, or whether the Hive Stand that hosts a local backend has room for one more.
The only bound was each backend's own `max_cells`, and a cloud backend (`PricingTag`) could be
asked for Cells until its cap with no word about money.

Roadmap 13.3 asks for `queen/scheduler/swarming.py`: a pure policy for provisioning more Virtual
Cells from queue depth and Forage headroom, with per-backend limits and cost caps.

## Decision

**A pure policy, no model.** `hivemind.queen.scheduler.swarming.plan_swarm(view, policy)` takes a
`SwarmView` (the ready tasks with no Cell on the way, each backend's Cells held, Cells being
made, remaining declared headroom, and the Hive Stand's room in fresh Cells) and a
`SwarmingPolicy`, and returns a `SwarmPlan`: per backend, how many fresh Cells it may start now,
and in plain words why when that is zero. It imports nothing from `hivemind`, does no I/O, reads
no clock, and `hivemind.llm` is forbidden to it by the same import contract as autopilot, so
Swarming keeps working with every provider down.

**Every bound is a minimum.** A backend may start
`min(demand, declared headroom, its own max_cells left, max_parallel left, stand room, cost room)`
fresh Cells, where:

- demand is the ready tasks not already covered by a Cell being made, never below one for the task
  being placed (each placement is for a task that has no Cell on the way);
- `max_parallel` counts every Virtual Cell held (dormant included: it still holds disk and, on a
  cloud, money) and every one being made, across every backend;
- stand room counts only for a backend marked `draws_on_hive_stand`: the Hive Stand's free memory
  and idle cores, measured live, divided by the fresh Cell's reservation;
- cost room is `max_cost_per_hour_usd` less the summed hourly price of every Cell held or being
  made, divided by this backend's price; a backend priced at zero is never cost-capped.

An unset bound does not bind. A Hive whose manifest sets none of these provisions exactly as before.

**Gated where placement already looks.** The dispatcher's snapshot builds the view and marks a
backend whose allowance is zero held back, with the policy's reason (`VirtualBackendCandidate.
held_back`), the same seam the provision backoff uses, so `decide` stays unchanged and the task's
one `queen.decided` names the cap ("swarming: max_parallel 2 reached"). A held-back task waits
PENDING and is placed on a later pass, when a Cell is released or a provision lands. The plan is
recomputed for every placement decision, and each decision provisions at most one Cell, so two
backends sharing a pool-wide budget never overspend it between them. Resuming a dormant Cell is
never gated: it makes nothing new.

**Scale-down stays where it is.** A Cell whose task ended is released beside the tick and
Overwintered or torn down (ADR-0029), so with no work left the fleet returns to the dormant pool,
or to zero when Overwintering is off. Swarming adds no idle timer of its own.

**Configuration.** `[virtual_cells.swarming]`: `max_parallel`, `max_cost_per_hour_usd`, and
`[virtual_cells.swarming.backends.<name>]` with `max_cells`, `cost_per_hour_usd` and
`draws_on_hive_stand`. Prices are stated by the operator, not read from the backend, so a cost cap
holds before any Cell exists to report spend.

## Consequences

- Queue depth reaches the policy through `DispatchBook.queue_depth`, written each time a dispatch
  pass reads its ready tasks; a placement outside a pass (a resumed task) reads the last figure,
  and the floor of one keeps it placeable.
- A Cell counted both by the lifecycle (its provision returned) and as still being made (its
  handshake not yet settled) is counted twice for a moment. That only ever lowers an allowance.
- Stand room is opt-in per backend: a reservation is a ceiling, not a use, and a Hive Stand whose
  free memory sits under one reservation would otherwise never swarm at all.
- Proved in-process by `tests/e2e/test_swarming.py`: six independent tasks under
  `max_parallel = 2` scale the fake backend to two Cells, never more, and back to zero.

## See also

- ADR-0028 (placement), ADR-0029 (Overwintering), `hivemind.queen.dispatcher.backoff`.
