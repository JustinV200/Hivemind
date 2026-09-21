# ADR-0028: Placement is a pure function that returns either a Real Cell to reuse or a spec to provision, with hard rules before preference

- Status: Accepted
- Date: 2026-09-21

## Context

Until phase 5, placement had one kind of candidate, the attached Wardens' Real Cells, and refused
any task with `isolation = "required"` outright. Phase 5 adds Virtual backends, so placement now
chooses between borrowing a machine and making one. The inputs are all known before the choice:
`TaskNeeds`, the Cell inventory, each candidate's Cell Wax, the Forage ledger's headroom, backend
headroom and the `[placement]` manifest section. Codingrules 8.7 requires the decision to be pure
and its reason to be on the trail. Codingrules 15 requires that an isolation-required task never
lands on a Real Cell, whatever the operator prefers.

## Decision

**`queen/placement/decide.py: decide(needs, inventory, forage, policy) -> Placement` is pure, and
`Placement` is a union:** `ReuseReal(cell_id, warden_id)`, `ReuseDormant(cell_id)` (an Overwintered
Virtual Cell with the right image), or `ProvisionVirtual(spec, backend)`. Every variant carries a
`reason` naming the rule that decided and any Cell Wax that weighed on it; the dispatcher records
it as `queen.placed`. `decide` performs no I/O: the caller precomputes blocked and cautioned Cells,
headroom and the dormant list.

**Hard rules run first and preference last, in a fixed order, each rule its own small function
with its own test:**

1. `isolation = "required"` is always Virtual. No manifest setting overrides it.
2. `comb_shield = NIGHT_VEIL` is always Virtual, freshly provisioned, and only when the request is
   human-originated (the constraints live in `queen/placement/policy.py`, step 5.7a).
3. A Real Cell with a `BLOCK` Cell Wax is excluded; `allow_hive_stand = false` excludes the Hive
   Stand.
4. A candidate must fit: OS, network scopes, and a display or the ability to start one when the
   Exoskeleton is asked for.
5. Forage must cover the grant: a Real Cell needs free capacity, a backend needs headroom.
6. Among what remains, honour `prefer = "real" | "virtual"` (with per-role overrides), rank a
   `CAUTION`ed Cell behind a clean one, prefer a dormant Cell over a fresh provision, and break
   remaining ties by attachment order, so the single-Warden default is unchanged.

If the preferred side has no candidate, the other side is used and the reason says why (this is
how a `BLOCK` on the Hive Stand turns `prefer = "real"` into a Virtual placement). If neither side
has one, `PlacementError` names every rule that eliminated a candidate.

**`decide` is one of the two places that may read `cell.kind`** (the Undertaker is the other), and
it does so only to sort the inventory into the two sides.

## Consequences

Positive: every exit criterion of phase 5 that concerns placement is a unit test over plain data.
The reason string makes `hive trail` explain itself. Safety rules cannot be reached by
configuration because they run before `prefer` is read.

Negative: the caller has to assemble a complete, consistent snapshot of inventory and headroom
before each decision, and that snapshot can be stale by the time a backend provisions; a failed
provision re-enters placement with that backend's headroom set to zero. `prefer` is coarse: it
cannot express "virtual for anything that installs packages", which would need a planner-set
`TaskNeeds` field instead.

## Alternatives considered

A scoring function over all candidates: hides hard rules among weights, so a large enough
preference could outvote isolation. Letting an awake Queen episode choose: placement would then
need a model to run at all, and would stop being reproducible; the Queen may still set `TaskNeeds`
during planning, which is where judgement belongs. Placement that provisions: mixes a pure decision
with the slowest I/O in the system and makes it untestable without a backend.
