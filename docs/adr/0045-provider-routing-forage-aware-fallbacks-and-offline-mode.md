# ADR-0045: Routing is a pure function over the slot's own fallback chain; grade floors come from the manifest and tempo only raises them; measured grades override hand-set ones from the ledger; offline mode is proven by a socket guard

- Status: Accepted
- Date: 2026-10-11

## Context

Phase 8 must make "routing weigh Forage and where the model runs", and prove that the Hive "can
run with no external network". Today the Fanner walks a bound model's fallback chain and spills
past a source on three static reasons (codingrules 8.10), and the allocator, the spill
rule and the hosting plan each read the hand-set `spec.grade` directly.

The roadmap text this ADR covers:

- 8.1: "pure `choose_binding(slot, tempo, required_capabilities, forage, hosting, manifest)`
  honouring fallback chains, required capabilities, Forage headroom, model location relative to the
  bee, cost caps, `offline`, and the task's tempo. It reads the Forage map: tempo's accuracy bar
  sets a minimum grade, its latency budget a maximum distance, and among sources that satisfy both
  and have abundance the cheapest wins. The binding carries the model's effort setting, chosen from
  the same two inputs, so an urgent low-bar task lands on a fast local model at low effort and a
  critical one on the strongest slot at high effort. Each role has a default grade floor in the
  manifest (the Queen the highest available; the Attendant and Ripener low) that tempo raises but
  never lowers, and a rebind on escalation raises the floor for that bee. ... Fallbacks are
  `llm.fallback` events; a fallback that needs more Forage becomes a `ForageRequest`."
- 8.5: "With `--update-map` the harness writes measured per-slot scores back onto the Forage map as
  `measured_grade`, so hand-set grades are replaced by evidence and routing's grade floors are
  grounded in the Hive's own results."
- 8.6: "`docs/manifests/offline.toml`; a `local_llm` e2e job runs the haiku goal ... with no network
  egress."
- Exit criteria: "Switching any slot between providers changes only the manifest; a checksum of
  `src/` is identical between the two configurations in the e2e job."

Codingrules 8.6 says "Offline is a first-class mode": with `[llm] offline = true` the registry
refuses any provider whose base URL is not loopback. Codingrules 8.10 says the grade is "hand-set,
then replaced by `measured_grade` from the eval harness" and that "a bee uses a local binding
whenever its grade clears the tempo floor and a seat is free". Codingrules 8.14 says routing "picks
the slot, the provider and the model's effort setting from tempo and Forage together" and that
"effort is a per-binding value, never a global".

The forces:

- Roadmap 8.1 writes the signature as returning a `BoundModel`, but a caller also needs to know why,
  what was passed over, and whether capacity is short. A bare binding discards that.
- A router that could name any source on the map would let a Worker reach a model the manifest never
  bound to its slot, which the Guard's `binding_check` and the allocator's `allowed` set exist to
  stop.
- The manifest cannot be both the operator's hand-edited file and a place the harness writes to.
- `llm` may not import `manifest` (codingrules section 4), so the roadmap's `manifest` argument
  cannot be taken literally.
- "No network" is a claim; the loopback check on a base URL proves the configuration, not the
  behaviour of the process.

## Decision

**Routing is pure and lives in a package, `llm/routing/`.** Its one entry point is
`choose_binding(slot, tempo, required_capabilities, forage, hosting, rules) -> RoutingDecision`.
It takes `Tempo` and capability flags (not `TaskNeeds`, so `llm` stays below `cell`), the Forage
map and the Cell's hosting plan as data, and a `RoutingRules` value in place of the manifest. It
does no I/O and holds no state, so every case is a table test.

**It reorders the slot's chain and never invents a binding.** The candidates are exactly the keys
reachable by walking the slot's manifest fallback chain. The result is that chain with the chosen
binding first and the rest in rank order. A source on the map that no chain of the slot names is
never a candidate, however good it is. `RoutingDecision` carries the ordered binding, the effort,
the reason as data, the candidates passed over each with why, and, when nothing has a free seat, a
Forage shortfall.

**Floors come from the manifest and tempo only raises them.** `RoutingRules` carries per-slot grade
floors from `[llm.floors]`. The defaults are: the Queen 5, capped at the highest grade available;
the Attendant, Ripener, Embedder and Transcriber 1; every other slot 2. The tempo's accuracy bar
raises a floor through `grade_floor`; it never lowers one. A rebind on escalation passes a
`raised_floor`, for that bee only. An eligible source has
an effective grade at or above the floor, every required capability, a measured distance within the
latency budget (when a distance has been measured), a cost within the goal's spend cap, and, when
`offline` is set, a local provider.

**Ranking and effort.** Among eligible sources: local to the bee's own Cell first, then a free
seat, then cheapest, then closest. Effort comes from tempo and the row's own setting: a LOW accuracy
bar or a latency budget under ten seconds gives LOW; CRITICAL gives HIGH; anything else keeps the
row's effort.

**Shortfall becomes data, then a request.** When the chosen source needs capacity the bee does not
hold, the decision's shortfall is turned by the Warden into a `ForageRequest` through
`wardens/requests.py`, answered only while connected. Each move down the chain is recorded as an
`llm.fallback` event with the reason, beside the Fanner's runtime `llm.spill` (which still decides
at call time whether the queue has waited too long). Routing decides the order; the Fanner decides,
call by call, when to leave the front of it.

**Measured grades override hand-set ones, from the ledger.** `ModelSourceSpec.measured_grade`
(default `None`) is added, and `ModelSource.effective_grade` is `measured_grade` when set, else
`grade`. Allocation, spill, hosting plans and routing read `effective_grade` and nothing reads
`spec.grade` directly. `hive llm eval --update-map` persists each measured grade in the Queen's
ledger store (table `forage_ledger_measured_grades`, migration 0003), and `build_forage_map`
applies those rows over the manifest's grades when it builds the map. The manifest file is never
written by the Hive.

**Offline mode is proven, not asserted.** The registry check stays (a non-loopback provider is
refused at load), and phase 8 adds an end-to-end job that runs a goal against a scripted loopback
server with a socket guard installed: any `connect` to a non-loopback address fails the test with
the address. The same job runs on the Hive Stand and in a Virtual Cell, and compares a checksum of
`src/` across the hosted and local configurations, so "only the manifest changed" is a test result.

## Consequences

Positive: routing is testable without a Hive, and a wrong binding is a bug in one function. The
manifest chain stays the operator's statement of what each slot may use, so routing cannot widen
what the Guard and the allocator already authorised. A slot moves from hosted to local by editing
the manifest, and the measured grade replaces the guess without anyone editing a number by hand.
The decision's reason data feeds the trail, `hive llm slots` and the eval report from one source.

Negative: because routing only reorders, a manifest whose chain lacks a strong enough binding has
a shortfall routing cannot fix; it can only say so. Measured grades live in the ledger, so a Hive
rebuilt from the manifest alone starts with the hand-set grades until the harness is run again;
Requeening (phase 13) must carry the table across with the rest of the ledger. The ranking order is
a policy encoded in one function and a wrong order is felt on every call, so the eval harness
reports quality and latency per tempo to tune it. The socket guard covers sockets opened by
the test process; anything it cannot see, such as a child process, is named in the job's header
rather than assumed clean.

## Alternatives considered

Routing inside the Fanner: the Fanner already spills, but it is the place seats are enforced and
queues are ordered, runs per call, and is hard to test without time. Putting the policy there would
mix "which binding" with "when to wait", and the Warden's spawn-time `allowed` check could not use
it.

Letting routing pick any source on the Forage map: the best possible binding, but it bypasses the
slot's declared chain, the Guard's `binding_check` and `authorize_binding`, and the grant's
allowed set, so the operator could no longer read the manifest to learn what a slot may call.

Returning a bare `BoundModel` as the roadmap sketch does: smaller, but the reason, the passed-over
candidates and the shortfall would have to be recomputed by whatever logs them, and a shortfall
would be indistinguishable from a bug.

Writing measured grades back into the manifest TOML: no new table, but it rewrites an
operator-owned file (losing comments and ordering), makes the manifest differ from run to run, and
breaks the exit criterion that switching configurations changes only the manifest.

Keeping two grades and having each caller choose: every reader of `spec.grade` would need to
remember to prefer the measured one, and one would eventually forget.

Letting tempo set the floor outright: an urgent task could drop a Queen decision onto a weak
model. Floors are a safety net, so tempo is allowed to ask for more, never less.

Proving offline mode by the loopback check alone: it catches a wrong URL but not a library that
phones home, a model hub download or a telemetry call. The socket guard catches those.
