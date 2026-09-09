# ADR-0015: The Forage map splits static identity from live figures; the Fanner meters, forage does not

- Status: Accepted
- Date: 2026-09-08

## Context

Every model call needs to know which source will serve it, how strong that source is, how far it
is from the calling Cell, and whether it is actually free right now — and the second half of that
question changes on nearly every call (seats fill and empty within milliseconds, latency drifts
with load) while the first half is set once by an operator in the Hive Manifest and almost never
changes. Conflating the two in one place would force a choice between revalidating an entire
source's static configuration on every live update (wasteful, and a needless place for a bug to
corrupt configuration a human wrote) or exposing live figures through a channel that has to import
the manifest to make sense of them — impossible, since `forage` sits below `manifest` in the layer
table (codingrules section 4) and may never import it. Separately, something has to be the one
place that actually enforces a seat count on a live call, not merely computes what a grant permits;
roadmap step 3.12a assigns that job, "the Fanner," to `llm/fanner.py`, and this dispatch has to
decide how the two halves — the map this dispatch owns, and the meter a later dispatch owns — meet
without either one importing across the forbidden `forage` → `llm` direction.

## Decision

`ModelSource` (`hivemind.forage.models.sources`) is split into a static `ModelSourceSpec` — exactly
what the Hive Manifest's `[forage.map.<source_id>]` table embeds: provider, model, hand-set grade,
context window, cost, capability flags, seat count — and a live half made of two further models,
`Distance` (measured latency and generation speed from a given Cell, since a model on the Hive
Stand is farther from a remote device than one on the device itself) and `Abundance` (free seats,
or rate-limit headroom for a hosted provider). `hivemind.forage.map.ForageMap` owns a collection of
these, keyed by `source_id`, under exactly one `asyncio.Lock`, but only its two live-updating
methods, `observe` and `set_abundance`, actually take that lock: the three read methods (`get`,
`sources`, `for_slot`) stay synchronous and lock-free, because every `ModelSource` is itself frozen
and a single dict lookup is atomic under the GIL, so a concurrent reader can only ever see a whole
old value or a whole new one, never a value half-replaced mid-write. `RoleFootprint` (cpu, memory,
one seat while mid-call, an estimated token rate, extra memory for an Exoskeleton) lives beside
these models but carries no `role` field of its own; the manifest's `[forage.roles.<role>]` table
keys it externally by the lowercase `waggle.messages.task.WorkerRole` member name, the convention
every per-role table in the Hive follows, so the type itself stays reusable regardless of how a
manifest section happens to be organised. The Fanner is deliberately *not* part of this package: it
is the seat meter every model call actually passes through, per codingrules section 8.10, and it
lives in `hivemind.llm` — one layer above `forage` — because metering a live call means holding a
semaphore and touching provider machinery, both Layer-2-and-above concerns `forage` may never
import. The seam between the two is the map itself: this dispatch decides the map's shape and what
a grant computes from it; the Fanner, in its own dispatch, calls back into `ForageMap.observe` and
`ForageMap.set_abundance` with what it actually measured on each call, so `forage` decides policy
and `llm` decides enforcement, and neither imports the other in the forbidden direction.

## Consequences

Positive: an operator can hand-set a source's grade in the manifest and have `forage.allocate.grant`
immediately reason about routing floors against it, while the live figures update independently as
calls land, with no risk of the two halves drifting inconsistently against each other, because
`model_copy` replaces a frozen value wholesale rather than mutating one field in place. Splitting
synchronous, lock-free reads from the two async, lock-guarded writers keeps `for_slot` cheap enough
to call from pure code like `forage.allocate.grant` without forcing that function to become async
itself. Because `ModelSource.source_ref()` builds the wire `SourceRef` from the static half alone,
a message that names a binding or a chain never has to carry (and therefore never has to keep
synchronised) the live figures a receiver would just re-measure locally anyway.

Negative: `ForageMap` has no notion of "this source is currently mid-measurement," so two Fanner
calls updating the same source's abundance in quick succession will simply overwrite each other's
figure rather than merge or average them; acceptable for v0, where the Fanner is the map's sole
writer and always writes the latest true figure, but a later phase that wants trend data (a moving
average of tokens-per-second, say) will have to add that layer on top of `ForageMap`, not inside it.
Splitting metering into `llm.fanner` also means nothing in `forage` alone can answer "is a seat free
right now with certainty" — `Abundance.seats_free` is only as fresh as the last report, so a true
real-time answer always requires the Fanner, a boundary this dispatch accepts deliberately rather
than duplicate live enforcement in two layers.

## Alternatives considered

Revalidating and rebuilding the entire `ModelSource` (spec included) on every live-figure update,
rather than splitting a static spec from a live half: would waste CPU re-checking provider, model,
cost and capability fields that cannot have changed since the manifest loaded, and would blur the
boundary between "what an operator configured" and "what was just measured" in every log line and
every test.

Metering seats inside `ForageMap` itself, with a semaphore per source rather than deferring to a
separate Fanner: would pull provider call semantics — retries, streaming, cost accounting, when a
call actually starts and ends — into Layer 1, and would force `forage` to import `hivemind.llm` to
know those things, directly violating the layer table codingrules section 4 fixes.

One lock guarding every `ForageMap` method, reads included: simpler to reason about as a single
invariant ("always hold the lock"), but it would needlessly serialise every pure lookup behind an
async lock acquisition, forcing every synchronous caller — including the pure allocator — to become
async for no correctness benefit, since frozen values already make unlocked reads safe.
