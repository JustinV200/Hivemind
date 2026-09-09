# ADR-0002: Workspace layout and layering

- Status: Accepted
- Date: 2026-09-07
- Amended by: ADR-0020 (the Layer 1 row only; everything else here stands); ADR-0018 (guard becomes its own rank below its Layer-2 siblings)

## Context

HiveMind (the project's name for the whole system) has three very different runtime footprints that
must not collapse into one another: the Queen (the central orchestrator, a kernel-shaped always-on
process) and every subsystem it coordinates; a shared protocol, Waggle, that both the Queen's side
and a bare device connector need without either depending on the other's weight; and Pollen (the
thin gateway on an enrolled device, driven over a signed terminal session by the Warden that
supervises that device), which must install on constrained hardware such as a Raspberry Pi with no
Docker, no SQLite extensions and no LLM SDK. Provider neutrality (codingrules section 2 and section
4) means the code that talks to a vendor SDK has to sit behind a protocol boundary that the rest of
the system cannot see past, and the same shape of problem applies to `subprocess`, which only a
handful of subsystems are allowed to touch (codingrules section 4). Autopilot (the deterministic,
model-free dispatch table every event goes through before an awake episode is even considered) has a
hard requirement to never await a model, which only holds if it is structurally impossible to import
the LLM layer from an `autopilot/` directory, not merely a convention. Finally, the front end must
be reachable from any enrolled device, phones and Android included, which means it is a package in
its own right with its own build and test tooling, not a folder bolted onto the Python workspace.

## Decision

The workspace is a `uv` workspace with four packages under `packages/`: `waggle` (the shared
protocol and the primitives even Pollen needs), `hivemind` (the Queen and every control-plane and
Hive subsystem), `pollen` (the device connector, depending on `waggle` alone) and
`observation-web` (the TypeScript and React front end). Inside `hivemind`, subsystems are arranged
into the eight-layer table below, imports flow strictly downward, and the table is the single
source of truth that the `import-linter` contracts in the root `pyproject.toml` encode
mechanically, so a violating import fails CI rather than surviving as a lint warning or a review
comment.

```text
Layer 7  entrance, observation, cli
Layer 6  queen
Layer 5  wardens
Layer 4  workers
Layer 3  hive, swarm, exoskeleton, royal_jelly
Layer 2  cell, brood_chamber, honey_store, memory, supervision, guard
Layer 1  manifest, pheromone, llm, forage
Layer 0  common
──────── waggle (separate package)
```

Layer 7 is the edges: HTTP, terminal and dashboard. Layer 6, the queen, is the kernel: the only
global view, and the subsystem that divides Forage (capacity as data) among Wardens. Layer 5,
wardens, are the per-Cell supervisors that spawn and supervise Workers. Layer 4, workers, are the
roles that do the work. Layer 3 holds the sources of Cells and the capabilities handed down to
them. Layer 2 is the Cell abstraction plus state, memory and policy. Layer 1 is foundational
services, with capacity expressed as data. Layer 0, common, holds primitives and imports nothing
internal. Waggle sits outside the layer numbering, in its own package, usable by every layer, and
imports nothing from `hivemind`.

The corollaries below are decisions in their own right, not just restatements of the table:

- `common` and `waggle` know nothing about bees. `waggle` provides envelopes, ids, the clock and
  the loop shape, because `pollen` needs those too and may import nothing from `hivemind`;
  `common` provides errors, results, logging setup and migrations, and never grows domain logic.
- `cell` (the Cell abstraction, meaning any machine or sandbox a bee can run on) is the home of
  `TaskNeeds` and the three security enums, `AccessLevel`, `CombShieldLevel` and `HoneyClearance`;
  `guard`, `hive`, `honey_store` and `supervision` import them from there, and nothing at Layer 2
  or below imports `guard` for an enum.
- Within Layer 1, `llm` imports `forage`, never the reverse; `ModelSlot` and `Tempo` live in
  `forage` so that grants, routing inputs and autopilot rules can name a slot or read a tempo
  without touching `hivemind.llm`.
- `wardens` imports `workers` (to spawn them) and `queen` imports `wardens` (to assign work to
  them); a Worker never imports its Warden and a Warden never imports the Queen, they talk over
  Waggle through the `Supervisor` protocol instead.
- `pollen` depends only on `waggle` and nothing else in the workspace.
- `subprocess` is importable only from `hivemind.cell.*`, `hivemind.hive.backends.*`, the dev
  sandbox (`hivemind.royal_jelly.quarantine_comb.sandbox_subprocess`) and `pollen.*` (plus
  `scripts/`, which sits outside the packages entirely).
- Any module under a directory named `autopilot/` may never import `hivemind.llm`, directly or
  transitively.
- `tests/unit/` mirrors `src/` one to one; CI checks for orphan modules with no matching test file.

How the table is enforced: one `import-linter` contract of `type = "layers"` lists the layers from
Layer 7 down to Layer 0, with same-layer independent siblings joined by `|` (for example,
`"entrance | observation | cli"`). `cell` is not joined to its Layer 2 siblings with `|`; it is
ranked as its own sub-layer immediately below them, and `forage` is ranked as its own sub-layer
immediately below `llm`, `manifest` and `pheromone`, for the same reason in both cases: a probe of
import-linter's containers-and-siblings behaviour showed that its `:` syntax for declaring
non-independent siblings is symmetric, so joining `cell` to `guard` with `:` to allow the one-way
`guard -> cell` edge would also have opened the reverse `cell -> guard` edge, which the corollary
above forbids outright. An ordered sub-rank gives the one-way edge without opening the one that
must stay closed. Any sibling dependency a later ADR approves is added to this workspace by editing
that one `layers` contract, which makes the contract the machine-readable list of every
ADR-approved sibling edge, not a second, independent guess at what the ADRs say. The vendor-SDK
contract (only `hivemind.llm.providers.<name>` may import a vendor LLM SDK) and the subprocess
contract listed above both carry `unmatched_ignore_imports_alerting = "warn"` rather than the
default `"error"`, because at this roadmap step neither the provider adapters nor the Cell-session
code that would exercise those carve-outs exists yet; every ignored import pattern currently
matches zero real imports, and the default would hard-fail every CI run until later phases land the
adapters. `"warn"` still surfaces a visible note if a carve-out path is ever typoed, so it is not
silently dead, without blocking CI on a contract that has nothing to check yet; both contracts
should move to `"error"` once their adapters exist.

## Consequences

Positive: a violating import fails CI immediately and names the exact contract it broke, instead of
surfacing as a runtime circular-import error or a design smell caught weeks later in review. New
contributors can read one table instead of inferring the architecture from `import` statements
scattered across the tree. The `cell`-as-sub-layer trick keeps the one legitimate upward-looking
read (`guard`, `honey_store` and `supervision` reading `cell`'s enums) without reopening the door
`cell -> guard`, so a future contributor cannot "fix" a stuck import by pointing `cell` at `guard`
and having CI silently allow it. Because Pollen depends only on `waggle`, it stays installable on a
Raspberry Pi with no Docker and no SQLite extensions, verified mechanically rather than by
discipline. `autopilot` never importing `llm`, enforced by `lint-imports` rather than by review
habit, is what keeps the Hive answering events when every model provider is down.

Negative: eight layers plus two package-level carve-outs (`waggle`, and `cell`'s and `forage`'s
sub-ranks) is a nontrivial amount of `import-linter` configuration to read and maintain, and a
genuinely new cross-layer need (for example, a Layer 3 subsystem needing something from Layer 2 in
a shape the table does not already allow) requires an ADR and a contract edit before the code can
land, which is friction by design but is still friction. The `"warn"` alerting on the two carve-out
contracts is a known, temporary gap: until the provider adapters and the Cell-session code exist,
a typo in one of those `ignore_imports` patterns would not fail CI, only print a warning that is
easy to miss in a busy log; this needs a follow-up to flip both contracts to `"error"` once phase 3
and the provider work land, or the safety net stays softer than the rest of the table.

## Alternatives considered

A single package for the whole backend: would have avoided the `waggle`/`hivemind`/`pollen` split
entirely, but Pollen would then either drag in every `hivemind` dependency (defeating its
constrained-hardware requirement) or the single package would need its own internal
"can't-import-that-from-here" convention with nothing to enforce it, which is exactly the gap
`import-linter` contracts between packages close for free.

A package per subsystem (one `uv` package for `queen`, one for `wardens`, one for `cell`, and so
on): gives the strongest possible import isolation, but at nineteen-plus packages the overhead of
per-package `pyproject.toml` files, versioning and a lockfile entry per subsystem outweighs the
isolation benefit that a single `layers` contract already provides inside one package.

Putting the shared primitives (ids, the clock, the loop shape) in `hivemind.common`: `common` is
part of the `hivemind` package, so `pollen`, which may import nothing from `hivemind`, could never
reach them; they have to live somewhere `pollen` and `hivemind` both may depend on, which is
`waggle`, not `common`.

`forage` importing `llm`: would let capacity code read the LLM layer's request and response shapes
directly, but it would mean naming a `ModelSlot` or reading a `Tempo` from Forage, Guard or
Autopilot would transitively pull in the LLM layer, breaking the `autopilot` never-imports-`llm`
guarantee the moment any of those callers needed a slot name; keeping `ModelSlot` and `Tempo` in
`forage` with the edge running the other way (`llm -> forage`) is what keeps that guarantee intact.
