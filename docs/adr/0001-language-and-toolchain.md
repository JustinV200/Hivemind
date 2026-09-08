# ADR-0001: Language and toolchain

- Status: Accepted
- Date: 2026-09-07

## Context

HiveMind (the project's own name for itself) is developed on a Windows 11 Home machine with no
Hyper-V available, and must also run on Ubuntu LTS and Arch Linux as first-class hosts, with macOS
as best-effort; nothing in the toolchain may assume a host it cannot run on. The Queen (the central
orchestrator), every Warden (a per-Cell supervisor) and every Worker (a bee that does the actual
work) are long-running, I/O-bound, `asyncio`-native processes, so the core language needs mature
structured concurrency and a mature async ecosystem rather than requiring one to be built. Pollen
(the thin gateway installed on an enrolled device, which that device's Warden drives over a signed
terminal session) has to install on constrained hardware, such as a Raspberry Pi, with no compiled
toolchain assumptions and no vendor LLM SDK, which argues for a single language across the whole
backend so Pollen can share code with the Queen instead of re-implementing protocol logic in a
second language. The project also commits to being provider-neutral among LLM vendors from day one
(codingrules section 2 and section 4), which is a toolchain-and-architecture concern more than a
language concern, but it shapes how strictly typed the core needs to be so that provider adapters
cannot leak vendor-specific shapes upward. Finally, the Observation Hive (the read-only dashboard
that shows what the Hive is doing) must be reachable from any enrolled device, including a phone
running Android, which means the front end has to work as an installable web app and package into a
native shell without a second rewrite.

## Decision

HiveMind's backend (the three packages: waggle, hivemind and pollen) is written in Python 3.12 or
later, managed as a single `uv` workspace with one lockfile, formatted and linted with `ruff`, and
type-checked with `mypy --strict` with zero errors tolerated on `main`. Import boundaries between
packages and between the layers inside `hivemind` are enforced mechanically by `import-linter`
contracts declared in the root `pyproject.toml`, not by convention alone. The front end, the
Observation Hive, is TypeScript in strict mode with React, built by Vite, linted by `eslint` and
formatted by `prettier`, type-checked with `tsc --noEmit`, and tested with `vitest`; it is packaged
for Android with Capacitor so the same web app becomes an installable APK with native push and a
platform credential manager, rather than a second, separately maintained mobile client.

## Consequences

Positive: `asyncio` plus `uv` plus `ruff` plus `mypy --strict` gives the backend one toolchain for
formatting, linting, dependency resolution and type-checking, so CI has few moving parts and every
contributor runs the same commands. Python's standard library and its packaging ecosystem run
unmodified on Windows, Ubuntu and Arch, which matches the supported-hosts assumption directly.
Sharing Python between the Queen and Pollen means the Waggle protocol (the shared messaging
protocol and its primitives) is one library, not a reimplementation kept in sync by hand. Strict
mypy catches an entire class of "the provider adapter leaked a vendor type upward" bugs at review
time rather than at runtime. TypeScript plus React plus Capacitor gives one Observation Hive
codebase for the web PWA and the Android app, so a new view or fix ships to both at once.

Negative: Python's runtime performance ceiling is lower than a compiled language's, which matters
if a future phase needs to process high-throughput telemetry or run CPU-bound work directly in a
bee's process; that work is expected to be delegated to a Cell's own tools or to an in-process
model library instead of tightened Python. `mypy --strict` and `ruff` on the full rule set add real
friction to fast prototyping, which is an intentional trade against long-term maintainability.
Capacitor introduces a native Android build pipeline (Gradle, an SDK, a signing story) as a second
toolchain surface alongside the pure-web one, and that surface needs its own maintenance even
though it reuses the React source.

## Alternatives considered

Rust for the core: better raw performance and a stronger compile-time safety net, but a much
smaller async-ecosystem overlap with what a lightweight device connector needs, and it would have
forced Pollen into a cross-compilation story for constrained devices rather than `pip install`.

Go for the core: simple deployment as a single static binary and good concurrency primitives, but
its type system is a poor fit for the deeply nested provider, message and manifest models this
project already commits to expressing as pydantic models, and its generics story is younger.

TypeScript for the core (a single-language full stack): would have unified backend and front end,
but Python's ecosystem for async networking, cryptography, SQLite and in-process ML libraries
(`faster_whisper`, `sentence_transformers`) is deeper, and none of the target hosts need Node for
anything but the front end.

`poetry` for dependency management: solid workspace support but a slower resolver and a project
file format that mixes build-system and dependency concerns; `uv` resolves and installs faster and
was chosen specifically for its workspace model.

`pip-tools` for dependency management: works, but it is a pair of separate compile and sync steps
layered on top of plain `pip`, with no native workspace concept, so a multi-package repo like this
one would need extra scripting `uv` already provides.

`pdm` for dependency management: has workspace support and a PEP 621 project file, but a smaller
community and slower iteration than `uv` at the time of this decision, with no advantage large
enough to offset that.

`black` plus `flake8` plus `isort` for formatting and linting: three separate tools with three
configuration files and three sets of plugins to keep compatible, where `ruff` reimplements all
three roles in one fast binary with one configuration block.

`pyright` for type checking: fast and IDE-friendly, but `mypy --strict` was chosen for its more
mature plugin ecosystem (notably for pydantic) and because it is the checker most third-party
stubs are validated against first.

Svelte for the front end: a smaller runtime and less boilerplate, but a smaller component and
tooling ecosystem than React, and a worse fit with Capacitor's official guidance and examples,
which target React and Vue first.

Vue for the front end: comparable ergonomics to React, but the project standardizes on generated
TypeScript types from the committed OpenAPI document, and React's ecosystem for that generation
and consumption pattern is the more travelled path.

HTMX for the front end: a good fit for server-rendered pages with light interactivity, but the
Observation Hive needs live streaming views (fleet status, thoughts, gauges) with rich client-side
state, which is React's home ground and HTMX's weak point.
