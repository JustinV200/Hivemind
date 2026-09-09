# ADR-0020: The LLM layer records its own trail events

- Status: Accepted
- Date: 2026-09-08

## Context

ADR-0002 fixed the hivemind layer table and placed `llm`, `manifest`, `pheromone` and `forage`
together at Layer 1 as independent siblings, with the single in-layer edge `llm -> forage` so that
a grant or an autopilot rule can name a `ModelSlot` (a named place a model call resolves to)
without importing provider machinery. Phase 3 is the first phase in which the LLM layer changes
state that the Pheromone Trail (the Hive's append-only audit log) must record: every model call
spends tokens and money (`llm.call`), a degradation ladder that steps down a rung or moves to a
fallback binding (`llm.fallback`), and the Fanner (the seat meter every model call passes through,
codingrules section 8.10) spilling a call along a hosting plan's chain (`llm.spill`). Codingrules
section 12 already lists `llm.call` among the events a Night Veil purge removes, section 8.10
says the Fanner "measures speed and latency and updates the map and ledger", and section 8.6 says
the trail and the cost view read only the normalised `Usage`, so the vocabulary and the writer were
always meant to be the LLM layer itself. But `hivemind.llm` and `hivemind.pheromone` were ranked
as siblings, and `import-linter` correctly rejected the first module that tried
(`hivemind.llm.ladders.observer -> hivemind.pheromone`). The alternative, injecting a trail-writing
observer from a higher layer into every ladder and into the Fanner, would leave the one place seat
counts are enforced unable to record the enforcement it performs, and would push the `llm.*`
event vocabulary's only writer up to Layer 4 or above, where nothing else needs it.

## Decision

Layer 1 becomes two ranks. `llm | manifest` sit above `pheromone | forage`. `llm` imports
`forage` (for `ModelSlot`, `Effort`, `Tempo` and the Forage map, exactly as before) and now also
`pheromone`, so the ladders and the Fanner record their own `llm.call`, `llm.fallback` and
`llm.spill` events through the `PheromoneTrail` protocol with the id-and-count payloads the event
validator allows. `manifest` imports `forage` for the section models it embeds (`RoleFootprint`,
`ModelSourceSpec`, `RoyalReserve`) and nothing else at Layer 1. `pheromone` and `forage` import
nothing else at Layer 1 in either direction: `pheromone` is the audit sink every layer above it
writes to and reads nothing from any of them, and `forage` still never imports `llm`, a rule that
keeps its own `import-linter` contract as well as its place in the layers contract. Every other
row of ADR-0002's table is unchanged; this ADR amends the Layer 1 row only and ADR-0002 otherwise
stands.

## Consequences

Positive: the writer of an `llm.*` event is the code that performed the action, which is the only
arrangement under which codingrules section 12's rule ("every state-changing action writes a
PheromoneEvent before the action is considered complete") can be enforced inside the Fanner rather
than hoped for at every call site. The ladders' `TrailLadderObserver` and the Fanner need no
injected trail adapter from a higher layer, so a Worker, a Warden and the Queen configure model
access with the same two objects (a `BoundModel` and a `PheromoneTrail`) and nothing else.
`pheromone` stays importable from every layer that changes state, including the two Layer 1
siblings above it, which is what an audit sink is for.

Negative: `llm` gains a second in-layer dependency, so a change to `PheromoneEvent`'s validator
(the payload rules) can now break the LLM layer's tests, not only the stores'. The layers contract
in `pyproject.toml` has two Layer 1 ranks where it had one, which every reader of codingrules
section 4 has to learn; the table there now shows both ranks so the picture in the document
matches the contract that enforces it.

## Alternatives considered

Injecting a trail observer from a higher layer into every ladder call and into the Fanner: keeps
Layer 1 flat, but makes the seat meter unable to record what it enforces, spreads the `llm.*`
vocabulary's only writer across every caller, and adds a constructor parameter to every bee for a
concern none of them owns.

Moving `pheromone` to Layer 0 beside `common`: would also let `llm` import it, but `common` "never
grows domain logic" (codingrules section 3) and the trail's event families are pure domain
vocabulary, so it would have put bee terms into the one package that must know nothing about
bees.

Recording `llm.*` events from the Worker runtime after each call returns: the simplest code, but
it records only calls the runtime saw and never those the Queen's awake episodes, the planner or a
Warden's own awake mode make through the same ladders, so the trail would silently miss the
Queen's own spend.
