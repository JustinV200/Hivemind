# HiveMind Coding Rules

> The standard every file in this repository is held to. If code and this document disagree, the
> code is wrong. If this document and reality disagree, fix the document in the same PR.

**Scope.** These rules apply to every package in the workspace (`waggle`, `hivemind`, `pollen`),
every script under `scripts/`, every Virtual Cell image under `images/`, and every test.

**Stated assumption.** Nothing in the repo has chosen a language yet. These rules assume
**Python 3.12+** because every subsystem the README describes (LLM orchestration, SQLite +
`sqlite-vec`, hypervisor bindings, Docker SDK, X11/uinput automation) has mature Python support.
If that choice changes, sections 2, 9, 11 and Appendix A are the only language-specific parts;
everything else is language-neutral. The Observation Hive front end is **TypeScript + React**;
section 2 lists its toolchain, and sections 5 and 7 apply to it unchanged.

---

## Table of contents

0. [How to use this document](#0-how-to-use-this-document)
1. [Guiding principles](#1-guiding-principles)
2. [Language and toolchain](#2-language-and-toolchain)
3. [Repository layout](#3-repository-layout)
4. [Package layering and dependency direction](#4-package-layering-and-dependency-direction)
5. [Files and modules](#5-files-and-modules)
6. [Naming](#6-naming)
7. [Comments and documentation](#7-comments-and-documentation)
8. [Modular design and interfaces](#8-modular-design-and-interfaces)
9. [Types and data models](#9-types-and-data-models)
10. [Errors and exceptions](#10-errors-and-exceptions)
11. [Async and concurrency](#11-async-and-concurrency)
12. [Logging and the Pheromone Trail](#12-logging-and-the-pheromone-trail)
13. [Configuration and secrets](#13-configuration-and-secrets)
14. [Testing](#14-testing)
15. [Security and safety](#15-security-and-safety)
16. [Git workflow](#16-git-workflow)
17. [Definition of done](#17-definition-of-done)
- [Appendix A: File templates](#appendix-a-file-templates)
- [Appendix B: Pre-commit checklist](#appendix-b-pre-commit-checklist)
- [Appendix C: State machines and where state lives](#appendix-c-state-machines-and-where-state-lives)

---

## 0. How to use this document

- **Before writing code:** read sections 1, 3, 4, 5 and 7. They decide *where* a file goes,
  *how big* it may be, and *how much* it must explain itself.
- **Before opening a PR:** run Appendix B top to bottom.
- **When a rule blocks you:** the rule probably exists to stop a design smell. Fix the design first.
  If the rule really is wrong, change the rule in the same PR and say why in the commit body.
- **Rule strength.** "MUST" and "NEVER" are enforced by tooling or review and are not negotiable.
  "SHOULD" is the default; deviating needs a comment at the site explaining why.

---

## 1. Guiding principles

Everything below is a consequence of these six rules.

1. **A file explains itself.** A reader who opens any single file cold, with no other tab open,
   can understand what it does, why it exists, and how it fits into the Hive. Comments are not
   optional decoration; they are part of the deliverable.
2. **Small pieces, sharp edges.** Files are short, do one thing, and talk to each other through
   explicit interfaces. If you need to scroll to understand a function, it is too long.
3. **Dependencies point one way.** Lower layers never know about higher layers. The Queen knows
   about Cells; a Cell never knows about the Queen.
4. **Boundaries are typed and validated.** Anything that crosses a process, network, file or LLM
   boundary is a validated model, never a raw dict.
5. **Nothing is precious except state and borrowed hardware.** Code should be easy to delete.
   State (Brood Chamber, Honey Store, Pheromone Trail) must be handled carefully, and a Real Cell
   (an existing device the Hive borrows for a task) must be left exactly as it was found.
6. **Terminal first, peripherals on demand.** Every Cell, Real or Virtual, offers a plain shell
   session. The Exoskeleton (virtual display, input, audio) is an optional attachment a task asks
   for; nothing in the core assumes a display exists.

---

## 2. Language and toolchain

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | `match`, `TaskGroup`, PEP 695 generics are all fair game. |
| Hosts | Windows 11, Ubuntu LTS, Arch Linux | The Hive Stand and the framework run on all three; macOS is best-effort. Nothing may assume one of them; platform shims live in `cell/local/` and `pollen/platform/`. |
| Virtual Cell images | Ubuntu LTS (24.04) | Every image under `images/` is Ubuntu-based, for containers and for QEMU cloud images alike. |
| Package/venv manager | `uv` | Workspace at the repo root; one lockfile. Never `pip install` by hand. |
| Lint + format | `ruff` | Format **and** lint. Rule set in root `pyproject.toml`. Line length 100. |
| Type checking | `mypy --strict` | Zero errors on `main`. No `type: ignore` without a reason code and comment. |
| Import boundaries | `import-linter` | Enforces the layer contracts in section 4. Runs in CI. |
| Tests | `pytest` + `pytest-asyncio` + `hypothesis` | Coverage via `pytest-cov`; see section 14 for targets. |
| Data models | `pydantic` v2 | For every boundary type. `dataclasses(frozen=True)` for internal values. |
| Async | `asyncio` | Structured concurrency only (section 11). No threads unless wrapping blocking I/O. |
| Structured logging | `structlog` | JSON in production, pretty in dev. See section 12. |
| CLI | `typer` | Thin layer; commands call into subsystem APIs, never contain logic. |
| API | FastAPI or Starlette (ADR, phase 10) | The Hive Entrance. Two listeners, loopback and remote; routes are thin; OpenAPI is generated from the pydantic route models and committed. |
| Front end | TypeScript (strict) + React, Vite | `packages/observation-web/`. `eslint` + `prettier`, `tsc --noEmit`, `vitest`. No `any`. Sections 5 and 7 apply to `.ts`/`.tsx` unchanged. TS types for every Landing Board model are generated from `docs/entrance/openapi.json`, never hand-written. |
| Android | Capacitor over the same React app | `packages/observation-web/android/`. Native push and the credential manager (passkeys) through plugins; the APK is a release artifact. Push delivery (FCM or UnifiedPush) is decided in the Android ADR. |
| Config format | TOML | Hive Manifests are TOML validated by pydantic (section 13). |
| Storage | SQLite (`aiosqlite`, FTS5, `sqlite-vec`) | One file per Hive. Migrations are numbered SQL files. |
| LLM | `LLMProvider`, `EmbeddingProvider` and `TranscriptionProvider` protocols in `hivemind/llm/` | Provider-agnostic. Adapters under `hivemind/llm/providers/<name>/` are the only modules that import a vendor SDK, model-server client or in-process model library (`anthropic`; `httpx` for OpenAI-compatible local servers such as Ollama, vLLM, llama.cpp; `faster_whisper` and `sentence_transformers` for in-process models). Enforced by `import-linter`. See 8.6. |
| Pre-commit | `pre-commit` | ruff, mypy, import-linter, trailing-whitespace, end-of-file. |

Pin every dependency in `uv.lock`. Add a dependency only with a one-line justification in the PR
description. Prefer the standard library when it is within 20% as good.

---

## 3. Repository layout

The tree below is the target. Empty directories get a `README.md` saying what will live there so
the structure is visible from day one.

```text
HiveMind/
├── .claude/                      # Agent-facing project docs: codingrules.md, roadmap.md
├── .github/workflows/            # CI: lint, type-check, import-linter, tests, image builds
├── docs/
│   ├── adr/                      # Architecture Decision Records: NNNN-short-title.md
│   ├── waggle/                   # Waggle protocol spec: message catalogue, transports, versioning
│   ├── entrance/                 # Landing Board: the committed OpenAPI document, client guide, push and enrolment contracts
│   ├── manifests/                # Annotated example Hive Manifests
│   ├── supervision/              # Data the supervisors load: default-policy.toml, capping-tiers.toml
│   ├── observation/              # Design notes the Observation Hive follows
│   ├── evals/                    # Reports written by `hive llm eval`
│   ├── runbooks/                 # Operational how-tos (requeening, supersedure, remote access, absconding, backups)
│   └── security.md               # The security review (phase 14)
├── packages/
│   ├── waggle/                   # SHARED PROTOCOL + shared primitives. Deps: pydantic, websockets, cryptography. No hivemind imports.
│   │   ├── src/waggle/
│   │   │   ├── messages/         # One package per message family (task/, cell/, honey/, capping/, ...), each split into modules by responsibility
│   │   │   ├── transport/        # Transport protocol + implementations (memory.py, websocket.py)
│   │   │   ├── envelope.py       # The outer wrapper every message travels in
│   │   │   ├── codec.py          # Serialise / deserialise + version negotiation
│   │   │   ├── signing.py        # Ed25519 over canonical envelope bytes
│   │   │   ├── outbox.py         # Durable queue for nodes that are offline
│   │   │   ├── ids.py            # Prefixed-ULID NewType ids. Here, not in hivemind, because envelopes and pollen need them
│   │   │   ├── clock.py          # Clock protocol, SystemClock, FakeClock. Here because pollen needs the fake too
│   │   │   ├── loop.py           # The standard long-running loop shape (section 11), shared by every bee and the gateway
│   │   │   └── errors.py
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── hivemind/                 # THE QUEEN and every control-plane + Hive subsystem
│   │   ├── src/hivemind/
│   │   │   ├── common/           # Layer 0. errors, result, logging setup, migrations. Imports nothing internal except waggle (ids, clock, loop).
│   │   │   ├── manifest/         # Hive Manifest loading, schema, validation
│   │   │   ├── pheromone/        # Pheromone Trail: append-only audit log, per-node segments that sync
│   │   │   ├── llm/              # LLMProvider + EmbeddingProvider protocols, slot resolution (the ModelSlot enum is in forage/), ladders, routing, fanner.py (seat meter), prompts/, providers/. Imports forage; forage never imports llm.
│   │   │   ├── forage/           # Capacity as data: HostCapacity, Seat, RoleFootprint, grants, requests, map.py (Forage map), pure allocation, slots.py (ModelSlot), tempo.py (Tempo). Imports nothing from llm.
│   │   │   ├── brood_chamber/    # Task graph, task state machine, persistence
│   │   │   ├── honey_store/      # Cold tier: nectar/ intake, ripening/ pipeline, honey/ retrieval, schema/
│   │   │   ├── memory/           # Hot + warm tiers: hot-state assembly, relevance, handoff model, compaction, pins, Bee Bread, cell_wax.py (Queen-written per-Cell cautions)
│   │   │   ├── supervision/      # Supervisor protocol, attendant.py (inbox triage for any supervisor), Alarm, ContextTelemetry, policy tables, capping/, mask.py (Pheromone Mask state)
│   │   │   ├── guard/            # Policy engine, capabilities.py (CapabilitySet, built in phase 3), access.py (what each AccessLevel allows), permission checks. The security enums live in cell/tiers.py.
│   │   │   ├── cell/             # The Cell abstraction: Cell, CellKind (REAL | VIRTUAL), CellSession (terminal), leases, needs.py (TaskNeeds), tiers.py (AccessLevel, CombShieldLevel, HoneyClearance), snapshot.py (Snapshotter protocol), local/ (the Hive Stand)
│   │   │   ├── hive/             # VIRTUAL Cells: lifecycle.py + night_veil.py (attestation) + backends/ (docker.py, qemu.py, cloud/...) + overwinter/ + snapshot.py (Snapshotter backends)
│   │   │   ├── swarm/            # REAL Cells from enrolled devices: registry, enrolment, heartbeat, PollenSession, Nuc promotion
│   │   │   ├── exoskeleton/      # compound_eye/, antennae/, buzz/, browser/, attach.py, recorder.py (flight recorder), tactics/ (mouse_like_human)
│   │   │   ├── royal_jelly/      # Royal Jelly Lab + Comb Registry: spec/, scaffold/, quarantine_comb/, registry/ (hive + cell scopes), promotion/
│   │   │   ├── workers/          # Worker runtime + roles/ (forager.py, scout.py, ...) + tools/ + tactics/ (write_like_human)
│   │   │   ├── wardens/          # Per-Cell supervisor: state.py, inbox/, autopilot/ (never awaits a model), awake/, spawn/, local_pool/ (allocation and hosting.py: model servers on the Warden's Cell), requests, offline/, watch/
│   │   │   ├── queen/            # inbox/, autopilot/, awake/, planner/, placement/, forage/ (shared pool, ceilings, hosting plans), dispatcher, cluster/, requeening/, supersedure/ (moving the Hive Stand)
│   │   │   ├── entrance/         # Hive Entrance: two listeners (loopback, remote), routes/ (versioned), enrol/ (device invites; approval on loopback only), auth/ (device key + password, sessions, step-up), push/ (webhooks, web push), expose.py (vpn, lan, tunnel), reducer.py (Entrance Reducer), landing_board.py (OpenAPI contract), human inbox
│   │   │   ├── observation/      # Observation Hive read side: views/ (pydantic read models), api.py, metrics. The front end is packages/observation-web/.
│   │   │   └── cli/              # `hive` CLI. One file per command group.
│   │   ├── tests/
│   │   │   ├── unit/             # Mirrors src/hivemind/ one-to-one
│   │   │   ├── integration/      # Real SQLite / real Docker; marked, skippable
│   │   │   ├── e2e/              # Whole-Hive scenarios
│   │   │   ├── contracts/        # Protocol contract suites, parametrised over every implementation
│   │   │   ├── builders/         # Test-data builders (make_task, make_cell_spec, ...)
│   │   │   ├── evals/            # Handoff and model evaluation harnesses (phases 4 and 8)
│   │   │   └── conftest.py
│   │   └── pyproject.toml
│   ├── pollen/                   # POLLEN PACKET device connector. Depends ONLY on waggle.
│   │   ├── src/pollen/
│   │   │   ├── agent/            # The gateway loop: connect out, heartbeat, hand a session to the Warden on the Hive Stand; persists the Queen's address and honours a signed QueenMoved
│   │   │   ├── enrol/            # First-run registration with the Queen: capability + Forage report
│   │   │   ├── executors/        # What the device can do: session.py (persistent shell), files.py, info.py
│   │   │   ├── lease/            # Device-side lease: scratch dir, started PIDs, restore on release, dead-man switch
│   │   │   ├── bootstrap/        # Install the full hivemind runtime when the Queen promotes the device to a Nuc
│   │   │   └── platform/         # OS-specific shims (linux.py, windows.py, macos.py)
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── observation-web/          # OBSERVATION HIVE front end: TypeScript (strict) + React, Vite. Served by the Entrance as static files.
│       ├── src/
│       │   ├── landing_board/    # Generated TS types from docs/entrance/openapi.json + the one client module (auth, streams, push)
│       │   ├── views/            # One directory per view (fleet, cell, forage, thoughts, attendant, capping, honey, chat, enrol)
│       │   ├── components/       # Shared components: badges, gauges, the live SVG diagrams, the summary strip
│       │   └── app.tsx           # Composition root: routes, stream subscriptions, PWA registration
│       ├── android/              # Capacitor project: the same app as an APK with native push and passkeys
│       ├── tests/
│       └── package.json
├── images/                       # Virtual Cell images (Ubuntu LTS): one directory per image, Dockerfile/VM spec + README
│   ├── base-ubuntu/              # Terminal-first: shell, Python, Warden entry point, Waggle client
│   ├── desktop-ubuntu/           # Exoskeleton-ready: base + Xvfb, xdotool, PulseAudio, browser
│   └── night-veil-ubuntu/        # Night Veil: desktop + OpenVPN client, Tor, Tor Browser, kill-switch rules; attested before READY
├── scripts/                      # Dev/ops helpers. One job each. Header comment says what and how.
├── pyproject.toml                # Workspace root: uv workspace members, ruff, mypy, pytest, import-linter
├── uv.lock
├── CLAUDE.md                     # Points Claude at .claude/codingrules.md and .claude/roadmap.md
├── README.md
└── LICENSE
```

**Rules for the layout**

- Every subsystem directory under `src/hivemind/` is a Python package with an `__init__.py` that
  re-exports **only** its public API (see 5.4). Anything not re-exported is private to the package.
- A subsystem may have sub-packages (`hive/backends/`, `honey_store/ripening/`). Depth stops at
  three levels below `src/hivemind/`. Deeper than that means the subsystem should be split.
- A Waggle message family is a package, `waggle/messages/<family>/`, never one file and never a
  run of prefixed siblings (`forage.py`, `forage_values.py`, ...). Its modules split the family by
  responsibility (`forage/grants.py`, `forage/values.py`, `forage/capacity.py`,
  `forage/hosting.py`), each with the 7.2 header; its `__init__.py` re-exports the family's
  messages, enums and value models (5.4), so a caller writes
  `from waggle.messages.forage import SourceRef` without knowing the split, while every bound
  stays in the module that names it. A new family starts as a package with one module, and a
  module that outgrows 5.1 splits into a sibling inside the package, never into a prefixed file
  beside it. `base.py`, `labels.py`, `reports.py`, `catalogue.py` and `registry.py` are the only
  modules directly under `messages/`.
- `tests/unit/` mirrors `src/` exactly: `src/hivemind/hive/backends/docker.py` is tested by
  `tests/unit/hive/backends/test_docker.py`. No exceptions; CI checks for orphan modules.
- `common/` is the only place for cross-cutting primitives. It never grows domain logic. If you are
  tempted to put a "helper" there, it belongs in the subsystem that uses it.
- No `utils.py`, `helpers.py`, `misc.py`, or `core.py` anywhere. Name the file for what it contains.
- Fakes live beside the Protocol they implement, as `fake.py` in the same package (`llm/fake.py`,
  `cell/fake.py`, `hive/backends/fake.py`; `FakeClock` in `waggle/clock.py`), never under
  `tests/`. They are shipped code: `pollen`, `hive doctor` and demo paths use them, and a fake in
  `src/` is held to the same standard as everything else.
- `packages/observation-web/` follows the same rules with TypeScript names: one concept per file,
  the size limits of 5.1, the comment density of 7.4, a header comment in the shape of 7.2. Its
  `landing_board/types.ts` is generated, never edited; CI regenerates it from the committed
  OpenAPI document and fails on a diff.

---

## 4. Package layering and dependency direction

Imports flow **downward only**. A module may import from its own layer or any lower layer. It may
NEVER import from a higher layer. This is enforced by `import-linter` contracts in the root
`pyproject.toml`; a violating import fails CI.

```text
Layer 7  entrance, observation, cli                                  (edges: HTTP, terminal, dashboard)
Layer 6  queen                                                        (the kernel; the only global view; divides Forage)
Layer 5  wardens                                                      (per-Cell supervisors; spawn and supervise Workers)
Layer 4  workers                                                      (roles that do the work)
Layer 3  hive, swarm, exoskeleton, royal_jelly                        (sources of Cells, and capabilities handed down)
Layer 2  cell, brood_chamber, honey_store, memory, supervision, guard (the Cell abstraction, state, memory, policy)
Layer 1  manifest, pheromone, llm, forage                             (foundational services; capacity as data)
Layer 0  common                                                       (primitives; imports nothing internal)
──────── waggle (separate package)              (usable by every layer; imports nothing from hivemind)
```

**Corollaries**

- `common` and `waggle` know nothing about bees. `waggle` provides envelopes, ids, the clock and
  the loop shape, because `pollen` needs those too and may import nothing from `hivemind`;
  `common` provides errors, results, logging and migrations.
- `cell` defines what a Cell is and how a terminal session on it works; it does not know how
  Cells are made. `hive` (Virtual Cells) and `swarm` (Real Cells on remote devices) produce Cells
  and import `cell`, never the reverse. The Hive Stand Real Cell lives in `cell/local/` because it
  needs nothing beyond the standard library.
- Process execution is a `CellSession` concern. `subprocess`, `os.system` and friends are
  importable only from `hivemind.cell.*` (the local and in-cell sessions),
  `hivemind.hive.backends.*`, `hivemind.royal_jelly.quarantine_comb.sandbox_subprocess`,
  `pollen.*` and `scripts/`. A Worker or tool that wants to run a command asks its session;
  `lint-imports` rejects anything else.
- Autopilot never awaits a model. Any module under a directory named `autopilot/` may not import
  `hivemind.llm`, directly or transitively; `lint-imports` enforces it. This is what keeps the
  Hive alive when every provider is down (section 8.8).
- `wardens` imports `workers` (to spawn them) and `queen` imports `wardens` (to assign to them).
  A Worker never imports its Warden and a Warden never imports the Queen; they talk over Waggle
  through the `Supervisor` protocol in `supervision/`.
- Two subsystems on the same layer may import each other only through their `__init__` public API,
  and only if the ADR for that subsystem lists the dependency. Prefer passing data through the
  layer above.
- `pollen` imports `waggle` and nothing else from the workspace. It must install on a Raspberry Pi
  with no Docker, no SQLite extensions, and no LLM SDK.
- Cross-layer communication that would violate direction goes through an **event** on the
  Pheromone Trail or a **message** over Waggle, never through a direct import.
- Vendor LLM SDKs and HTTP clients for model servers are imported only inside
  `hivemind.llm.providers.<name>`. An `import anthropic` or `import openai` anywhere else fails
  `lint-imports`. Everything above `llm/` sees only our own request, response and capability
  models (section 8.6).
- Within Layer 1, `llm` imports `forage` and `forage` never imports `llm`. `ModelSlot` and
  `Tempo` live in `forage` so that grants, routing inputs and autopilot rules can name a slot or
  read a tempo without touching `hivemind.llm`.
- `cell` is the home of everything a task or a policy needs to say about a Cell: `TaskNeeds`, the
  three security enums (`AccessLevel`, `CombShieldLevel`, `HoneyClearance`) and the `Snapshotter`
  protocol. `guard`, `hive`, `honey_store` and `supervision` import them from there; nothing at
  Layer 2 or below imports `guard` for an enum.

---

## 5. Files and modules

### 5.1 Size limits (enforced by a ruff plugin / CI script)

| Unit | Target | Hard limit | Reaction when exceeded |
|---|---|---|---|
| File (lines of code) | ≤ 200 lines | 300 lines | Split by responsibility, not by line count. |
| Function / method | ≤ 30 lines | 50 lines | Extract named helpers. Names replace comments. |
| Class | ≤ 150 lines | 200 lines | Compose smaller classes; extract a Protocol. |
| Parameters | ≤ 4 | 5 | Introduce a frozen dataclass for the argument group. |
| Cyclomatic complexity | ≤ 8 | 10 | `ruff` C901. Flatten with early returns or `match`. |
| Nesting depth | ≤ 3 | 4 | Early return / guard clause / extract. |

A file's size is its **lines of code**: a blank line, a comment-only line or a line inside a
docstring does not count, so the header of 7.2 and the per-block comments of 7.4 never push a
file over. `scripts/check_sizes.py` counts exactly that; a statement with a trailing comment
counts once. Test files may go to 400 lines of code because fixtures and parametrisation are
verbose; split by feature under test before that.

### 5.2 One concept per file

A file holds exactly one of: a Protocol and its docs; one concrete class; a group of pure
functions that share a noun; one pydantic model family; one CLI command group. If you cannot
write the module docstring's first sentence without "and", split the file.

### 5.3 Module structure (in this order)

1. Module docstring (mandatory, see 7.2).
2. `from __future__ import annotations` if needed.
3. Imports: stdlib, third-party, workspace (`waggle`), local, each group separated by a blank line
   and sorted by ruff.
4. Module-level constants (UPPER_SNAKE, each with a trailing comment saying what it is and why that value).
5. `__all__` listing the public names.
6. Types / Protocols.
7. Public functions and classes.
8. Private helpers (prefixed `_`), placed **after** the public code that uses them, so the file reads
   top-down from intent to detail.

### 5.4 `__init__.py` contract

- Contains a docstring in the 7.2 shape (summary, one-paragraph explanation, "Fits into the
  Hive", "Key invariants", "See Also") plus a `Public API:` section listing its public entry
  points with one line each; a package with nothing public yet says so and names the phase that
  first populates it.
- Contains only re-exports and `__all__`. Never logic, never side effects.
- Whatever is not in `__all__` is private. Other subsystems importing a non-exported name is a CI
  failure (`import-linter` "forbidden" contract on `hivemind.*._*` and non-`__init__` modules
  across subsystem boundaries).

### 5.5 No module-level side effects

Importing any module must be free of I/O, network, environment reads, logger configuration or
global mutation. Construction happens in a composition root (`cli/`, `entrance/`, or a test).

---

## 6. Naming

### 6.1 Use the Hive vocabulary in code

The README's terminology table is the domain language. Code uses it literally so the docs, the
logs, and the code agree.

| Concept | Module / package | Class | Notes |
|---|---|---|---|
| Queen | `queen` | `Queen` | Only one instance per Hive. |
| Cell (either kind) | `cell` | `Cell`, `CellKind`, `CellHandle`, `CellCapabilities`, `CellSession`, `RealCellLease`, `TaskNeeds`, `Snapshotter` | `CellKind.REAL` or `CellKind.VIRTUAL`. A Worker only ever sees `Cell` + `CellSession`. |
| Virtual Cell | `hive` | `VirtualCellSpec`, `CellBackend`, `CellLifecycle` | Owned: provisioned, then destroyed or Overwintered, except `NIGHT_VEIL` which is always teardown-only. |
| Real Cell | `cell/local/`, `swarm` | `HiveStand`, `SwarmNode`, `RealCellSource` | Borrowed: leased, then released and left as found. Never destroyed. |
| Hive Stand | `cell/local/` | `HiveStand`, `HiveStandSource` | The machine the Queen runs on. Also the first Real Cell and the default home of every Warden. |
| Placement | `queen/placement/` | `TaskNeeds`, `Placement` | Pure decision: reuse a Real Cell or provision a Virtual one. |
| Warden | `wardens` | `Warden`, `WardenAutopilot`, `WardenAwake`, `WardenOffline` | One per Cell. Supervises sub-bees; never provisions Cells. |
| Attendant | `supervision/attendant.py`, used by `queen/inbox/` and `wardens/inbox/` | `Attendant`, `InboxItem`, `Priority` | Every supervisor's inbox triage; deterministic first, a cheap slot for ties only where the grant allows. |
| Supervision | `supervision` | `Supervisor`, `Alarm`, `AlarmKind`, `ContextTelemetry`, `Intervention`, `EscalationPolicy` | Same protocol at every level: human → Queen → Wardens → sub-bees. |
| Memory tiers | `memory` | `HotState`, `Handoff`, `Pin`, `RelevanceScore`, `BeeBread` | Working, hot, warm (Bee Bread), cold (Honey). |
| Cell Wax | `memory/cell_wax.py` | `CellWax`, `WaxSeverity` (`NOTE`, `CAUTION`, `BLOCK`) | Queen-written cautions about one Cell; proposed by anyone, written and cleared only by the Queen; in hot state only while that Cell is a candidate. Marks the cell, is not the honey inside. |
| Forage | `forage` | `HostCapacity`, `Seat`, `RoleFootprint`, `ForageCapacity`, `ForageGrant`, `ForageRequest`, `RoyalReserve`, `LocalPool`, `Ceilings`, `HostingPlan` | Capacity in several dimensions. The Queen divides the shared pool by grant; a Warden divides its local pool under ceilings; grants are leases. |
| Forage map | `forage/map.py` | `ForageMap`, `ModelSource` | Every source that can serve a model: grade, distance, abundance, cost. |
| Fanner | `llm/fanner.py` | `Fanner` | The seat meter every model call passes through; enforces grants, measures speed. |
| Colonized | `swarm` | (a `SwarmNode`/`NodeStatus` state, phase 11.2) | A pollinated Real Cell whose Warden and sub-bees have moved onto it (Level 1+); it carries device-scoped tools and Honey that outlive any one lease. |
| Nuc | `swarm` | `NucPromotion` | A colonized Real Cell that has also gained its own model server; keeps working when disconnected. |
| Clustering | `queen/cluster/` | `ClusterProtocol` | Pause and preserve while a provider is unavailable; resume from Handoffs. |
| Observation Hive | `observation` | `FleetView`, `CellView`, `ForageView`, `AttendantView`, `ThoughtsView`, `CappingView`, `HoneyBrowser` | Read-only views; the chat is the only write, and it goes into the Queen's inbox. |
| Capping | `supervision/capping/` | `Proposal`, `Postcondition`, `RiskTier`, `Verdict`, `CappingGate` | The QA gate: nothing with a side effect outside scratch lands uncapped. |
| Access level | `cell/tiers.py` | `AccessLevel` (`READ_ONLY`, `SCRATCH`, `FULL`) | Per Real Cell; Virtual Cells are always `FULL`; caps every capability set for that Cell. What each level permits is data in `guard/access.py`. |
| Comb Shield level | `cell/tiers.py` | `CombShieldLevel` (`MEADOW`, `PROPOLIS`, `NIGHT_VEIL`) | Per Cell security tier. Runtime controls are enforced from the Cell's tier; tasks inherit from their placed Cell. Operator-set for Real Cells, Queen-chosen for Virtual ones. |
| Honey clearance | `cell/tiers.py` | `HoneyClearance` (`C0`, `C1`, `C2`) | Data sensitivity labels carried by every memory tier, not only Honey. Any user personal detail (including first name or habits) is `C2`. |
| Watch mode, Patrol | `wardens/watch/` | `WatchObserver`, `Patrol` | A Real Cell's Warden with no active bees observes read-only and reviews on a schedule. |
| Tempo | `forage/tempo.py` | `Tempo` (latency budget, accuracy bar) | Read by the Attendant, routing, Forage allocation and Capping; never overrides safety. Lives at Layer 1 because Layer 1 reads it. |
| Worker roles | `workers/roles/` | `Forager`, `Scout`, `GuardBee`, `Undertaker`, `Drone`, `HouseBee` | All implement `Worker`. |
| Exoskeleton | `exoskeleton` | `CompoundEye`, `Antennae`, `Buzz` | Each is a Protocol with backends. |
| Royal Jelly Lab + Comb Registry | `royal_jelly` | `ToolSpec`, `QuarantineComb`, `CombRegistry` | |
| Task store | `brood_chamber` | `BroodChamber`, `Task`, `TaskGraph` | |
| Knowledge | `honey_store` | `Nectar`, `Honey`, `Ripener`, `HoneyStore` | |
| Protocol | `waggle` | `Envelope`, `Waggle*Message` | |
| Gateway | `entrance` | `HiveEntrance` | Two listeners: loopback (always) and remote (when exposed). |
| Landing Board | `entrance` | `LandingBoard`, `EnrolledDevice`, `DeviceInvite`, `StepUp`, `PushChannel` | The versioned public contract; only devices enrolled and approved on the loopback listener may use it. |
| Entrance Reducer | `entrance/reducer.py` | `EntranceReducer`, `EntranceMode` | Drops the Entrance to loopback only and kills remote sessions; only loopback reopens it. |
| Pheromone Mask | `supervision/mask.py`, `workers/tactics/`, `exoskeleton/tactics/` | `MaskState` (`OFF`, `WARDEN`, `QUEEN_FORCED`), `MaskTactic` | State with reason and expiry in supervision; the prose tactic acts in workers, the input tactic in the exoskeleton. |
| Supersedure | `queen/supersedure/` | `Supersedure`, `SupersedurePlan`, `QueenMoved` | Moving the Hive Stand: freeze, copy, hand over, never two Queens. |
| Sting Cut | `queen/sting_cut.py` | `StingCut` | Human-initiated per-Cell disconnect; revokes the lease and keys, kills lease processes; the Undertaker performs the cleanup. |
| Devices | `swarm`, `pollen` | `SwarmNode`, `PollenPacket`, `PollenSession` | A Swarm node is a Real Cell; `PollenSession` is its `CellSession` carried over Waggle. |
| Audit | `pheromone` | `PheromoneTrail`, `PheromoneEvent` | |
| Config | `manifest` | `HiveManifest` | |
| LLM access | `llm` | `LLMProvider`, `EmbeddingProvider`, `ProviderCapabilities`, `BoundModel` | Vendor-neutral; adapters in `llm/providers/`. The `ModelSlot` enum is in `forage/slots.py`. |
| Transcription | `llm/transcription.py` | `TranscriptionProvider`, `Transcript`, `TranscriptSegment` | One slot, `TRANSCRIBER`, for the human's voice at the Entrance and a Worker's ears through Buzz. Whisper by default, local first. |

Each bee term appears **with its plain-English meaning in parentheses the first time it is used in
every module docstring**, e.g. "Provisions a Cell (an isolated VM or container)". New readers
should never need the README open to understand a file.

### 6.2 Conventions

- Modules and packages: `snake_case`, singular nouns for a thing (`envelope.py`), plural for a
  collection of that thing (`messages/`).
- Classes: `PascalCase`. Protocols are named for the capability, not suffixed `Interface` or
  `Protocol`: `CellBackend`, not `CellBackendProtocol`. Implementations are named for what makes
  them different: `DockerCellBackend`, `QemuCellBackend`.
- Functions: `snake_case`, verb first: `provision_cell`, `ripen_nectar`, `assign_task`.
- Booleans read as predicates: `is_ready`, `has_exoskeleton`, `can_promote`.
- Async functions are **not** prefixed `async_`; the signature already says so.
- Constants: `UPPER_SNAKE`. Enum members: `UPPER_SNAKE`.
- Type variables: descriptive, `MessageT` not `T`, unless the scope is three lines.
- IDs: `NewType` wrappers named `<Thing>Id` (`CellId`, `TaskId`, `WorkerId`) so a `TaskId` can
  never be passed where a `CellId` is expected.
- Never abbreviate beyond the accepted set: `id`, `db`, `cfg` (only inside `manifest/`), `ctx`,
  `msg`. Everything else is spelled out.
- Test names describe behaviour: `test_undertaker_tears_down_cell_after_task_completes`, never
  `test_undertaker_1`.

---

## 7. Comments and documentation

This is the section most likely to be skimped on and the one that matters most. The standard is
**heavily commented**: a reader should be able to follow every file from top to bottom without
opening another file, and should understand *why* every non-trivial line is there.

### 7.1 The three questions every comment answers

Every block of code should have an answer nearby to at least one of:

1. **Why does this exist?** (intent, the requirement that drove it)
2. **Why this way and not the obvious way?** (the trade-off, the bug it avoids, the constraint)
3. **What must stay true?** (invariants, assumptions about callers, ordering requirements)

Comments that merely restate *what* the code does (`# increment counter`) are noise and are removed
in review. Comments that explain *why* are mandatory.

### 7.2 Module docstring (mandatory, fixed shape)

Every `.py` file starts with a docstring in this shape. Template in Appendix A.

```python
"""One-line summary in the imperative: what this module provides.

Longer explanation (2-6 sentences): the responsibility of this module, the problem it solves,
and the key design decision behind it. Define any Hive term (with its plain meaning) on first use.

Fits into the Hive:
    Layer N (<layer name>). Called by <who>, calls into <what>. One sentence on the data that
    flows through here and where it goes next.

Key invariants:
    - Bullet list of things that must always be true, or "None." if there really are none.

See Also:
    - docs/adr/NNNN-xxx.md for the decision that shaped this module (if any)
    - hivemind.<sibling> for the counterpart module
"""
```

### 7.3 Docstrings on every public name (Google style)

- Every public class, function, method and Protocol member has a docstring with **Args**,
  **Returns**, **Raises**, and for anything non-trivial an **Example**.
- The first line is a one-sentence imperative summary that fits on one line.
- `Args` explain meaning and constraints, not just type (the type is in the annotation):
  `timeout: Seconds to wait for the Cell to report ready. Must be > 0; typical value 60.`
- `Raises` lists every exception the caller can reasonably handle, with the condition.
- Private helpers (`_name`) get at least a one-line docstring saying what they are for.
- Pydantic models document **every field** with `Field(description=...)`, and the class docstring
  explains what the model represents and where it crosses a boundary.

### 7.4 Inline comment density and placement

- **Every logical block** (a group of 3-8 lines that does one step) gets a leading comment
  explaining the step. Blank line before the comment, none after.
- **Every branch** whose condition is not self-evident from its names gets a comment on the
  branch line or the line above it stating the case being handled.
- **Every loop** states what it iterates over and what it accumulates or decides.
- **Every early return / raise** states the reason in a comment or in the exception message.
- **Every `await`** on something external (network, subprocess, LLM, DB) has a comment stating the
  expected latency class and what happens on timeout.
- **Every magic-looking value** (retry counts, buffer sizes, timeouts) is a named constant with
  a trailing comment explaining the choice. Model names and provider URLs are never constants;
  they come from the manifest (section 8.6).
- Comments live **above** the code they describe, on their own line. Trailing comments only for
  constants and dataclass fields.
- Long files use section dividers to make the structure scannable:
  ```python
  # ──────────────────────────────────────────────────────────────────────────────
  # Provisioning
  # ──────────────────────────────────────────────────────────────────────────────
  ```

### 7.5 Marker tags

Use exactly these, uppercase, followed by `(owner)` and a colon. CI greps for them; `HACK` and
`FIXME` fail CI on `main` unless followed by an issue link.

| Tag | Meaning |
|---|---|
| `TODO(name): ...` | Planned work, not blocking. |
| `FIXME(name): ... <issue-url>` | Known bug or gap. Must reference an issue. |
| `HACK(name): ... <issue-url>` | Deliberate ugliness with a reason and a removal plan. |
| `NOTE:` | A non-obvious fact the reader must know. |
| `SAFETY:` | Explains why an unsafe-looking operation (subprocess, eval, network) is acceptable here. |
| `PERF:` | Explains a non-obvious performance decision. |
| `WHY:` | Marks the sentence that explains a design choice, when it deserves emphasis. |

### 7.6 Forbidden comment practices

- **NEVER** commit commented-out code. Git remembers it.
- **NEVER** leave a comment that describes behaviour the code no longer has. Comments change in the
  same commit as the code they describe.
- **NEVER** write a comment to excuse a violation of these rules ("this file is long but…"). Fix it.
- **NEVER** put a comment where a better name would do. Rename first, then comment what remains.
- No author tags, dates, or change logs in files. Git holds that.

### 7.7 Docs outside code

- **ADRs** (`docs/adr/NNNN-title.md`) record every decision that is hard to reverse: transport
  choice, storage engine, sandbox technology, auth model. Format: Context, Decision, Consequences,
  Alternatives considered. Numbered, never edited after acceptance (write a superseding ADR).
- **Protocol spec** (`docs/waggle/`) is the source of truth for message shapes. The pydantic models
  in `packages/waggle/` are generated from or checked against it in CI.
- **API spec** (`docs/entrance/openapi.json`) is the Landing Board's contract, generated from the
  route models by `entrance/landing_board.py` and committed; CI fails if the generated document
  differs from the committed one, so a route change is always a visible contract change. The
  front end's TypeScript types are generated from it in turn.
- Each subsystem has a `README.md` in its package directory: purpose, public API summary, a diagram
  if the flow is not linear, and a "How to test this" section.
- The root `README.md` links to `.claude/roadmap.md` and `.claude/codingrules.md`.

---

## 8. Modular design and interfaces

### 8.1 Protocols at every seam

Anything with more than one plausible implementation, or that touches the outside world, is
defined as a `typing.Protocol` (structural) in its own file, with the implementations in sibling
files or a `backends/` sub-package.

Mandatory protocols (each gets its own ADR when first implemented):

| Protocol | Location | Known implementations |
|---|---|---|
| `Transport` | `waggle/transport/base.py` | `MemoryTransport`, `WebSocketTransport` |
| `LLMProvider` | `hivemind/llm/provider.py` | `AnthropicProvider`, `OpenAICompatProvider` (Ollama, vLLM, llama.cpp server, LM Studio, any OpenAI-compatible API), `FakeLLMProvider` |
| `CellSession` | `hivemind/cell/session.py` | `LocalProcessSession` (Hive Stand), `InCellSession` (inside a Virtual Cell), `PollenSession` (Swarm device over Waggle), `FakeSession` |
| `RealCellSource` | `hivemind/cell/source.py` | `HiveStandSource`, `SwarmSource` |
| `Supervisor` | `hivemind/supervision/supervisor.py` | `Queen` (over Wardens), `Warden` (over sub-bees), `FakeSupervisor` |
| `CellBackend` (Virtual Cells only) | `hivemind/hive/backends/base.py` | `DockerCellBackend`, `QemuCellBackend`, cloud later |
| `CompoundEye` / `Antennae` / `Buzz` | `hivemind/exoskeleton/*/base.py` | X11/Xvfb backends, Playwright fast-path |
| `Sandbox` | `hivemind/royal_jelly/quarantine_comb/sandbox.py` | Container sandbox, subprocess sandbox (dev only) |
| `EmbeddingProvider` | `hivemind/llm/embedding.py` | `OpenAICompatEmbedding` (Ollama, vLLM, hosted), `SentenceTransformersEmbedding` (in-process), `FakeEmbedding` |
| `TranscriptionProvider` | `hivemind/llm/transcription.py` | `WhisperLocalTranscription` (faster-whisper in-process, optional extra, GPU when present), `OpenAICompatTranscription` (any server or hosted API speaking `/v1/audio/transcriptions`), `FakeTranscription` |
| `TaskStore` | `hivemind/brood_chamber/store.py` | SQLite, in-memory (tests) |
| `Worker` | `hivemind/workers/base.py` | one per role |
| `DeviceExecutor` | `pollen/executors/base.py` | shell, files, per-OS |
| `PheromoneTrail` | `hivemind/pheromone/trail.py` | SQLite, in-memory (tests) |
| `Snapshotter` | `hivemind/cell/snapshot.py` | `DockerSnapshotter`, `QemuSnapshotter` (in `hive/snapshot.py`), `NoopSnapshotter` (Real Cells, warns) |
| `PushChannel` | `hivemind/entrance/push/base.py` | `WebSocketPush`, `WebhookPush`, `WebPush`, `FakePush` |

### 8.2 Dependency injection, no globals

- Objects receive their collaborators through `__init__`. No module-level singletons, no service
  locator, no `get_current_queen()`.
- The **composition root** (where concrete classes are wired together) is exactly one place per
  entry point: `cli/app.py` for the CLI, `entrance/app.py` for the gateway, `conftest.py` for tests.
- A class that needs more than four collaborators is doing too much. Split it.

### 8.3 Pure core, effectful edges

- Decision logic (planning, scheduling, ripening rules, policy evaluation) is written as **pure
  functions** over plain data: input models in, output models out, no I/O.
- I/O (DB, network, subprocess, LLM) lives in thin adapter classes that call the pure core.
- This makes the core testable without fakes and makes the adapters trivially small.

### 8.4 Plugins by registry, not by `if` chain

Backends, worker roles, tools and executors are discovered through an explicit registry
(`register("docker", DockerCellBackend)`), populated in the composition root, keyed by the name
used in the Hive Manifest. Adding a backend never edits an `if backend == "docker"` chain.

Tool selection follows an explicit retrieval and authority chain:

- The Queen uses Honey for discovery and ranking only.
- The Comb Registry is the source of truth for executable tool definitions, versions and scope.
- The Queen resolves candidates from the Comb Registry, then issues a scoped capability grant to a Warden.
- A Warden attenuates that grant per Worker and never hands a sub-bee the full catalog.
- Workers execute only granted tools and never enumerate the full Comb Registry.

### 8.5 Data flows as immutable values

- Internal values are `@dataclass(frozen=True, slots=True)`.
- Boundary values are pydantic `BaseModel` with `model_config = ConfigDict(frozen=True, extra="forbid")`.
- State changes produce new objects (`dataclasses.replace`, `model_copy(update=...)`); nothing is
  mutated in place except inside a class that owns that state and says so in its docstring.

### 8.6 LLM provider independence

The Hive starts on Claude and must be able to move, slot by slot, to locally hosted models without
touching any code above `hivemind/llm/`. These rules make that a configuration change, not a
refactor.

- **One door.** `LLMProvider` (`llm/provider.py`) and `EmbeddingProvider` (`llm/embedding.py`)
  are the only way any code talks to a model. Vendor SDKs and model-server HTTP clients are
  imported only inside `llm/providers/<name>/` (enforced by `import-linter`, section 4). `httpx`
  itself is not banned elsewhere (the Entrance and the http tool use it); what is banned outside
  the adapters is a model-server client, meaning any request to a provider base URL, and the
  model-id grep below catches URLs.
- **Our types at the boundary.** Requests and responses are HiveMind models in `llm/models.py`
  (`LLMRequest`, `Message`, `ContentPart`, `ToolDefinition`, `ToolCall`, `LLMResponse`, `Usage`).
  SDK types never leave the adapter. Each adapter has a `mapping.py` that converts in both
  directions and is the only file where vendor field names appear.
- **Capabilities are declared, not assumed.** Every provider exposes a frozen
  `ProviderCapabilities` (native tool calls, schema-enforced output, vision, streaming, reasoning
  control, context window, system-role support). Core code branches on capabilities, never on
  provider name. `if provider.name == "anthropic"` is a review rejection.
- **Degrade by ladder, in one place.** `llm/structured.py` and `llm/tools.py` implement the
  fallbacks once. Structured output: native schema enforcement → JSON mode plus pydantic
  validation and retry → prompted JSON with fenced extraction, validation and retry. Tool calls:
  native tool-call protocol → a prompted tool protocol that we parse and validate. Subsystems call
  the ladder, never the provider directly, so a weaker local model gets the same interface with
  more retries and the retry counts are commented constants.
- **Model slots, not model names.** Code asks for a `ModelSlot` (`QUEEN`, `ATTENDANT`, `WARDEN`,
  `WORKER`, `RIPENER`, `SCAFFOLDER`, `EMBEDDER`, `JUDGE`, `TRANSCRIBER`), an enum that lives in
  `forage/slots.py` so autopilot and Forage can name a slot without importing `hivemind.llm`.
  `TRANSCRIBER` is audio in, text out; like `EMBEDDER` it is a non-chat slot with its own protocol
  (`llm/transcription.py`), metered by the Fanner and listed on the Forage map like any source.
  The manifest's `[llm.slots]` table maps each slot to a provider and model id with an optional
  fallback; a fallback may name a slot or a named binding that appears only in that table
  (`local_worker` in section 13). A model id or provider URL in code is a lint failure (a CI grep
  for `claude-`, `gpt-`, `llama` outside `docs/` and `manifest/`, skipping comments and
  docstrings so an adapter may say which servers it speaks to).
- **Prompts are portable.** Prompt assets under `llm/prompts/` are plain markdown with no
  vendor-specific tags, tokens or formatting tricks. If a provider needs a tweak, it lives in that
  adapter's `mapping.py` or a per-provider overlay file under `llm/prompts/overlays/<provider>/`,
  never in the shared prompt.
- **Same contract for every provider.** Every adapter passes
  `tests/contracts/test_llm_provider_contract.py` against recorded HTTP cassettes, including the
  degradation ladders at each capability level. A new provider is registered only after the suite
  passes. Live calls are `@pytest.mark.live_llm` and never run in the default CI job.
- **Offline is a first-class mode.** With `[llm] offline = true`, the provider registry refuses
  any provider whose base URL is not loopback, so a Hive can be proven to run with no external
  network.
- **Usage is normalised.** Every `LLMResponse` carries a `Usage` (input, output, cached tokens,
  and cost when the manifest lists a price for that model). The Pheromone Trail and the cost view
  read only this normalised value, so provider changes never break accounting.
- **Two channels, never confused.** Waggle carries messages between bees: Queen, Wardens,
  Workers, Pollen Packets. Model calls travel on a separate channel, HTTP to whichever provider
  serves the slot, whether that is a hosted API, a server on the Hive Stand, or a server on the
  Cell. Where a model runs never changes how bees talk to each other; it only changes the base URL
  the adapter uses. Bees in the same process use Waggle over the memory transport; bees in
  different processes or on different machines use it over WebSocket.

### 8.7 Cells: Real or Virtual, terminal first

A Cell is the unit of compute a Worker runs in or on. It is either **Virtual** (a VM or container
the Hive provisions and later destroys) or **Real** (an existing device: the host HiveMind runs on,
or a device enrolled in the Swarm, borrowed for a task and returned unchanged). The README makes
this distinction central; the code makes it invisible to Workers.

- **One abstraction, two sources.** `cell/` defines `Cell`, `CellKind`, `CellCapabilities`
  (os, arch, has_display, has_audio, has_browser, network scopes) and the `CellSession`
  protocol. `hive/` is the only producer of Virtual Cells; `cell/local/` and `swarm/` are the
  producers of Real Cells, both behind `RealCellSource`.
- **A session is a terminal.** `CellSession` offers `exec` with streaming output, `put_file`,
  `get_file`, a `scratch_dir`, and `close`. Every Cell has one. It is the only way a Worker or a
  tool runs a command or touches a file on its Cell (section 4).
- **The Exoskeleton is an attachment.** `exoskeleton/attach.py` equips a Cell with display,
  input and audio on request, starting what is missing through the session and stopping exactly
  what it started on detach. A Cell is terminal-only unless the task's `TaskNeeds` asks for more.
- **Brain and hands, and the runtime ladder.** A Worker's runtime process (the LLM loop) runs
  inside a Virtual Cell, so isolation covers the model's actions. For a Real Cell, what runs on
  the device climbs a fixed ladder: **Level 0**, gateway only, Warden and sub-bee brains on the
  Hive Stand reaching the device through `CellSession`; **Level 1**, the Warden and its sub-bees
  on the device, models still elsewhere — the Cell is **colonized** from this point on, and stays
  that way (tools scoped to it, Honey it has accumulated) across leases, even back in `WATCH`;
  **Level 2**, a model server on the device as well, which makes a colonized Cell a Nuc (8.10).
  Two invariants: a Warden and its sub-bees are always co-located (sub-bees run
  where their Warden runs, never the other way round), and **the Warden is the first bee to move
  onto a device**, because it is the supervisor and the reconnecting agent and needs no model to
  keep the lights on. Where the runtime runs is a Warden spawn decision in `wardens/spawn/`, never
  visible to role code.
- **Owned versus leased.** Virtual Cells are provisioned and destroyed by `hive`. Real Cells are
  leased: `RealCellLease` records the scratch root, every process it started and every path it
  was allowed to touch outside scratch, and `release()` restores the device. The Undertaker performs
  both `destroy` and `release`.
- **Placement is a pure decision.** `queen/placement/decide.py` maps `TaskNeeds` (isolation
  `required | preferred | none`, exoskeleton, os, network scopes, disposability) plus the current
  Cell inventory, each candidate's Cell Wax (a `BLOCK` excludes the Cell, a `CAUTION` counts
  against it) and the `[placement]` manifest section to a `Placement`: reuse this Real Cell, or
  provision a Virtual Cell from this spec. The reason is recorded on the trail.
- **Comb Shield is a Cell property.** Tier is bound to the Cell, not the task. A task inherits the
  `CombShieldLevel` of the Cell where it executes; moving a task to another Cell re-evaluates and
  re-binds controls before resume.
- **Tier defaults and authority.** New Virtual Cells default to `MEADOW`, and the Queen may
  provision one at `PROPOLIS` by policy. A Real Cell's tier is set by the operator at enrolment,
  like its access level, and describes a VPN the device already runs: HiveMind verifies the
  tunnel before placing Propolis work on it and never installs, starts or reroutes one on a
  borrowed machine, because that would touch the whole device and not the lease. `NIGHT_VEIL`
  requires explicit human request, is Virtual-only, and may not be autonomously escalated by the
  Queen or a Warden.
- **Night Veil is deterministic and strict.** A `NIGHT_VEIL` Cell is Virtual-only and may be marked
  `READY` only after attestation of: VPN tunnel up, Tor up, Tor Browser presence, default route via
  the tunnel, the Waggle client reaching the Hive Stand's hidden-service address through Tor, DNS
  leak checks green, and direct egress blocked by kill-switch rules. Night Veil
  model bindings are local-only and may not spill to hosted or Hive-Stand sources.
- **Night Veil's control channel has no clearnet destination.** The Warden's Waggle link to the
  Hive Stand goes over Tor to a `.onion` hidden service rather than the VPN tunnel or any clearnet
  address. Sharing the VPN tunnel with task egress would let anyone watching that tunnel's exit see
  one IP both feeding a Tor circuit and talking to a known Hive Stand address, linking the
  anonymized work back to the operator; a hidden service removes the fixed destination those
  observers would correlate against. This does not defend against an adversary who can see the
  Cell's own host (the hypervisor or cloud provider), who can still observe that a VPN tunnel and a
  Tor process are both active on the same machine — no tunnel topology fixes that, and it is the
  same limit Tor's own threat model excludes (a global passive adversary). The trade is latency and
  connection reliability for Waggle traffic, which Clustering (8.13) and the disconnected-Warden
  backoff and outbox replay (8.8) already absorb.
- **Night Veil is location-blind by policy.** A `NIGHT_VEIL` Cell exposes no geolocation path:
  no GPS or host location-service access, no Wi-Fi scan capability, metadata endpoints blocked,
  UTC timezone, fixed locale profile, and randomized hostname per boot.
- **Location-blind checks are part of readiness.** `NIGHT_VEIL` attestation fails closed unless
  geolocation APIs are denied, metadata endpoints are unreachable, timezone equals UTC, locale
  matches policy, and WebRTC local-IP leak probes are blocked.
- **Night Veil lifecycle is teardown-only.** A `NIGHT_VEIL` Cell is created just in time, never
  Overwintered, and must be destroyed immediately when its task completes. It boots from
  `images/night-veil-ubuntu`, which carries the OpenVPN client, Tor, Tor Browser and the
  kill-switch rules, so attestation checks an image rather than configuring a Cell at runtime.
- **Night Veil keeps a skeleton, not a story.** Section 12 defines exactly which events survive a
  Night Veil teardown; nothing else does.
- **Branch on capabilities, never on kind.** Worker, role and tool code may read
  `cell.capabilities` but never `cell.kind`. `CellKind` matters to exactly two callers: placement
  (choose) and the Undertaker (destroy versus release). An `if cell.kind == CellKind.REAL` anywhere
  else is a review rejection.
- **Every Real Cell has an access level.** `READ_ONLY`, `SCRATCH` or `FULL`, set at enrolment
  (the Pollen Packet asks for `FULL`; the operator may grant less) and stored with the node and
  every lease. Virtual Cells are always `FULL`. The level caps every capability set issued for
  that Cell, so a `READ_ONLY` device can never receive a write capability however the policy is
  configured.
- **Idle Real Cells are watched, not touched.** A Real Cell's Warden with no active bees sits in
  `WATCH`: a read-only autopilot state that observes what its access level allows (process list,
  resource use, logs and file changes in allowed roots, network counters), writes observations to
  Bee Bread with a retention window, and on the Patrol interval runs one awake episode to raise an
  Alarm, deposit a Nectar summary, propose Cell Wax for the device, or record "nothing notable".
  Watch mode never writes to the
  device and never captures screen or input; those need a separate, explicit capability and are
  never part of watching.

### 8.8 Supervision: the kernel, Wardens, autopilot and awake

The Queen behaves like an operating system kernel: a thin always-on loop with a prioritised inbox.
Every Cell has a Warden, a per-Cell supervisor built on the same loop shape. The tree is
human → Queen → Wardens → sub-bees, and one `Supervisor` protocol is used at every level.

- **Autopilot first, awake second.** Every event is handled by autopilot, a deterministic dispatch
  table over `(event kind, state)`, before any model is consulted. Autopilot returns an action or
  `NEEDS_JUDGEMENT`; only the latter runs an awake episode. Autopilot modules never import
  `hivemind.llm` (section 4). This is what keeps the Hive alive when every model is unreachable.
- **Awake episodes are stateless.** An episode assembles its prompt through `memory.assemble`
  from durable state plus the triggering event, decides one action, writes the decision back, and
  discards the transcript. No module keeps a conversation across episodes. Continuity lives in hot
  state or a pending question, never in a transcript.
- **Every supervisor has an Attendant.** `supervision/attendant.py` scores every inbox item
  (Waggle messages, Alarms, questions, human messages, timers, watch observations) with one
  deterministic function parametrised by the principal. The Queen's Attendant (`queen/inbox/`)
  weighs human messages heavily but not absolutely and may use `ModelSlot.ATTENDANT` for ties
  and unknown kinds. A Warden's Attendant (`wardens/inbox/`) runs the same scoring over a smaller,
  more uniform inbox and consults a model only if its grant allows it; by default it is
  autopilot-only. The Queen alone decides when a question goes to the human.
- **Escalation is a typed Alarm, and the human is last.** An issue a bee cannot resolve becomes
  an `Alarm` (id, kind, severity, origin, attempts, context reference) sent to its supervisor.
  Each level's `EscalationPolicy` is data (TOML) mapping `(kind, attempts)` to retry, respawn,
  rebind, takeover or escalate. A Warden never addresses the human; the chain is sub-bee →
  Warden → Queen → human. Every hop is a trail event carrying the same alarm id so no level
  handles it twice.
- **The Queen delegates and rebinds.** She never holds a session or a Comb Registry, and a test
  asserts it. Her levers are `Supervisor.intervene`: compact, checkpoint, handoff, rebind to
  another slot, takeover (spawn a bee on that Cell with the Queen's slot and the stuck bee's
  Handoff), cancel. Wardens have the same levers over their sub-bees, minus takeover with the
  Queen's slot and minus anything outside their `ForageGrant`; a rebind that needs Forage the
  Warden does not hold becomes a `ForageRequest`.
- **Pheromone Mask overrides are supervisory controls.** A Warden may invoke mask tactics for a
  bounded task segment when policy allows. The Queen may force a mask override at Cell scope with
  explicit reason and expiry; while active, the Warden enforces it for the Cell's sub-bees until
  expiry or explicit clear. `supervision/mask.py` holds the per-Cell state (`OFF`, `WARDEN`,
  `QUEEN_FORCED`) with its reason and expiry (Appendix C); the tactics live where they act,
  `workers/tactics/write_like_human.py` for prose and `exoskeleton/tactics/mouse_like_human.py`
  for input cadence, because prose is shaped on terminal-only Cells too.
- **Wardens never provision.** A Warden spawns sub-bees within its grant and requests Cells, more
  Forage, or tools from the Queen with a reason. Sub-bees inherit a subset of the Warden's
  capabilities and grant, never more.
- **Every bee reports `ContextTelemetry`** on heartbeat: tokens used against the window, current
  goal, last actions, blockers, spend. Supervisors watch telemetry, may `inspect` a compacted view
  on demand, and intervene. Full transcripts never travel up the tree.
- **A disconnected Warden keeps working** within what it owns: autopilot continues, its local
  pool (8.10) stays fully usable, awake continues if a model is reachable from where the Warden
  runs, results and Alarms queue, the trail is written to a local segment, and reconnection is
  retried with backoff. Shared grants are frozen; new shared Forage, new Cells and human-bound
  questions wait for reconnection. A Level 1 Warden with no local models clusters its sub-bees at
  once and keeps them warm; a Nuc carries on; either clusters fully past the manifest's maximum
  offline duration (8.13). A device with no local Warden, only a Pollen Packet gateway, runs a
  dead-man switch instead: when its link is lost for longer than the manifest allows, it kills what
  the lease started and releases.
- **Where a model is reachable, reconnection is investigated, not just retried** (roadmap 11.9a).
  A Nuc's Warden, and the Hive Stand's own Warden for any device it can no longer reach, each spend
  a bounded `reconnect_budget` spawning a Drone from their own local pool — never a request to the
  Queen — to run ordinary Capping-gated scratch diagnostics (`run_command`, one-off scripts) and
  attempt a fix. This never touches the Royal Jelly Lab or the Quarantine Comb; nothing here is
  promoted, only tried once and reported. The budget is carved out of, not added to, the offline
  duration in 8.13, so Clustering keeps the same deadline as a backstop regardless of outcome.

### 8.9 Memory: tiers, budgets, handoffs

Context is treated like a cache hierarchy. What a model sees is assembled per episode from the
tiers below; nothing accumulates.

| Tier | Holds | Lives in | Reached by |
|---|---|---|---|
| Working context | One episode's prompt | Assembled by `memory.assemble` | Built to a budget |
| Hot state | Active goals, open tasks and Alarms, pending questions, recent decisions with reasons, fleet and Forage summary, pins, short notes, Cell Wax for Cells in play | Derived from Brood Chamber and trail; notes, pins and Cell Wax in `memory` tables | Always loaded, bounded |
| Bee Bread (warm) | Recent episodes, task results, recent Nectar, Handoffs | Brood Chamber history, Pheromone Trail | Lookup by id, time, task; no search |
| Honey (cold) | Everything ripened | Honey Store | Full-text and semantic search |

- **Bounded by construction.** `memory.assemble` scores candidates with `memory/relevance.py`
  (recency decay, linkage to active tasks, Alarm severity, pins that never decay) and packs by
  score until the budget fills. The budget is a manifest fraction of the slot's window minus an
  output reserve. Before every call the assembled prompt is token-counted (provider count where
  available, else an estimate with margin) and must fit; on overflow the lowest-scored items are
  dropped first, then oversized items are replaced by a reference. A `ContextTooLong` error
  shrinks the budget and retries; it never crashes a bee.
- **Nothing is lost by not fitting.** Anything that does not fit is still in Bee Bread or Honey and
  surfaces again when an event references it. Per-item size caps mean a large tool result is
  stored as Nectar with a reference in hot state, never inlined.
- **Demotion is a duty, not an emergency.** A House Bee sweep moves items that no longer link to
  an active task from hot state to Bee Bread, and ripens Bee Bread into Honey on the ripener slot.
  Compaction summarises from source records, never from a previous summary, and copies pins
  verbatim.
- **Cell Wax is a caution, not knowledge.** `memory/cell_wax.py` holds Queen-written notes about
  one Cell (a known limit, a risk, a quirk) with a `WaxSeverity` (`NOTE`, `CAUTION`, `BLOCK`),
  proposer, reason, clearance, optional expiry, and text capped by the manifest. Anyone may
  propose wax (a bee or Warden over Waggle, the Patrol, the human from the chat or a Cell's
  folder); only the Queen writes, rejects or clears it, by autopilot for a Warden's `NOTE` or
  `CAUTION` about its own Cell within the per-Cell cap and by awake decision for everything else,
  every `BLOCK` included. Wax enters hot state only while its Cell is a candidate for placement or
  assignment, so it costs nothing until it matters; placement treats `BLOCK` as exclusion and
  `CAUTION` as a penalty. A Real Cell's wax outlives its leases; a Virtual Cell's is retired when
  the Cell is destroyed. Cleared and expired wax is ripened into Honey at `cell:<id>` scope, so
  the history compounds without living in hot state. Every edge is a `memory.wax_*` event.
- **Handoffs are schema'd.** `memory/handoff.py` is a pydantic model with mandatory fields: goal,
  progress, decisions with reasons, tried and failed, constraints discovered, open threads, next
  steps, do-not-redo, pinned facts verbatim, bounded notes. Freeform text is capped.
- **One mechanism, many names.** Rebind, takeover, threshold reset, Warden migration, Clustering
  and Requeening are all the same operation: checkpoint, write a Handoff, deposit the transcript
  as Nectar, resume on a possibly different slot or host from the Handoff.
- **Threshold reset.** A bee whose telemetry crosses the manifest threshold (initially two thirds
  of its window) checkpoints and resets itself; its supervisor may order it earlier.
- **Stable prefix first.** Assembled prompts are ordered system prompt, tools, pins, hot state,
  then the event, so provider prompt caching keeps working.
- **Every tier carries a clearance.** Episode records, notes, pins, Handoffs, watch observations
  and Bee Bread entries carry a `HoneyClearance` exactly as Nectar and Honey do, and
  `memory.assemble` filters by the principal's clearance allowance, so a Night Veil bee never sees
  Royal data in hot state and never resumes from a Royal Handoff. Labels come from provenance at
  intake: anything from a Real Cell, a human message or watch mode is `C2`; a model may raise a
  label; only a judge verdict or a human may lower one.

### 8.10 Forage: capacity, and where models run

Forage is the Hive's capacity. It is several dimensions, never one number, and it is data in
`forage/`. **The Queen divides what is shared; a Warden divides what is on its own Cell, under
ceilings the Queen set once.**

- **Dimensions.** Host compute (`HostCapacity`: cores, memory, disk, GPUs and VRAM, with live
  load and free figures), model seats (`Seat`: one concurrent request on one model on one server;
  rate limits and spend caps for hosted providers), spend, and bee slots derived from the rest.
  Every Cell reports `ForageCapacity` at provision or enrolment and on change.
- **Footprints.** Every role has a `RoleFootprint` in the manifest: cpu, memory, one seat while
  mid-call, estimated token rate, extra memory with an Exoskeleton. A role without a footprint
  cannot be spawned; `lint-imports` cannot check this, so the registry refuses it.
- **The Forage map.** `forage/map.py` holds every `ModelSource`: model id, provider or server,
  host, `grade` 1 to 5 (hand-set, then replaced by `measured_grade` from the eval harness),
  context window, capability flags, cost, and the live `distance` (latency and tokens per second
  from a given Cell) and `abundance` (free seats). Tempo's accuracy bar sets a minimum grade and
  its latency budget a maximum distance; routing (8.6) picks the cheapest source that clears both
  with a seat free. Each role has a grade floor that tempo may raise and never lower.
- **Grants are computed, then metered.** `forage/allocate.py` is pure: `max_sub_bees` is the
  minimum of the Cell's cap, free memory over the footprint's memory, free cores over its cpu,
  reachable seats, and the goal's remaining bee cap; `allowed` bindings are those reachable under
  the Cell's network policy and hosting decision whose grade meets the floor and whose cost fits;
  budgets come from the goal's caps and tempo; the Royal Reserve (the Queen's, the Attendant's and
  the House Bees' seats and memory plus a margin) is subtracted first. Every model call then
  passes through the **Fanner** (`llm/fanner.py`): a per-binding semaphore sized to the seats
  held, a tempo-ordered queue for overflow, a rate limiter per hosted provider, and the only
  place seat counts are enforced. It measures speed and latency and updates the map and ledger.
- **Grants are leases.** A `ForageGrant` has `expires_at`, is renewed on the Warden's heartbeat,
  returns to the pool when the Warden is dead or offline, and can be shrunk or revoked. A Warden
  over its grant gets an Alarm, not a crash. The ledger recomputes grants when measurements drift
  past a manifest threshold. Grants, requests, denials and expiries are trail events.
- **Two pools.** The **shared pool** is Hive Stand seats, hosted seats and spend; only the Queen
  allocates it, by grant, after the Royal Reserve. A **local pool** is everything physically on
  one Cell: its cores, memory, disk, VRAM and any seats on a model server running on it. The
  Cell's Warden owns its local pool outright, runs the same pure allocator over it for its own
  sub-bees, keeps a small local reserve for its own awake mode and Patrol, and reports usage to
  the ledger; it never asks. Spend is always shared, because every Cell draws from one wallet. A
  Virtual Cell's spec is its ceiling, set at provisioning, so its in-cell Warden owns what is inside.
- **Ceilings, not approvals.** When the Queen puts a Warden on a device, or promotes it to a Nuc,
  she sets `Ceilings` once: maximum sub-bees (for access level and left-as-found reasons, not
  capacity), VRAM and disk the Warden may use for models, which Forage map entries it may load,
  and how many of its seats the Queen may borrow for other Cells. Within the ceilings the Warden
  never asks; changing a ceiling is a Queen decision on the trail. Loading an allowlisted model
  within the VRAM ceiling is autonomous and is reported as a new source, not requested. The Queen
  may later evict a local model only to reclaim seats she has exported.
- **A hosting plan, not a hosting flag.** Each Cell has a `HostingPlan`: per slot, a primary
  source and a fallback chain, plus a default for slots not named. The Queen writes it from the
  map and ledger (free VRAM against the model, seat pressure on the Hive Stand, measured distance
  against tempo, the task's need to survive disconnection, cost) and records it with its reason.
  A Cell with local models defaults to **local first, shared when needed**. Every model slot has a
  cost: a seat on a specific host, or hosted spend.
- **Spill-over is a Fanner rule.** A bee uses a local binding whenever its grade clears the
  tempo floor and a seat is free. The Fanner spills to the next binding in the plan's chain in
  exactly three cases: the local grade is below the floor, the model the task needs is not loaded
  locally, or the local queue has waited past a threshold derived from the latency budget. Every
  spill is an `llm.spill` event. Reaching for shared Forage beyond the standing grant is a
  `ForageRequest`, answered only while connected; offline it waits in the outbox.
- A Real Cell whose Warden and model server both run on it is a **Nuc**. It keeps working when
  disconnected because everything it needs is in its local pool. Promoting a device to a Nuc is a
  Queen decision based on the device's Forage and the Hive Stand's pressure: the Warden moves
  first by Handoff and resume, `pollen/bootstrap/` installs the runtime, then the Warden starts a
  model server through `wardens/local_pool/hosting.py` within the ceilings. Starting a server
  needs the Cell's `CellSession`, which is why that code lives with the Warden and not in
  `forage`.

### 8.11 Observation Hive: read everything, write through the Queen

The UI is a window, not a control panel. Its rules:

- **One write path.** The only thing the Observation Hive can send into the system is a message
  to the Queen's inbox: a task request, a question, an answer, a proposed note. No view calls a
  store, a Warden or a Cell directly, and the Entrance exposes no other write for the `observe`
  principal. A test enumerates the Entrance routes reachable with UI credentials and fails if any
  other mutating route is among them.
- **Everything else is a live read.** Fleet (with a Real / Virtual / All filter), Cell pages
  (diagram of bees and what each is doing, current tasks and goals, the Warden's Forage and Honey,
  `CombShieldLevel`, Pheromone Mask state, access level and mode, Cell Wax, Capping activity), Forage, Attendant views for the Queen and every
  Warden, thoughts, the Capping queue, the Honey browser and the trail are all views over the
  streams and read API in `entrance/streams/` and `observation/api.py`. Views never poll; they
  subscribe.
- **Core counters are always visible.** The Observation Hive shell includes a persistent summary
  strip with total active bees, active versus inactive Cells, Real versus Virtual Cell counts, and
  live LLM versus autopilot activity (bees in awake episodes versus deterministic autopilot
  handling). These counters update from streams, not polling, and remain visible while navigating.
- **Tier must be obvious at a glance.** `CombShieldLevel` is rendered as a persistent, high-contrast
  badge on every Cell page header and every Fleet row, with a fixed legend (`MEADOW`, `PROPOLIS`,
  `NIGHT_VEIL`) that is always visible in the Fleet view. Tier is never hidden behind a tooltip,
  drill-down or hover-only affordance.
- **Mask state must be obvious at a glance.** Pheromone Mask state is rendered as a persistent,
  high-contrast badge on every Cell page header and every Fleet row, including source (`WARDEN`
  or `QUEEN_FORCED`). Mask state is never hidden behind a tooltip, drill-down or hover-only
  affordance.
- **Thoughts are episode records** (section 12), scoped by `observe:thoughts`. "Full read access
  to any bee" means every episode record and every telemetry sample, not the trail.
- **The Honey browser is a view over provenance.** Folders are derived from scope (`/hive`,
  `/cells/<id>`, `/bees/<id>`, `/tasks/<id>`, `/bee-bread`), never stored as a second structure,
  and filtered by the viewer's `observe:honey:<scope>` capabilities and clearance allowances.
  Every listed item includes its `HoneyClearance` label.
- **Views are data-shaped, not code-shaped.** Every view has one pydantic read model in
  `observation/views/`; the web app renders those models and nothing else, so a view can be
  regenerated from the models alone. The React components take those models through TypeScript
  types generated from the committed OpenAPI document, never through hand-written shapes.
- **Reachable from any enrolled device.** The front end (`packages/observation-web/`) is a
  TypeScript + React app served by the Entrance as static files, responsive from phone width up,
  installable as a PWA, and packaged for Android with Capacitor. It is a Landing Board client
  like any other (section 8.15): it runs on an enrolled device, logs in with that device's key
  plus the operator's password, and has no privileged path. Wherever the Entrance is exposed, the
  Observation Hive is.

### 8.12 Capping: nothing lands unchecked

Beekeepers cap a honey cell only once the honey is ripe. In HiveMind, work is provisional until it
is capped, and uncapped work never leaves scratch.

- **Propose, then commit.** Every action with a side effect outside a lease's scratch directory,
  and every command in a tier above `read_only`, is a `Proposal`: what will be done, its risk tier,
  and the **postconditions** the bee expects to hold afterwards, stated before acting. The
  `CappingGate` runs the checks the tier requires, applies the proposal, verifies the
  postconditions, and rolls back on failure. Outcomes are `capping.*` trail events.
- **The proposer never verifies its own work.** Postconditions and acceptance criteria are
  checked by the bee's Warden, or by a judge, never by the bee that did the work. A task reaches
  `SUCCEEDED` only after its acceptance criteria pass.
- **Tiers are data, checks are layered, cheapest first.** `docs/supervision/capping-tiers.toml`
  maps each risk tier (`read_only`, `scratch_write`, `outside_scratch_write`, `network_egress`,
  `spend`, `device_command`, `irreversible`) to its checks: deterministic validators in autopilot
  (schema, lint, types, allowlists, size caps), then tests in a sandbox Cell, then an independent
  judge, then a human. Adding a tier or a check is a table edit.
- **The judge is independent.** Judge review runs on `ModelSlot.JUDGE` with a rubric and no shared
  context with the proposer, and the manifest may pin it to a different provider so blind spots
  do not correlate. Verdicts are structured: approve, request changes, reject, with reasons.
- **Snapshots make rollback whole-machine.** On Virtual Cells the gate snapshots before
  `irreversible` and `device_command` proposals and rolls the Cell back when postconditions fail.
  The gate calls the `Snapshotter` protocol from `cell/snapshot.py`, which the Warden injects;
  `hive/snapshot.py` implements it for Docker and QEMU and Real Cells get a documented no-op, so
  Capping at Layer 2 never imports `hive` at Layer 3.
- **GUI work is recorded, not trusted.** While an Exoskeleton is attached the flight recorder
  keeps every action with before-and-after frames and structural snapshots as Nectar, referenced
  from the episode record, never on the trail or in logs. Structural assertions (URL, accessibility
  tree, element text) are preferred over pixels; reusable browser procedures are rehearsed on a
  fixture and promoted as tools before they run against a real target.
- **What cannot be gated is sampled.** Tiers the table marks as ungated in real time are audited
  after the fact at a per-tier sampling rate by the judge; findings become Nectar and Alarms, and
  the Guard Bee raises a tier's rate when its failure rate climbs.

### 8.13 Clustering: pause and preserve

When a provider becomes unavailable and no fallback slot fits within Forage, or a cost cap is hit,
the Queen does not try to run the Hive on autopilot alone; she clusters. `queen/cluster/`
checkpoints every affected bee, moves their tasks to `PAUSED`, keeps leases and Cells alive
(Overwintering Virtual Cells if the outage is long), keeps heartbeats and watchdogs running, polls
provider health with backoff, and resumes every paused bee from its Handoff when the provider
returns. Bees bound to unaffected providers continue. The protocol is per provider, can be
triggered by hand (`hive cluster`, `hive wake`), and a Warden offline past its limit runs the same
protocol locally.

### 8.14 Tempo: speed against accuracy

Every task carries a **tempo**: how fast it must be done and how right it must be. The waggle dance
encodes how far and how good a source is; tempo is the Hive's version of that signal, and every
allocation decision reads it.

- `TaskNeeds.tempo` holds a latency budget (optional, seconds) and an accuracy bar (`LOW`,
  `NORMAL`, `HIGH`, `CRITICAL`). The planner sets both per subtask; the human may set them on a
  goal; the Attendant weighs the latency budget when ordering the inbox.
- **Routing reads tempo.** `llm/routing.py` picks the slot, the provider and the model's effort
  setting from tempo and Forage together: a fast local model at low effort for an urgent, low-bar
  task; the strongest slot at high effort for a critical one. Effort is a per-binding value, never
  a global.
- **Forage reads tempo.** Urgent tasks may be granted more parallelism; thorough tasks may be
  granted more spend. The allocator's inputs include tempo, and its decision records why.
- **Capping reads tempo, within floors.** Tempo can shorten the check ladder for low-risk tiers
  (skip the judge for an urgent `scratch_write`) and lengthen it for critical ones (add a second
  judge). It can never remove a check the tier table marks as a floor; `irreversible` always gets
  its full ladder however urgent the task claims to be.
- **The Queen tunes her own effort.** Autopilot sets the effort of each awake episode by event
  class: routine decisions run at low effort, planning and contested Forage at high. This is an
  autopilot rule, not a model choice.
- Tempo never overrides safety: access levels, capability attenuation and the left-as-found rule
  are unaffected by how urgent a task is.

### 8.15 The Hive Entrance and the Landing Board: one door, one keeper

The Hive Entrance (`entrance/`) is the only way anything outside the process talks to the Hive:
the Observation Hive, the `hive` CLI, a phone, a pair of glasses, another program. The **Landing
Board** is its public contract, named for the platform at a real hive's entrance where every bee
lands before going in. Guard bees admit only bees that carry the colony's scent; the Landing
Board admits only devices the operator enrolled at the Hive Stand. The rules:

- **One operator, no sign-up.** Brood 1.0 has exactly one operator principal. There is no user
  table to register into; `hive entrance operator add` is a loopback-only command and refuses
  unless `[entrance] operators` is raised above one.
- **Devices are enrolled, then approved at the Hive Stand.** `entrance/enrol/` mirrors Swarm
  enrolment (roadmap 11.1): a short-lived, single-use invite minted on loopback, a keypair the
  device generates and keeps (a WebAuthn passkey in a browser, an Ed25519 key in a program's
  secure storage), a pending record, and an approval that binds name, `CapabilitySet`, spend cap
  and expiry. Approval, capability widening, unlock and reopening exist **only on the loopback
  listener**; the remote listener does not serve those routes at all (a 404, not a 403), and the
  route test from 8.11 asserts it. A steward device (`[entrance] steward_devices = true`, off by
  default) may approve after full step-up; nothing else can. Glasses are their companion phone;
  they never enrol on their own.
- **Two factors, one of them the device.** Login is the device key (a passkey assertion with
  user verification, or a signed challenge) plus the operator's password (Argon2id). Sessions
  are short (`session_ttl_hours`), bound to the device key so a stolen token is useless without
  it, idle out, and are revoked instantly with the device. Step-up (`step_up_window_minutes`)
  re-runs both factors and is required for spend above `step_up_spend`, key and capability
  changes, Supersedure, Sting Cut, Absconding and reopening after a reduction; break-glass actions
  additionally need the typed confirmation from section 15 on every path, the API included.
- **Never on the open internet.** `entrance/expose.py` reads `[entrance] expose`: `loopback`
  (always on), `vpn` (recommended for remote access: a WireGuard or Tailscale overlay, the remote
  listener bound to the overlay interface only, so unauthenticated packets never reach the
  Entrance), `lan` and `tunnel` (both require TLS and mutual TLS with the device certificate on
  top of login). There is no `public` value, and the Entrance refuses to start exposed without
  TLS.
- **Guard Bees watch the door; the Entrance Reducer narrows it.** `entrance/reducer.py` drops
  the Entrance to loopback only and kills every remote session, on `hive entrance reduce` or on a
  Guard Bee autopilot rule (failure bursts, lockouts across devices, an unknown client hammering
  the invite route); only loopback reopens it, with step-up. Narrowing access is always safe, so
  autopilot may do it without judgement. Lockout after `lockout_attempts` failures per device,
  rate limits per device and per address, and every enrolment, approval, denial, revocation,
  lockout, step-up and reduction is a `guard.entrance.*` trail event pushed to every other
  enrolled device. The optional travel lock (`travel_lock = true`) forces step-up and a
  notification when a known device appears from a new network; it never approves anything.
- **Versioned and described.** Routes live under `/v1/`. `entrance/landing_board.py` generates
  the OpenAPI document from the pydantic route models and it is committed as
  `docs/entrance/openapi.json`; CI fails when the generated document differs from the committed
  one. Additive changes stay within `/v1/`; a breaking change is `/v2/` with `/v1/` kept for one
  Brood. Loopback-only routes are marked as such in the document.
- **Push, not polling.** `entrance/push/` defines a `PushChannel` protocol with three
  implementations: WebSocket for live clients, signed webhooks for programs (signed with the Hive
  key, retried with backoff, idempotent by event id), and web push for phones (native push on
  Android through the Capacitor build). Subscriptions are per device, filtered by capability, and
  persisted (Appendix C). What is pushed: a question for the human, an Alarm that reached the
  human, a reply from the Queen, the completion of a goal the device submitted, and every
  Entrance security event. Push payloads say only that something is waiting, never the content,
  because they transit third-party push services. A question answered on any device is withdrawn
  from every other.
- **Programs get narrow keys and spend caps.** A program is a device with a small
  `CapabilitySet` (usually `entrance:submit`, `entrance:answer`, `observe`) and a daily spend cap
  set at approval. It can never hold more than the operator granted on loopback.
- **Voice is transcribed at the door.** An enrolled device may send audio instead of text: a
  clip on `/v1/chat/audio`, or audio frames on the chat WebSocket for push-to-talk. `entrance/voice.py`
  transcribes it on `ModelSlot.TRANSCRIBER` (8.6), Whisper by default and local first, and the
  transcript enters the Queen's inbox as a `HumanMessage`. A spoken goal is echoed back for
  confirmation before it is submitted (`[entrance.voice] confirm_goals`, on by default), so a
  misheard sentence never spends anything; answers and chat go straight through. Audio and
  transcript are `C2`; the audio is discarded after transcription unless `keep_audio` is set, in
  which case it is Nectar with a retention window. Clips are capped by `max_clip_seconds`.
  Replies are text; speech synthesis is post-1.0. Images a client sends are deposited as Nectar
  with clearance `C2`, since they come from the human. On a Night Veil Cell the same slot resolves
  to a local Whisper, because every slot there is local.
- **The Observation Hive is a client.** The React app, as a web page, a PWA or the Android APK,
  holds no privilege the Landing Board does not grant to the device it runs on.

### 8.16 Supersedure: moving the Hive Stand

The Hive Stand is a role, not a machine. It is identified by the Hive keypair and by the Queen's
address, which lives in `[hive_stand] address` and in every device's enrolment record. Supersedure
(`queen/supersedure/`), named for a colony raising a new queen while the old one still lays, moves
the role to another machine without losing a task.

- **The candidate is a colonized Real Cell.** Level 1 or a Nuc, `FULL` access, the same runtime
  version, disk for the stores plus headroom, Forage that covers the Royal Reserve, reachable from
  every Swarm node and from every Virtual Cell backend (they all connect out), the Hive's secrets
  present in its secret store, clock skew within bound. `hive doctor --supersedure <node>` checks
  all of it before anything starts.
- **Freeze, then copy.** The Queen clusters every provider (8.13): every bee checkpoints, every
  lease and Cell stays alive, every grant is frozen. `queen.supersedure_started` is written before
  the first byte is copied. The stores move with `hive backup` over the candidate's `CellSession`
  and `hive restore` on the far side, checksummed. The Hive keypair moves only through the secret
  store, never over Waggle.
- **Handover.** The old Queen signs a `QueenMoved` (new address, effective at, grace until) to
  every Warden and Pollen Packet; each persists the new address and reconnects. Both addresses
  are honoured during the grace window. The new Queen starts in `REQUEENING` from the copy,
  reconciles with every Cell and Warden, and resumes every paused bee from its Handoff. The old
  Queen moves to `SUPERSEDED` and stops. Its machine keeps its Warden and joins the Swarm as an
  ordinary Real Cell (a Nuc if it had a model server), in `WATCH` until it is given work. The
  Entrance, both listeners, and every enrolled device record move with the stores; devices learn
  the new address from the same signed notice.
- **Never two Queens.** The old Queen never leaves `CLUSTERED` once the copy starts; on any
  restart it finds the unfinished `queen.supersedure_started` in its own trail and stays put until
  it sees the outcome. The new Queen leaves `REQUEENING` only when the old one has acknowledged
  `SUPERSEDED` or the grace window has expired with the old Queen unreachable.
- **Rollback.** If the new Queen fails readiness before the grace window ends, the old Queen
  wakes, sends a `QueenMoved` pointing back at itself, records `queen.supersedure_aborted`, and
  resumes. Nothing is lost either way, because nothing ran while the copy was in flight.
- **Human-initiated, on loopback, with step-up.** `hive supersede <node>` with confirmation;
  never an autopilot rule or an awake decision. Every step is a `queen.supersedure_*` event.

---

## 9. Types and data models

- `mypy --strict` passes. No `Any` in signatures. Untyped third-party libraries get a stub in
  `typings/` or a `# type: ignore[import-untyped]  # reason` with a reason.
- Every function is fully annotated, including `-> None`.
- Use `NewType` for every identifier (`CellId = NewType("CellId", str)`).
- Use `Literal` and `Enum` for closed sets; never bare strings for states or kinds.
- State machines are modelled as an `Enum` plus a **single** transition table in one file per
  machine (`brood_chamber/task_state.py`, `wardens/state.py`, and so on) with a comment on every
  allowed edge. Appendix C lists every machine, its owner and where it persists; a new machine is
  added there in the same PR that introduces it.
- Optional means "absence is meaningful". Do not use `Optional` as "I haven't decided yet".
- Return a `Result`-style value only at the LLM boundary, where partial failure is normal. Everywhere
  else raise (section 10).
- Pydantic models are the **only** thing that reads JSON/TOML. `dict[str, Any]` never leaves the
  codec that produced it.

---

## 10. Errors and exceptions

- One root: `hivemind.common.errors.HiveMindError`. Every subsystem defines its own subclass tree
  in `<subsystem>/errors.py` (`HiveError`, `CellProvisionError(HiveError)`, and so on).
- `waggle` has its own root `WaggleError`; `pollen` has `PollenError`. They do not inherit from
  `HiveMindError` because they cannot import it.
- Exception messages are full sentences containing the identifiers needed to debug
  (`"Cell c_01H... did not report ready within 60s (backend=docker, image=desktop-ubuntu)"`).
- **NEVER** `except Exception:` or bare `except:` outside the three places allowed: the top of a
  Worker's run loop, the top of a CLI command, and the Pheromone Trail writer. Each of those
  re-raises after logging or converts to a typed error, and carries a `# SAFETY:` comment.
- **NEVER** swallow an exception silently. If ignoring is correct, log at `debug` with the reason.
- Use exception chaining (`raise CellProvisionError(...) from exc`) so the Pheromone Trail keeps the
  cause.
- Errors that cross Waggle are serialised as `ErrorMessage` with a stable `code` field
  (`hive.cell.provision_failed`), never as a stringified traceback.

---

## 11. Async and concurrency

- The Queen, Workers, transports and stores are `asyncio`-native. Public APIs that do I/O are
  `async def`.
- **Structured concurrency only.** Spawn with `asyncio.TaskGroup`; never a bare
  `asyncio.create_task` whose handle is dropped. Every task has an owner that awaits or cancels it.
- **Every** external await has a timeout (`asyncio.timeout(...)`), and the value is a named constant
  from the manifest or the subsystem's `constants.py`.
- Blocking calls (hypervisor SDKs, `sqlite3` when not using `aiosqlite`, image decoding) run under
  `asyncio.to_thread` in the adapter that owns them, never in core code.
- Cancellation is honoured: no `except asyncio.CancelledError` that does not re-raise.
- Shared mutable state is confined to one owning object per resource, guarded by an `asyncio.Lock`
  whose purpose is documented at the declaration.
- Long-running loops (`Queen.run`, `Worker.run`, `PollenPacket.run`) follow the same shape:
  `while not self._stop.is_set():` → one iteration in a named method → catch typed errors → record
  to Pheromone Trail → back off. The shape is documented once in `waggle/loop.py` and reused.

---

## 12. Logging and the Pheromone Trail

Two distinct things:

- **Logs** are for humans debugging a process. `structlog`, bound with `hive_id`, `component`,
  and where relevant `cell_id`, `task_id`, `worker_id`. Levels: `debug` (noise), `info` (lifecycle
  milestones), `warning` (recovered problem), `error` (failed operation), `critical` (Queen
  integrity at risk).
- **The Pheromone Trail** is the system's audit record. Every state-changing action (Virtual
  Cell provisioned, Real Cell leased or released, task assigned, tool promoted, device enrolled,
  command sent to a device, path touched outside a lease's scratch directory) writes a
  `PheromoneEvent` **before** the action is considered complete. Events are append-only, typed,
  and stored durably. The dashboard reads them; humans audit them. The one exception is the Night
  Veil boundary defined below.

Rules:

- **NEVER** `print()` outside `cli/` output formatting.
- **NEVER** log secrets, tokens, full page contents, or screenshots. Log identifiers and sizes.
- **NEVER** retain a Night Veil Cell's execution records after teardown. **The Night Veil
  boundary:** a Night Veil Cell's execution records live in an ephemeral segment keyed to the
  Cell and purged at teardown: session commands and their output, episode records and assembled
  prompts, Nectar, Handoffs, flight recordings, `llm.call` events, the VPN gateway's and Tor
  daemons' own per-Cell connection and circuit logs (including the Hive Stand's hidden-service
  logs for that Cell), the Cell's local trail detail and its logs. What survives, on the Queen's
  trail, is the lifecycle skeleton: `cell.provisioned`,
  `cell.attested` (pass or fail, per check), `queen.placed` with the id of the human request that
  asked for Night Veil, `forage.granted`, `forage.plan_written`, `cell.sting_cut`, task state
  transitions carrying nothing beyond the task id, one `capping.summary` per tier with counts of
  approved, rejected and rolled back, and `cell.destroyed`. Honey the work ripened on the Cell's
  own local slots at `C0` or `C1` is deposited on purpose and kept, labelled
  `origin_tier = NIGHT_VEIL`. Nothing else crosses the boundary.
- Log messages are lowercase event names with fields, not prose: `log.info("cell.ready", cell_id=..., took_s=...)`.
- Every subsystem gets its logger from `common.logging.get_logger(__name__)`; no logger
  configuration outside the composition root.
- The trail is one logical log made of per-node segments. A Warden that is offline writes to its
  local segment; on reconnection the segment merges into the Queen's trail. Event ids are ULIDs and
  every event carries its node id, so merges never collide and ordering across nodes is by
  timestamp plus node id, documented as approximate.
- **Thoughts are memory, not audit.** What a bee saw and decided (the assembled context by
  reference, the provider's reasoning summary where exposed, the rule that fired or the decision
  made) is an `EpisodeRecord` in Bee Bread with a retention window. It is readable in full through
  the Observation Hive by anyone holding `observe:thoughts`, streamable live, and redacted at the
  source (secrets, screenshot bytes). The trail records that an episode happened and what it
  cost, never its text.

---

## 13. Configuration and secrets

- All configuration is a **Hive Manifest**: a TOML file validated into `HiveManifest` (pydantic) in
  `manifest/`. Subsystems receive the slice they need (`manifest.hive`, `manifest.honey_store`)
  as typed objects, never the whole manifest and never raw dicts.
- Environment variables are read in exactly one place (`manifest/env.py`), are prefixed
  `HIVEMIND_`, and only override manifest values. They are documented in `docs/manifests/`.
- Defaults live in the pydantic model with a `description`, not scattered through code.
- The `[llm]` section declares providers and **model slots** (section 8.6). Model ids, base URLs
  and prices appear only there. Code that needs a model asks for a slot (`ModelSlot.QUEEN`),
  never a name. Example shape, kept current in `docs/manifests/full.toml`:
  ```toml
  [llm]
  offline = false                        # true refuses every non-loopback provider

  [llm.providers.anthropic]
  kind = "anthropic"                     # key comes from HIVEMIND_ANTHROPIC_API_KEY

  [llm.providers.local]
  kind = "openai_compat"                 # Ollama, vLLM, llama.cpp server, LM Studio
  base_url = "http://127.0.0.1:11434/v1"

  [llm.providers.whisper]
  kind = "whisper_local"                 # faster-whisper in process; GPU when present

  [llm.slots]
  queen    = { provider = "anthropic", model = "claude-opus-5" }
  worker   = { provider = "anthropic", model = "claude-sonnet-5", fallback = "local_worker" }
  local_worker = { provider = "local", model = "llama3.1:8b" }
  ripener  = { provider = "local", model = "llama3.1:8b" }
  embedder = { provider = "local", model = "nomic-embed-text" }
  transcriber = { provider = "whisper", model = "large-v3-turbo" }
  ```
- The `[hive_stand]` section names the Queen's address (`address`, the URL Wardens and Pollen
  Packets connect out to) as well as `enabled` and `scratch_root`. The address is the one value
  Supersedure (8.16) rewrites on every node.
- The `[entrance]` section controls the door (8.15). Example shape:
  ```toml
  [entrance]
  bind = "127.0.0.1:8710"                # the loopback listener; always on
  expose = "loopback"                    # loopback | vpn | lan | tunnel; there is no public mode
  remote_bind = ""                       # the remote listener; set when expose is not loopback
  public_url = ""                        # used for CORS, webhooks and the PWA manifest
  tls = { cert = "", key = "" }          # required for lan and tunnel; vpn may rely on the overlay
  mutual_tls = true                      # lan and tunnel refuse to start with this false
  operators = 1                          # Brood 1.0 is single-operator
  steward_devices = false                # let one enrolled device approve others after step-up
  travel_lock = false                    # step-up and notify when a known device changes network
  session_ttl_hours = 12
  idle_timeout_minutes = 30
  step_up_window_minutes = 5
  step_up_spend = 5.00                   # spend per goal above which step-up is required
  lockout_attempts = 5
  rate_limit_per_device = 60             # requests per minute

  [entrance.push]
  webhooks = true
  web_push = true                        # VAPID keys come from HIVEMIND_ENTRANCE_VAPID_*

  [entrance.voice]
  enabled = true                         # audio in on the chat route, transcribed on the transcriber slot
  confirm_goals = true                   # echo a spoken goal back before it becomes a task
  keep_audio = false                     # discard audio after transcription; true keeps it as C2 Nectar
  max_clip_seconds = 120
  ```
- **Secrets** (API keys, device enrolment tokens, cloud credentials) are never in the manifest
  file, never in code, never in logs, never in the Pheromone Trail. They come from environment
  variables or a secret store adapter, are held in `SecretStr`, and are redacted in `repr`.
- Every example manifest under `docs/manifests/` is loaded in a test so the docs cannot drift.

---

## 14. Testing

### 14.1 Targets

| Layer | Coverage floor | Notes |
|---|---|---|
| `common`, `waggle`, `brood_chamber`, `guard`, pure cores | 95% | These are cheap to test and expensive to get wrong. |
| Adapters (backends, transports, stores) | 80% | Integration-tested against the real thing where possible. |
| `cli`, `observation` | 60% | Smoke tests plus critical paths. |
| Repository total | 85% | CI fails below this. |

Coverage is a floor, not a goal. A test that asserts nothing meaningful does not count.

### 14.2 Layout and naming

- `tests/unit/` mirrors `src/` exactly (section 3). One test module per source module.
- Test names: `test_<unit>_<behaviour>_<condition>`. Read them as sentences.
- Arrange / Act / Assert with a blank line between each, and a comment on the arrange block when
  the setup is not obvious.
- Markers: `@pytest.mark.integration` (needs SQLite file, Docker, network),
  `@pytest.mark.e2e` (whole Hive), `@pytest.mark.slow`. Unit tests run in under 30 seconds total.

### 14.3 What to test

- Every Protocol has a **contract test suite** (`tests/contracts/test_<protocol>_contract.py`)
  parametrised over all implementations, including the fake. A new backend passes the contract
  before it is registered.
- Every state machine has a test that walks every allowed transition and asserts every forbidden
  one raises.
- Every pydantic boundary model has a round-trip test (serialise → deserialise → equal) and a
  rejection test for at least one malformed input.
- Every LLM-facing prompt has a snapshot test on the rendered prompt and a test with a
  `FakeLLMProvider` returning a canned response.
- Every `LLMProvider`, `EmbeddingProvider` and `TranscriptionProvider` implementation passes its
  provider contract suite against recorded HTTP cassettes or fixture clips (no network in CI).
  The suite exercises each capability level so the degradation ladders are proven for weak
  models, not just for the strongest one.
- Property-based tests (`hypothesis`) for codecs, id generation, and the Ripening chunker.

### 14.4 Fakes over mocks

- Prefer hand-written fakes that implement the Protocol honestly (`FakeCellBackend`,
  `FakeTransport`, `FakeLLMProvider`) over `unittest.mock`. Fakes live in `src/` beside their
  Protocol as `fake.py` (section 3), never under `tests/`, because `pollen`, `hive doctor` and
  demo paths use them too.
- `mock.patch` is allowed only to isolate a third-party SDK at the very edge, and the patched
  path must be a name in our own adapter module, never deep inside the library.
- Fakes are production-quality code: documented, typed, size-limited like everything else.

### 14.5 Test data

- Builders, not fixtures with 20 fields: `make_task(status=TaskStatus.RUNNING)` with sensible
  defaults in `tests/builders/`.
- No sleeping in tests. Use fake clocks (`waggle.clock.Clock` Protocol with `FakeClock`).
- Integration tests create their own temp directory / SQLite file and clean up.

---

## 15. Security and safety

HiveMind can provision compute, drive GUIs, author tools, and reach into external devices. Every
rule here exists because a bug in this system has a large blast radius.

- **Least privilege is code, not policy.** A Worker receives a `Capabilities` object listing the
  tools, network scopes and devices it may use. The `guard` module checks it on every dispatch. A
  Worker cannot escalate by asking.
- **Tools never skip the Quarantine Comb.** `CombRegistry.promote()` refuses a tool without a
  passing `QuarantineReport`. There is no `force=True`. Ever.
- **The sandbox is the boundary.** Tool code under quarantine runs in a container (or VM) with no
  network by default, CPU/memory/time limits, and a read-only view of the repository. The
  subprocess sandbox exists for local development only and is refused when `manifest.env != "dev"`.
- **Subprocess calls** use argument lists, never `shell=True`, and carry a `# SAFETY:` comment.
  Inputs that reach a subprocess are validated by a pydantic model first.
- **Device commands are signed.** Every Waggle message to a Pollen Packet carries a signature over
  the envelope; the packet verifies it before executing. Enrolment uses one-time tokens.
- **Nothing is executed from the Honey Store.** Honey is data. It is never `exec`'d, never
  templated into a shell, and is treated as untrusted when it reaches an LLM prompt (it is delimited
  and labelled as retrieved content).
- **LLM output is untrusted input.** Tool calls proposed by a model are validated against the
  tool's schema and the Worker's capabilities before execution. Text from a model is never used as
  a file path, URL, or command without validation.
- **Blast radius defaults.** New Virtual Cells get no inbound ports and outbound network only to
  what the task's capability set lists. Absconding (tear everything down) must always work even if
  the Brood Chamber is corrupt, and it releases every Real Cell lease as well as destroying every
  Virtual Cell. Absconding is human-only and break-glass: it requires password re-auth and an
  explicit typed confirmation phrase, and it is never callable by Queen, Wardens, Workers,
  autopilot, or tools.
- **Capabilities and Forage only attenuate down the tree.** A sub-bee's capability set and grant
  are subsets of its Warden's, which are subsets of what the Queen issued. Nothing below the Queen
  can widen either; escalation is the only way up.
- **An offline Warden cannot grow.** Disconnected, it may spend its existing grant and nothing
  more: no new Cells, no new slots, no human-bound questions. A device with no Warden on it runs the
  dead-man switch from 8.8.
- **Watching is bounded by the access level.** Watch mode observes only what `READ_ONLY` allows
  on that device, never writes, never captures screen or input, keeps observations under a
  retention window, and is visible in the UI as the Cell's mode. Screen or input capture on a Real
  Cell is a separate capability that is never issued implicitly. Watch observations are `C2` by
  construction, because they describe the operator's own machine.
- **Nothing lands uncapped.** Every side effect outside scratch goes through the Capping gate
  (8.12) with declared postconditions, and the bee that proposed it never verifies it.
- **Real Cells are borrowed.** A Worker on a Real Cell works inside its lease's scratch directory.
  Anything outside it (files, services, startup items, package installs, ports) needs an explicit
  capability, is recorded on the trail with the path, and is restored or removed on release where
  the lease can do so. `release()` kills every process the lease started and removes the scratch
  directory. The Hive Stand is a Real Cell only when the manifest enables it, and a task with
  `isolation = "required"` never lands on a Real Cell.
- **The Entrance is the only door, and only enrolled devices get through it.** Two listeners;
  approval, unlock, capability widening and reopening exist only on loopback; every login is the
  device key plus the operator's password; sessions are bound to the device key; step-up for
  anything sensitive; no public mode; VPN overlay by default and mutual TLS for LAN and tunnel;
  lockout, rate limits and the Entrance Reducer; every security event trailed and pushed to every
  other device (8.15). A program's key never exceeds what the operator granted on loopback.
- **Supersedure is human-initiated and never leaves two Queens running.** The old Queen stays
  clustered from the moment the copy starts until the new Queen is acknowledged or the move is
  rolled back, and the Hive keypair moves only through the secret store (8.16).
- Dependencies are audited in CI (`pip-audit` for Python, `pnpm audit` for the front end); a
  known-vulnerable pin blocks merge.

---

## 16. Git workflow

- `main` is always green: lint, types, import-linter, unit tests, coverage floor.
- Branch names: `<type>/<short-kebab-description>`, e.g. `feat/waggle-envelope`,
  `fix/undertaker-double-teardown`, `docs/adr-0003-transport`.
- **Conventional commits.** `type(scope): summary` where scope is the subsystem
  (`feat(hive): add docker cell backend`). Types: `feat`, `fix`, `refactor`, `test`, `docs`,
  `chore`, `perf`, `ci`. Body explains *why*; footer links issues.
- One logical change per commit. A commit that touches three subsystems is three commits.
- PRs have no size cap. A PR is one logical change, however many lines that takes, provided every
  file in it meets sections 3-13: modular under the 5.1 limits, layered per section 4, commented
  per section 7. Split a PR only when it mixes two logical changes, never to hit a line count. The
  PR description states the roadmap phase and step it advances.
- Every PR that adds a Protocol, a backend, a storage format, or a message type links the ADR.
- No force-push to shared branches. Rebase your own branch freely.

---

## 17. Definition of done

A change is done when **all** of the following hold:

1. Code follows sections 3-13 (layout, size, naming, comments, types, errors, async, logging, config).
2. Tests from section 14 exist and pass, coverage floors hold, and the contract suite passes for
   any new Protocol implementation.
3. `ruff format`, `ruff check`, `mypy --strict`, `lint-imports`, and `pip-audit` are clean.
4. Every new public name has a docstring; every new module has the full header from 7.2.
5. Any hard-to-reverse decision has an ADR, and the subsystem `README.md` is updated.
6. `.claude/roadmap.md` has the corresponding step checked off, or a new step added if scope grew.
7. The Pheromone Trail records every new state-changing action the change introduced.
8. The change was run once for real (CLI command, integration test, or e2e) and the output is
   pasted in the PR description.

---

## Appendix A: File templates

### A.1 Module with a Protocol and one implementation

```python
"""Define the CellBackend protocol: how the Hive provisions and destroys Virtual Cells.

A Virtual Cell is an isolated VM or container the Hive (the Virtual Cell fleet) creates for one
Worker (a subagent) and destroys afterwards. It is the owned counterpart of a Real Cell, an
existing device that is borrowed and released instead (see hivemind.cell). The Hive needs to
create, inspect and destroy Virtual Cells on more than one kind of infrastructure: local containers
for development, local hypervisors, and cloud providers. This module defines the single interface
all of those share so the Queen (the orchestrator) never depends on a specific backend.

Fits into the Hive:
    Layer 3 (sources of Cells). Called by hivemind.hive.lifecycle, which the Queen uses through
    the hive package's public API. Implementations live in hivemind.hive.backends.*. The Cell
    this returns is handed to workers.launch, which opens an InCellSession on it.

Key invariants:
    - provision() either returns a running Cell or raises CellProvisionError; it never leaves a
      half-created Virtual Cell behind (implementations must clean up on failure).
    - destroy() is idempotent: destroying an already-destroyed Cell is a no-op, not an error.
    - Every Cell returned has kind == CellKind.VIRTUAL; Real Cells never pass through here.

See Also:
    - docs/adr/0018-cell-backends-docker-first-qemu-second.md
    - hivemind.cell for the Cell abstraction both kinds share
    - hivemind.hive.backends.docker for the reference implementation
"""

from __future__ import annotations

from typing import Protocol

from hivemind.cell import Cell
from hivemind.common.ids import CellId
from hivemind.hive.models import VirtualCellSpec

__all__ = ["CellBackend"]


class CellBackend(Protocol):
    """Provision and destroy Virtual Cells on one kind of infrastructure.

    Implementations are registered by name in the Hive Manifest (`[hive] backend = "docker"`)
    and constructed in the composition root. They must be safe to call concurrently: the Queen
    may provision several Cells at once during Swarming (scale-up).
    """

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """Create a Virtual Cell matching ``spec`` and return it once it is reachable.

        Args:
            spec: Image, resources, lifetime and network policy for the new Cell. Already
                validated against the manifest; implementations may assume it is well-formed.

        Returns:
            A Cell of kind VIRTUAL whose capabilities reflect the image (a desktop image reports
            has_display and has_audio). The Cell is running, its Warden has connected out to the
            Queen (Virtual Cells expose no inbound ports) and its first Heartbeat has arrived
            when this returns.

        Raises:
            CellProvisionError: The backend could not create the Cell, or it did not become
                reachable within ``spec.ready_timeout_s``. Any partial resources are already
                cleaned up when this is raised.
        """
        ...

    async def destroy(self, cell_id: CellId) -> None:
        """Tear down the Virtual Cell and release every resource it held.

        Idempotent: unknown or already-destroyed ids return silently, because an Undertaker
        (the cleanup Worker) may retry after a partial failure.

        Args:
            cell_id: The Cell to destroy.

        Raises:
            CellDestroyError: The backend acknowledged the Cell exists but could not remove it.
                The caller should record this on the Pheromone Trail and retry later.
        """
        ...
```

### A.2 A pure-core function with the expected comment density

```python
def choose_cells_to_overwinter(
    idle: Sequence[CellSnapshot],
    policy: OverwinterPolicy,
    now: datetime,
) -> tuple[CellId, ...]:
    """Pick which idle Virtual Cells to pause (Overwinter) instead of destroying.

    Overwintering keeps a Virtual Cell dormant so the next task with the same image starts in
    seconds instead of minutes. The policy caps how many we keep, because dormant Cells still cost
    disk. Real Cells never reach this function: they are released, not kept.

    Args:
        idle: Virtual Cells with no assigned task, newest-idle first. Must not contain running
            Cells or Real Cells.
        policy: Limits from the manifest: maximum dormant count and maximum idle age.
        now: Injected clock so the decision is deterministic in tests.

    Returns:
        Ids to overwinter, in priority order. Everything else in ``idle`` should be destroyed.
    """
    # Cells that have been idle longer than the policy allows are stale: their image may be out
    # of date and their disk is better reclaimed. Filter them out before ranking.
    fresh = [c for c in idle if now - c.idle_since <= policy.max_idle_age]

    # Prefer the images we use most; a dormant Cell for a rare image is wasted disk.
    # WHY: sorting by usage rather than recency avoids keeping one-off Scout images around.
    ranked = sorted(fresh, key=lambda c: policy.image_usage.get(c.image, 0), reverse=True)

    # Hard cap from the manifest. Slicing an empty list is fine, so no special case for zero.
    return tuple(c.cell_id for c in ranked[: policy.max_dormant])
```

### A.3 Subsystem `__init__.py`

```python
"""The Hive: on-demand Virtual Cell (VM/container) provisioning and lifecycle.

Real Cells (borrowed devices) are not produced here; see hivemind.cell.local and hivemind.swarm.

Fits into the Hive:
    Layer 3 (sources of Cells). Called by hivemind.queen.placement to provision and by the
    Undertaker (the cleanup Worker) to destroy; calls into hivemind.cell and hivemind.pheromone.

Key invariants:
    - Every Cell produced here has kind == CellKind.VIRTUAL; Real Cells never pass through.

See Also:
    - docs/adr/0018-cell-backends-docker-first-qemu-second.md
    - hivemind.cell.local and hivemind.swarm for the Real Cell sources

Public API:
    - CellBackend: protocol every provisioning backend implements.
    - VirtualCellSpec: the request; the result is a hivemind.cell.Cell of kind VIRTUAL.
    - CellLifecycle: provision → bind → teardown / overwinter, with Pheromone Trail events.
    - register_backend, get_backend: the backend registry used by the composition root.
"""

from hivemind.hive.backends.base import CellBackend
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.registry import get_backend, register_backend

__all__ = [
    "CellBackend",
    "CellLifecycle",
    "VirtualCellSpec",
    "get_backend",
    "register_backend",
]
```

### A.4 ADR

```markdown
# ADR-0018: Virtual Cell backends behind a single CellBackend protocol

- Status: Accepted
- Date: 2026-09-06

## Context
(What forces are at play: the README requires local-first provisioning with cloud later; Windows
Home has no Hyper-V; Workers must not care whether their Cell is Real or Virtual, let alone which
backend made it.)

## Decision
(One paragraph. The thing we are doing.)

## Consequences
(Positive, negative, and what becomes easier or harder.)

## Alternatives considered
(Each with one sentence on why it lost.)
```

---

## Appendix B: Pre-commit checklist

Run through this before every commit. Every line is a yes/no.

- [ ] The file I touched is under 300 lines of code (comments and docstrings excluded) and each
      function under 50 lines.
- [ ] The module docstring has the four parts: summary, explanation, "Fits into the Hive", invariants.
- [ ] Every Hive term in this file is defined in plain English on first use.
- [ ] Every public name has a Google-style docstring with Args / Returns / Raises.
- [ ] Every logical block, non-obvious branch, loop, early exit and external await has a comment
      that answers *why*.
- [ ] Every magic value is a named constant with a comment.
- [ ] No `print`, no `except Exception` outside the allowed three places, no bare `create_task`,
      no module-level side effects, no `dict[str, Any]` past the codec.
- [ ] Imports respect the layer table in section 4 (`lint-imports` passes).
- [ ] No vendor LLM SDK import outside `hivemind/llm/providers/`; no model id, provider URL, or
      `if provider.name == ...` branch anywhere in code; model access goes through a `ModelSlot`.
- [ ] Worker, role and tool code runs commands and touches files only through `CellSession`, and
      branches on `cell.capabilities`, never on `cell.kind`.
- [ ] Anything a Worker may do outside a Real Cell lease's scratch directory is capability-gated,
      on the trail, and undone by `release()`; the left-as-found test covers it.
- [ ] Nothing under an `autopilot/` directory imports `hivemind.llm`; every awake episode gets its
      prompt from `memory.assemble` and keeps no transcript afterwards.
- [ ] Any new issue a bee can raise is an `AlarmKind` with a row in the escalation policy table,
      and any new resource a bee can consume is accounted for in `ForageCapacity` and `ForageGrant`.
- [ ] Any new side-effecting tool or action declares a risk tier, accepts a postcondition, and
      goes through the Capping gate; any new state machine has a row in Appendix C.
- [ ] Any new row in a memory table, Nectar or Honey carries a `HoneyClearance`; any new event
      kind says whether it survives a Night Veil teardown.
- [ ] Any new Entrance route is versioned, appears in the committed OpenAPI document, is
      capability-scoped, and is loopback-only if it approves, widens, unlocks or reopens anything.
- [ ] New Protocol implementation passes the contract suite; new state transitions are tested.
- [ ] No secrets, screenshots, page bodies or tokens in logs or the Pheromone Trail.
- [ ] Night Veil runs left nothing beyond the lifecycle skeleton of section 12.
- [ ] `ruff format`, `ruff check`, `mypy --strict`, `pytest -m "not integration"` all pass locally;
      for the front end, `pnpm lint`, `tsc --noEmit` and `vitest` pass.
- [ ] Commit message is `type(scope): summary` and the body says why.
- [ ] `.claude/roadmap.md` step is checked off or amended.

---

## Appendix C: State machines and where state lives

One table for every state machine in the Hive. Each has exactly one transition table in the file
named, tested edge by edge, and every transition is a Pheromone Trail event written in the same
transaction as the state change.

| Machine | Owner and file | States and transitions | Notes |
|---|---|---|---|
| Task | Brood Chamber, `brood_chamber/task_state.py` | `PENDING → ASSIGNED → RUNNING → SUCCEEDED / FAILED / CANCELLED`; `RUNNING ↔ BLOCKED` (question); `RUNNING ↔ PAUSED` (Clustering); `ASSIGNED → PENDING` (Warden lost) | `SUCCEEDED` only after acceptance checks pass, run by the Warden. |
| Question | Brood Chamber, `brood_chamber/questions.py` | `ASKED → ANSWERED / WITHDRAWN` | Asking blocks the task; answering resumes it. |
| Proposal | Capping, `supervision/capping/state.py` | `PROPOSED → CHECKING → CAPPED → APPLIED → VERIFIED`; `CHECKING → REJECTED`; `APPLIED → ROLLED_BACK` | Tier decides the checks between `CHECKING` and `CAPPED`. |
| Alarm | Supervision, `supervision/alarm.py` | `RAISED → HANDLING → RESOLVED`; `HANDLING → ESCALATED → HANDLING` (at the next level) | Attempt count travels with it; same id at every level. |
| Worker | Warden, `workers/state.py` | `SPAWNED → RUNNING → DONE / FAILED / KILLED`; `RUNNING ↔ HANDING_OFF` (reset, rebind, migrate); `RUNNING ↔ PAUSED` | Runtime state is in-process; a Handoff is the durable form. |
| Warden | Queen, `wardens/state.py` | `STARTING → ACTIVE`; `ACTIVE ↔ WATCH` (Real Cells, no active bees); `ACTIVE / WATCH → OFFLINE → ACTIVE`; any → `CLUSTERED → ACTIVE`; `ACTIVE → MIGRATING → ACTIVE` (new host); → `STOPPED` | `WATCH` exists only for Real Cells; Virtual Cell Wardens stop with their Cell. |
| Virtual Cell | Hive, `hive/cell_state.py` | `PROVISIONING → READY → BUSY ↔ IDLE → DORMANT → READY`; `IDLE / DORMANT → TEARING_DOWN → DESTROYED`; `PROVISIONING → DESTROYED` (failure) | Backend labels are the source of truth; the table is reconciled against them. |
| Lease (Real Cell) | Cell, `cell/lease_state.py` | `REQUESTED → OPEN → RELEASING → RELEASED`; `OPEN → ORPHANED → RELEASING` (sweep) | Every open lease has a scratch root and a process list. |
| Swarm node | Swarm, `swarm/node_state.py` | `INVITED → ENROLLING → ONLINE ↔ UNREACHABLE`; `ONLINE → PROMOTING → NUC`; `NUC → DEMOTING → ONLINE`; any → `REVOKED` | Access level is an attribute, not a state. |
| Forage grant | Queen, `forage/grant_state.py` | `ISSUED → ACTIVE → REVOKED`; `ACTIVE → EXHAUSTED → ACTIVE` (top-up) | Growing or shrinking keeps it `ACTIVE`; each change is an event. |
| Tool | Royal Jelly, `royal_jelly/tool_state.py` | `REQUESTED → SCAFFOLDED → QUARANTINING → PROMOTED / REJECTED`; `PROMOTED → RETIRED` | Scope (`hive` or `cell`) is an attribute set at promotion. |
| Provider health | LLM, `llm/health.py` | `HEALTHY ↔ DEGRADED ↔ DOWN` | In memory, re-probed on start; `DOWN` with no fallback triggers Clustering. |
| Queen mode | Queen, `queen/state.py` | `REQUEENING → RUNNING`; `RUNNING ↔ CLUSTERED` (per provider set); `CLUSTERED → SUPERSEDING → SUPERSEDED` (the old Queen, 8.16); `SUPERSEDING → CLUSTERED` (rollback) | Per event, `RUNNING` is autopilot then awake; mode is not a transcript. A new Queen starts in `REQUEENING` from the copied stores. |
| Knowledge tier | Memory and Honey Store | `HOT → BEE_BREAD → HONEY`; `NECTAR → HONEY` | A pipeline, not a strict machine; demotion is a House Bee duty. |
| Cell Wax note | Memory, `memory/cell_wax.py` | `PROPOSED → WRITTEN → CLEARED / EXPIRED`; `PROPOSED → REJECTED` | Only the Queen writes, rejects or clears; every edge is a `memory.wax_*` event; cleared and expired notes are ripened into Honey at `cell:<id>` scope. |
| Pheromone Mask | Supervision, `supervision/mask.py` | `OFF → WARDEN / QUEEN_FORCED → OFF` (expiry or explicit clear); `WARDEN → QUEEN_FORCED` (the Queen's override wins) | Per Cell; every edge carries reason and expiry; shown as a badge in the UI. |
| Enrolled device | Entrance, `entrance/enrol/state.py` | `INVITED → PENDING → APPROVED`; `PENDING → DENIED / EXPIRED`; `APPROVED ↔ LOCKED` (lockout, loopback unlock); `APPROVED / LOCKED → REVOKED` | Approval, unlock and revocation are loopback-only edges; every edge is a `guard.entrance.*` event pushed to every other device. |
| Entrance mode | Entrance, `entrance/reducer.py` | `OPEN → REDUCED → OPEN` | `REDUCED` keeps only the loopback listener; reopening is loopback-only with step-up. |

Where state lives, and what survives a Queen crash:

| State | Store | Survives | Recovery |
|---|---|---|---|
| Tasks, questions, acceptance results | Brood Chamber (SQLite) | Yes | Requeening reads it back. |
| Every transition, every decision | Pheromone Trail (SQLite, per-node segments) | Yes; a Night Veil Cell keeps only the lifecycle skeleton from section 12 | Source of truth for reconciliation and audit; offline segments merge. |
| Hot state | Nowhere; derived per episode | Not applicable | Rebuilt by `memory.assemble` from the stores below. |
| Notes, pins, Cell Wax, episode records, Handoffs, Bee Bread index, watch observations | Memory tables (SQLite) | Yes | Read directly; retention windows apply; wax expires on its own clock. |
| Honey and Nectar | Honey Store (SQLite, FTS5, `sqlite-vec`) | Yes | Read directly; re-embed on embedder change. |
| Capacity, grants, hosting decisions, snapshots | Forage ledger (SQLite) | Yes | Reconciled against fresh capacity reports on Requeening. |
| Leases | Lease table plus trail | Yes | Orphan sweep on Queen and Warden start. |
| Virtual Cell status | Backend labels plus a Hive table | Backend is truth | Reconciled from the backend on start. |
| Swarm nodes, access levels | Swarm registry (SQLite) | Yes | Heartbeats re-establish reachability. |
| Tools and reports | Comb Registry (SQLite plus package dir) | Yes | Read directly. |
| Proposals and verdicts | Capping table plus trail | Yes | In-flight proposals are re-checked, never auto-applied, after a restart. |
| Provider health | In memory | No | Re-probed on start. |
| A bee's in-flight reasoning | Its process, and its last Handoff | Via the Handoff | Resume from the Handoff on the same or another slot or host. |
| Hive identity and the Queen's address | Secret store (keypair); manifest `[hive_stand] address`; every device's enrolment record | Yes | Supersedure (8.16) rewrites the address on every node with a signed `QueenMoved`. |
| Operator credential, enrolled devices, sessions, push subscriptions | Entrance tables (SQLite) | Yes | Password hash and device public keys only; sessions are re-validated against device state on start; move with the stores on Supersedure. |

Five rules follow from the tables:

1. A process holds no state that a store does not, except in-flight work, and in-flight work is
   covered by a Handoff at every threshold and on every intervention.
2. One transition table per machine, exhaustively tested; forbidden edges raise.
3. Every transition is a trail event in the same transaction as the state change, except on a
   Night Veil Cell, where only the lifecycle skeleton from section 12 survives teardown.
4. Derived views (hot state, Attendant ordering, Forage headroom, Honey folders) are never
   stored; they are rebuilt from the tables above.
5. Offline nodes write to their own segments and outboxes; merging on reconnection is
   idempotent and ordered by timestamp and node id.
