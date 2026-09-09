# ADR-0016: Shared and local pools are distinct types; a hosting plan is a chain, not a flag

- Status: Accepted
- Date: 2026-09-08

## Context

Roadmap step 3.12's models have to represent two structurally different pools of capacity: the
**shared pool** (Hive Stand seats, hosted seats and spend), which only the Queen (the central
orchestrator) ever divides, by grant (ADR-0014); and a **local pool**, everything physically on one
Cell (a unit of compute), which that Cell's Warden (its always-on supervisor) divides among its own
sub-bees with the same allocator, entirely on its own. Modelling both as one flat structure would
blur who is allowed to change what — the Queen sets a Warden's `Ceilings` once, when it moves onto a
device, and otherwise never touches that Warden's local pool again; the Warden spends its own pool
freely within those ceilings and never asks. Separately, codingrules section 8.10 replaces "a single
hosting flag" (local vs. shared, as one Cell-wide switch) with "a hosting plan": per model slot, a
primary source and an ordered fallback chain, plus one default chain for any slot the plan does not
name, because "spill to the next source when the first is unavailable" needs an actual ordered list
of alternatives, not a single pointer or a boolean.

## Decision

`LocalPool` and `Ceilings` (`hivemind.forage.models.pools`) are separate types from the shared-side
`ForageGrant` and `RoyalReserve` (ADR-0014), so a Warden's code can only ever construct the local
variant — "a Warden never allocates shared Forage for itself" becomes a fact the type system
enforces, not a convention a reviewer has to remember to check. `LocalPool` bundles a Warden's own
`HostCapacity` (already reduced by any local model server's footprint), the `Seat`s that server
offers, and a small reserve that deliberately *reuses* `RoyalReserve`'s exact shape — seats, memory,
a headroom fraction — scoped to that one Cell's own awake mode and Patrol (a Real Cell's Warden with
no active bees, reviewing read-only on a schedule) rather than to the whole Hive, because both
describe the same underlying idea, "hold back a margin before dividing what remains," just at
different scopes; giving them two structurally identical models would have doubled the tests and
the documentation for no behavioural difference. `Ceilings` mirrors the wire
`CeilingsReport` field for field (max sub-bees, VRAM, disk, loadable source ids, exportable seats),
so a Warden's usage report and its own ceiling compare directly with no translation step in
between. `SourceChain` (a primary Forage map source id plus an ordered list of fallback ids) and
`SlotPlan` (one named slot's chain) are the building blocks `HostingPlan` composes: per Cell, for
every named slot a chain, plus one default chain for any slot the plan does not name explicitly —
literally replacing what codingrules 8.10 calls "a hosting flag" with a real, ordered, per-slot
structure. Every chain names its sources by id only, exactly the convention `AllowedBinding`
already established (ADR-0014): the receiver always resolves an id against its own copy of the
Forage map, so a wire message never has to carry — and therefore never has to keep synchronised —
a source's live figures alongside its identity.

## Consequences

Positive: the shared/local split is structural rather than a documented convention someone could
violate by accident; `wardens/local_pool/` (a later phase 3 dispatch) can only ever hold a
`LocalPool`, never mistakenly reach for a `ForageGrant`. Reusing `RoyalReserve`'s shape for
`LocalPool.reserve` means one set of validators and one mental model — "seats, memory, a headroom
fraction, sensible defaults" — covers "hold back a margin" everywhere it appears in Forage, at
whatever scope. `SourceChain`/`SlotPlan` naming sources by id, not by full reference, keeps every
hosting-plan message small and keeps the Forage map as the single source of truth for what a source
currently is.

Negative: `HostingPlan` and its `SlotPlan`s are defined by this dispatch but *written* by the Queen,
a much later phase 3 step — this dispatch ships a plan shape with no writer yet, a risk any
interface-first phase accepts; the risk is mitigated somewhat because the wire form
(`waggle.messages.forage.hosting.PlanWritten`) already exists and constrains the shape independently
of this dispatch's own choices. `LocalPool.reserve` reusing `RoyalReserve` rather than a
purpose-built type is a deliberate shortcut, not a roadmap-named concept; if `wardens/local_pool`
later needs a reserve field `RoyalReserve` does not have (say, a disk-specific margin), a future
dispatch will have to decide whether to diverge the two types or extend the shared one, and either
choice has to be made consciously rather than by accretion.

## Alternatives considered

A single `Pool` model with a `scope: Literal["shared", "local"]` discriminator field, instead of two
distinct classes: would let calling code construct a "shared"-scoped pool with local-only semantics
(or the reverse) and pass mypy anyway, since a discriminated union cannot enforce "only
`wardens/local_pool` ever constructs the local variant" the way two genuinely separate types can.

Giving `SourceChain` and `SlotPlan` the full `SourceRef` (provider, model, host Cell id) instead of
a bare source id: would break the "everything moves through the map" convention `AllowedBinding`
already established for the exact same reason it was rejected there, and would make every hosting
message larger for no benefit, since a receiver always resolves through its own map regardless.

A dedicated `LocalReserve` model distinct from `RoyalReserve`, differing only in which supervisor it
scopes to: more semantically precise on paper, but two structurally identical three-field models
with only their intended caller differing would cost twice the tests and twice the documentation
for a distinction that a docstring already communicates just as clearly.
