# HiveMind Roadmap

> The build order for the system described in [README.md](../README.md), broken into phases with
> concrete steps, exit criteria and the decisions each phase must record. This file supersedes the
> checklist in the README's "Roadmap" section.

**How to read this**

- A **phase** ends with something that runs. Every phase produces a vertical slice you can
  demonstrate from the `hive` CLI, not a pile of unconnected modules.
- **Steps** are ordered. Each step is small enough for one PR (see coding rules section 16) and ends
  with tests, docstrings and a roadmap checkbox ticked.
- **Exit criteria** are the acceptance test for the phase. Do not start the next phase until they
  all hold; the phases build on each other.
- **ADRs to write** lists the hard-to-reverse decisions that phase forces. Write the ADR *before*
  the code that depends on it. Titles are listed without numbers; a number is assigned, next in
  sequence, when the ADR is written.
- File paths refer to the layout in [codingrules.md](codingrules.md) section 3.
- Every step obeys [codingrules.md](codingrules.md). Read it first.

**Stated assumptions** (change the roadmap if any of these change)

- Python 3.12+, `uv` workspace with three packages: `waggle`, `hivemind`, `pollen`.
- **The Queen is a kernel, not a chatbot** (coding rules 8.8). A thin always-on loop with an inbox
  the Attendant orders. Every event goes to autopilot, a deterministic dispatch table that never
  awaits a model, and only what autopilot cannot decide runs an awake episode. Awake episodes are
  stateless: the prompt is assembled from durable state each time (coding rules 8.9).
- **Every Cell has a Warden**, a per-Cell supervisor on the same loop shape. Wardens spawn sub-bees
  within a Forage grant, handle their Alarms by policy, and escalate what they cannot resolve. The
  chain is sub-bee → Warden → Queen → human, and the human is always last. Wardens never provision
  Cells; they request. The Queen never executes; she rebinds slots and can spawn a takeover bee.
- **Cells are Real or Virtual, terminal first** (coding rules 8.7). The machine the Queen runs on
  is the **Hive Stand**: the first Real Cell, and the default home of every Warden. A Virtual Cell
  is a VM or container the Hive provisions and destroys. Both offer a shell session; the
  Exoskeleton is attached only when a task needs it. Phase 3's first vertical slice runs a Warden
  and Drones on the Hive Stand with no infrastructure provisioned.
- **Forage is capacity as data** (coding rules 8.10): every Cell reports what it can bear. The
  Queen divides the **shared pool** (Hive Stand seats, hosted seats, spend) by grant; each Warden
  divides the **local pool** on its own Cell under ceilings the Queen set once, and never asks for
  what is already on its machine. Where a Cell's models come from is a per-slot **hosting plan**
  with fallback chains, local first where local exists. A Real Cell with its own Warden and model
  server is a **Nuc** and keeps working when disconnected.
- **What runs on a device climbs a ladder** (coding rules 8.7): Level 0, gateway only; Level 1,
  the Warden and its sub-bees on the device, at which point the Cell is **colonized**; Level 2, a
  model server as well, which makes a colonized Cell a Nuc. A Warden and its sub-bees are always
  co-located, and the Warden is the first bee to move onto a device. Every supervisor, Queen or
  Warden, has an Attendant ordering its inbox.
- **Cells carry a Comb Shield level** in addition to access level: **Meadow (Tier 0)** for broad
  compatibility, **Propolis (Tier 1)** as an OpenVPN-only hardened baseline (no Tor), and
  **Night Veil (Tier 2)** as a virtual-only profile where all web traffic is forced through
  OpenVPN + Tor, direct egress is blocked, all model slots are local-only, and the Cell's Waggle
  link to the Hive Stand goes over Tor to a hidden-service endpoint rather than a clearnet or
  direct route, so no fixed Hive Stand address ever appears on the wire.
- **Tier defaults and escalation.** New Cells default to Meadow. The Queen may escalate to
  Propolis by policy when needed. Night Veil may be used only on explicit human request.
- **Tier is per Cell, not per task.** A task inherits the controls of the Cell it runs on;
  execution on a Night Veil Cell is Night Veil work by definition.
- **Honey and Nectar carry clearance labels**: **Wildflower (C0)** public/non-sensitive,
  **Apiary (C1)** internal non-personal, **Royal (C2)** personal/sensitive. Any user personal detail
  at all, including first name or habits, is Royal (C2). Night Veil Cells may access C0 and C1 but
  cannot read or write C2.
- **Supported hosts are Windows 11, Ubuntu LTS and Arch Linux.** The Hive Stand and the whole
  framework run on all three; CI covers Windows and Ubuntu natively and Arch in a container.
  macOS is best-effort. **Virtual Cell images are Ubuntu LTS (24.04)**: reliable, open, widely
  documented, with cloud images and cloud-init for the QEMU path and first-class support for the
  Exoskeleton's packages.
- Development host is Windows 11 Home. Hyper-V is not available on Home edition, so the first
  Virtual Cell backend is **Docker Desktop (WSL2)**; the first real-VM backend is **QEMU** because
  it is portable across Windows, macOS and Linux. Cloud backends come after both.
- **Every task has a tempo** (coding rules 8.14): a latency budget and an accuracy bar. The
  Attendant, slot routing, Forage allocation and the Capping ladder all read it, within safety
  floors that tempo can never lower.
- **Waggle is for bees, not for models.** Bees talk to each other over Waggle regardless of
  where their models run; model calls go over HTTP to whichever provider serves the slot. Same
  process: memory transport. Different process or machine: WebSocket.
- **The LLM layer is provider-agnostic from day one** (coding rules 8.6). The first adapter is
  Anthropic Claude (example slots: `claude-opus-5` for the Queen, `claude-sonnet-5` for Workers).
  The second adapter, built in the same phase, speaks the OpenAI-compatible HTTP API that locally
  hosted servers expose (Ollama, vLLM, llama.cpp server, LM Studio). Phase 8 is where the whole
  Hive is proven to run offline on local models. **Whisper transcription is in Brood 1.0** as
  the `TRANSCRIBER` slot: a `TranscriptionProvider` protocol with an in-process faster-whisper
  adapter, an OpenAI-compatible audio-endpoint adapter and a fake, serving both the human's voice
  at the Hive Entrance and a Worker's ears through Buzz.
- The Exoskeleton's native peripherals target **Linux** Cells. On a Windows or macOS Real Cell,
  including the Hive Stand during development, the browser fast path (Playwright) is the
  Exoskeleton for Brood 1.0.
- **Shared primitives live in `waggle`.** Ids, the clock and the loop shape are needed by
  envelopes and by `pollen`, which may import nothing from `hivemind`, so they live in `waggle`
  and `hivemind.common` keeps only errors, results, logging and migrations.
- **`llm` imports `forage`, never the reverse.** `ModelSlot` and `Tempo` live in `forage` so
  grants, routing inputs and autopilot rules can name a slot or read a tempo without importing
  `hivemind.llm`. `TaskNeeds` and the three security enums live in `cell`; `guard` imports them,
  never the other way round.
- **A Real Cell's Comb Shield tier is set by the operator at enrolment**, like its access level.
  HiveMind verifies a Propolis device's own VPN before placing work and never installs, starts or
  reroutes one. The Queen chooses tier only for Virtual Cells.
- **Clearance labels ride on every memory tier**, not only Honey, assigned from provenance at
  intake; a model may raise a label and only a judge or a human may lower one.
- **The Hive has one door and one keeper** (coding rules 8.15). Brood 1.0 is single-operator. A
  phone, laptop, program or the Observation Hive itself is a device enrolled with an invite minted
  on the Hive Stand and approved only on the Hive Stand's loopback listener; it logs in with its
  own key plus the operator's password, sessions are bound to the device, sensitive actions need
  step-up, and anything that needs the human is pushed to whichever device they are on. Remote
  access is over a VPN overlay by default; there is no public mode.
- **The Observation Hive is TypeScript + React** (`packages/observation-web/`), served by the
  Entrance as static files, installable as a PWA, and built for **Android** with Capacitor so a
  phone is a first-class entrance point with native push and passkeys. Glasses reach the Hive
  through their companion phone.
- **The Hive Stand is a role** (coding rules 8.16). Supersedure moves the Queen and her stores to
  another colonized Real Cell; the old machine stays in the Swarm as an ordinary Cell.

---

## Phase overview

| # | Phase | What runs at the end | Depends on |
|---|---|---|---|
| 0 | Foundation | `hive --version`, green CI, empty-but-structured workspace | nothing |
| 1 | Waggle protocol | Two processes exchange typed, versioned, signed messages over WebSocket | 0 |
| 2 | Brood Chamber + Pheromone Trail | Tasks persist in SQLite; mutations leave audit events with Night Veil retention exceptions | 0 |
| 3 | Queen kernel + Warden + Drone on the Hive Stand | `hive run "goal"`: the Queen decomposes, the Hive Stand's Warden spawns Drones, Alarms escalate, a question blocks until answered, the host is left as found; on Claude or a local model | 1, 2 |
| 4 | Memory, Forage, Clustering | Hot state stays inside its budget under load; handoffs resume; grants and requests flow; a provider outage pauses and preserves, then resumes | 3 |
| 5 | The Hive (Virtual Cells) + placement | The Queen chooses Real or Virtual per task; each Virtual Cell gets a Warden; Undertakers destroy or release; Overwintering pool (never for Night Veil) | 3, 4 |
| 6 | Exoskeleton | A Forager sees a screen, clicks, types, hears audio, on either Cell kind, only when asked; Pheromone Mask behavior is callable tactics, not a persistent mode | 5 |
| 7 | Honey Store | Nectar ripens into Honey with clearance labels; the cold tier of memory is live; Workers query it before acting | 3, 4 |
| 8 | Local models + provider routing | Every slot can run local; the Hive runs fully offline; routing weighs Forage and model location | 4, 7 |
| 9 | Royal Jelly Lab | A Worker or Warden requests a tool; it is scaffolded, quarantined, promoted at hive or cell scope, used | 5, 7 |
| 10 | Guard Bees + Hive Entrance | Every dispatch, lease and grant is authorised, including tactic invocation and clearance enforcement; the Hive has a versioned API (the Landing Board) that only devices enrolled at the Hive Stand can use, two-factor login bound to the device, push for questions and Alarms, a human inbox, and remote exposure over a VPN | 3 |
| 11 | The Swarm (Pollen Packet, Nucs) | A device enrols through a gateway, gets a Warden on the Hive Stand, can be promoted to a Nuc, enforces Meadow/Propolis on Real Cells, and keeps working when cut off | 1, 8, 10 |
| 12 | Observation Hive | One live UI in TypeScript + React, on any enrolled device including an Android app: the Queen's and any bee's thoughts, Cell diagrams, the Forage split, the fleet list with model hosting, Attendant views for the Queen and every Warden, a chatbox, device enrolment screens, and a browsable Honey tree | 2, 7, 10 |
| 13 | Resilience | Requeening restores a crashed Queen and her leases and grants; Supersedure moves the Hive Stand to another machine with no task lost; Swarming and Absconding work under load | 5, 10, 11 |
| 14 | Brood 1.0 | Packaged release with docs, runbooks, Getting Started | all |

Phases 6, 7 and 10 can proceed in parallel once phase 5 lands. Phase 8 needs 7 because embeddings
are the first workload that must go local. Phase 9 needs 7 because promoted tools are recorded as
Honey. Phase 11 needs 8 because a Nuc hosts its own model, and 10 because a lease on someone
else's device must be authorised.

---

## Phase 0: Foundation

**Goal.** A repository where the structure, tooling and quality gates exist before any domain code,
so every later phase lands into an enforced shape.

**Depends on.** Nothing.

**Deliverables.** Workspace skeleton, CI, `common` package, `hive --version`, first three ADRs.

### Steps

- [x] **0.1 Workspace scaffold.** Root `pyproject.toml` declaring the `uv` workspace with members
  `packages/waggle`, `packages/hivemind`, `packages/pollen`. Each member has a `pyproject.toml`,
  `src/<name>/__init__.py` with the subsystem docstring, and an empty `tests/`. Create every
  directory from the layout in codingrules section 3 with a one-paragraph `README.md` in each.
  `packages/observation-web/` is scaffolded too: `pnpm`, Vite, React, TypeScript strict,
  `eslint` + `prettier`, `vitest`, a placeholder `app.tsx` that renders the Hive name, and a
  `README.md` saying it is built in phase 12 and served by the Entrance.
- [x] **0.2 Toolchain config.** In the root `pyproject.toml`: `ruff` (format + lint, line length
  100, rule sets `E,F,I,N,D,UP,B,C4,C90,SIM,ANN,ASYNC,S,T20,RUF`, `pydocstyle` Google convention),
  `mypy --strict`, `pytest` (asyncio mode auto, markers `integration`, `e2e`, `slow`, `live_llm`,
  `local_llm`), `pytest-cov` with the floors from codingrules 14.1, and `import-linter` contracts:
  the layer table from codingrules section 4, vendor LLM packages importable only from
  `hivemind.llm.providers.*`, `subprocess` importable only from `hivemind.cell.*`,
  `hivemind.hive.backends.*`, the dev sandbox and `pollen.*`, **no `hivemind.llm` import from
  any `autopilot` package**, and **no `hivemind.llm` import from `hivemind.forage`**. Add
  `.pre-commit-config.yaml`.
- [x] **0.3 Hygiene checkers.** `scripts/check_sizes.py` (file ≤ 300 lines, function ≤ 50, via
  `ast`, covering `.py`, `.ts` and `.tsx`), `scripts/check_no_model_ids.py` (no model id or
  provider URL outside `manifest/` and `docs/`; skips comments and docstrings, otherwise the
  OpenAI-compatible adapter's own docstring naming llama.cpp fails it),
  `scripts/check_no_kind_branches.py` (no `cell.kind ==` outside placement and the Undertaker),
  `scripts/check_no_transcripts.py` (no module-level or instance attribute named
  `messages`/`history` that grows across awake episodes outside `memory/`; allowlist
  `llm/models.py`, where `LLMRequest.messages` is one request, `llm/tools.py`, where the tool loop
  accumulates within one episode by design, and `waggle/messages/`, a package name). Wire all
  into pre-commit and CI. Heavily commented; these are the first files people will read.
- [x] **0.4 CI.** `.github/workflows/ci.yml`: matrix on Ubuntu + Windows, plus an Arch Linux
  job running in an `archlinux` container on the Ubuntu runner; jobs for `ruff`, `mypy`,
  `lint-imports`, the hygiene scripts, `pytest -m "not integration and not e2e and not live_llm
  and not local_llm"`, coverage upload, `pip-audit`; a `web` job on Ubuntu running `pnpm lint`,
  `tsc --noEmit`, `vitest` and `pnpm audit` for `packages/observation-web/`. A second workflow
  `integration.yml` runs Docker-dependent tests on Ubuntu only, on demand and nightly.
- [x] **0.5 Shared primitives: `waggle` and `hivemind.common`.** Everything `pollen` also needs
  lives in `waggle`, because `pollen` may import nothing from `hivemind` (coding rules 4). These
  are the first code in `packages/waggle`, ahead of phase 1, because phase 2 needs ids and the
  clock and may not wait on the protocol. Each in its own file:
  - `waggle/ids.py`: `NewType` ids (`HiveId`, `CellId`, `LeaseId`, `TaskId`, `WorkerId`,
    `WardenId`, `AlarmId`, `GrantId`, `ToolId`, `NodeId`, `EventId`, `DeviceId`) generated as
    ULIDs with a type prefix (`cell_01H...`) so an id is self-describing in logs. Envelopes
    carry them, which is why they cannot live in `hivemind`.
  - `waggle/clock.py`: `Clock` protocol + `SystemClock` + `FakeClock` (the fake lives here, not
    in tests, because `pollen` and `hive doctor` need it too).
  - `waggle/loop.py`: the standard long-running loop shape from codingrules section 11 as a
    small base class with `run()`, `stop()`, `_tick()`. The Queen, every Warden, every Worker and
    the Pollen gateway all subclass it.
  - `common/errors.py`: `HiveMindError` root and the base classes each subsystem will extend.
  - `common/logging.py`: `structlog` configuration function called only from composition roots,
    and `get_logger`.
  - `common/result.py`: a tiny `Ok/Err` union for the LLM boundary only.
- [x] **0.6 CLI skeleton.** `hivemind/cli/app.py` (composition root) and `cli/version.py`. `uv run
  hive --version` prints the version and the Python version. Registered as a script entry point.
- [x] **0.7 Docs skeleton.** `docs/adr/0000-adr-template.md`, `docs/adr/README.md` (how to write
  one), `docs/waggle/README.md` (placeholder), `docs/manifests/README.md`. Confirm the root
  `CLAUDE.md` points at `.claude/codingrules.md` and `.claude/roadmap.md`.
- [x] **0.8 First ADRs.** `language-and-toolchain.md` (Python/uv/ruff/mypy; TypeScript + React
  for the front end), `workspace-layout-and-layering.md` (three Python packages plus the web
  package, layer table, `llm` over `forage`), `ids-clock-and-loop-live-in-waggle.md` (prefixed
  ULIDs; why the shared primitives sit in the protocol package).

### Exit criteria

- `uv sync && uv run hive --version` works on Windows and Ubuntu.
- CI is green on `main` with all gates enabled, including the coverage floor (trivially met).
- `lint-imports` has at least one contract per layer boundary and they all pass.

---

## Phase 1: Waggle protocol

**Goal.** The single vocabulary every component speaks. Defined once, versioned, and usable by both
`hivemind` and the minimal-dependency `pollen` package.

**Depends on.** Phase 0.

**Deliverables.** `packages/waggle` with envelope, message catalogue, codec, two transports, a
conformance suite, and `docs/waggle/` as the human-readable spec.

### Steps

- [x] **1.1 Protocol spec first.** Write `docs/waggle/spec.md`: envelope fields (`id`,
  `correlation_id`, `sender`, `recipient`, `kind`, `version`, `sent_at`, `node_id`, `payload`,
  `signature`), the request/reply/event distinction, versioning rule (additive changes bump minor;
  breaking bump major; receivers reject unknown majors), and error message shape with stable
  `code` strings.
- [x] **1.2 Envelope and codec.** `waggle/envelope.py` (pydantic, frozen, `extra="forbid"`),
  `waggle/codec.py` (JSON serialise/deserialise, version check, size limit constant with comment).
  Round-trip and rejection tests, plus `hypothesis` property tests for the codec. Landed as:
  `wrap(payload, hop, *, clock, correlation_id=None)` with `Hop(sender, recipient, node_id)`;
  frame parsing in `waggle/codec.py`, the dialable-URI rule in `waggle/uris.py`; a verifier
  configured on the `Codec` means signatures are required, none means ignored (in-process).
- [x] **1.3 Message catalogue.** One file per family under `waggle/messages/`:
  - `task.py`: `TaskAssign`, `TaskProgress`, `TaskResult`, `TaskCancel`, `TaskPause`, `TaskResume`.
  - `supervision.py`: `Heartbeat` (carries `ContextTelemetry`), `AlarmRaised`, `AlarmResolved`,
    `Inspect`, `InspectReply`, `Intervene` (compact, checkpoint, handoff, rebind, takeover, cancel),
    `Question`, `Answer`.
  - `forage.py`: `CapacityReport`, `GrantIssued`, `GrantRevoked`, `ForageRequest`, `ForageReply`,
    `HostingDecided`, `CeilingsSet`, `PlanWritten`.
  - `cell.py`: `CellReady`, `CellHeartbeat`, `CellTeardownRequest`, `CellRequest` (Warden → Queen),
    `LeaseOpened`, `LeaseReleased`, `CellWaxProposed` (any bee → the Queen), `CellWaxWritten`,
    `CellWaxCleared` (the Queen → the Cell's Warden).
  - `session.py`: `SessionOpen`, `SessionExec`, `SessionStdin`, `SessionOutput`, `SessionExit`,
    `SessionPutFile`, `SessionGetFile`, `SessionClose`. The terminal-over-Waggle family that lets a
    Warden on the Hive Stand drive a remote Real Cell (phase 11).
  - `honey.py`: `NectarDeposit`, `HoneyQuery`, `HoneyResponse`.
  - `tool.py`: `ToolRequest`, `ToolPromoted`, `ToolInvoke`, `ToolResult`.
  - `capping.py`: `ProposalSubmitted`, `CheckResult`, `Verdict`, `PostconditionResult`,
    `RollbackDone`. Proposals cross from a Worker to its Warden, which runs the gate (phase 3.17).
  - `swarm.py`: `EnrolRequest`, `EnrolAccept`, `DeviceHeartbeat`, `NucPromote`, `NucPromoted`,
    `TrailSegmentSync`.
  - `control.py`: `Ping`, `Pong`, `Error`, `Shutdown`, `Cluster`, `Wake`, `HumanMessage`,
    `MaskOverride` (Queen → Warden, reason and expiry; phase 6.13), `QueenMoved` (a relocation
    notice signed with the Hive key: new address, effective at, grace until; phase 13.2a).
  Each message is a pydantic model with every field described. A `registry.py` maps `kind` string
  to model class and is the only place that list lives. Landed as: each family a package
  `messages/<family>/` split by responsibility into two to four modules (`task/assignment.py` +
  `task/reports.py`, and so on; the list is in `messages/README.md`) with an `__init__.py` as the
  family's face, because one file per family broke the size limit (codingrules section 3 now
  requires the package shape); the shared
  labels and reports in `labels.py` and `reports.py`; `Error` named `ErrorMessage` (codingrules
  section 10); the sixty-six rows in `messages/catalogue.py`, which `registry.py` indexes.
- [x] **1.4 Transport protocol.** `waggle/transport/base.py`: `Transport` protocol with
  `connect()`, `send(envelope)`, `receive() -> AsyncIterator[Envelope]`, `close()`. Document the
  delivery guarantee (at-most-once at this layer; retries belong to the caller) in the docstring.
- [x] **1.5 Memory transport.** `waggle/transport/memory.py`: in-process pair of queues for tests
  and for the Warden and Workers running in the Queen's process during phase 3.
- [x] **1.6 WebSocket transport.** `waggle/transport/websocket.py` using `websockets`. Server and
  client halves in separate files if either exceeds the size limit. Heartbeat, reconnect with
  backoff (constants with comments), and clean close.
- [x] **1.7 Signing.** `waggle/signing.py`: Ed25519 signature over the canonical envelope bytes
  (`cryptography` library). Optional for in-process transport, mandatory for anything that crosses
  a machine boundary; the codec verifies when a key is configured.
- [x] **1.8 Outbox for offline nodes.** `waggle/outbox/`: a durable, ordered queue of envelopes
  a node could not send, replayed on reconnection. Used by offline Wardens and by the Pollen
  gateway. Small, file-backed, property-tested for ordering and idempotent replay.
- [x] **1.9 Conformance suite.** `waggle/tests/contracts/test_transport_contract.py` parametrised
  over `MemoryTransport` and `WebSocketTransport`: ordering within a connection, close semantics,
  oversized message rejection, malformed frame rejection, outbox replay after a dropped link.
- [x] **1.10 Spec/code drift check.** A test loads `docs/waggle/spec.md`'s message table and asserts
  every listed `kind` exists in `registry.py` and vice versa.

### Exit criteria

- A demo script (`scripts/waggle_echo.py`) starts a WebSocket server in one process and a client in
  another; they exchange `Ping`/`Pong` and a signed `TaskAssign`, reject a tampered envelope, and
  replay an outbox after the server is restarted.
- `pollen` can depend on `waggle` with only `pydantic`, `websockets` and `cryptography` installed.

### ADRs to write

- `waggle-transport-websocket-json.md`.
- `waggle-envelope-signing-and-offline-outbox.md`.

---

## Phase 2: Brood Chamber and Pheromone Trail

**Goal.** Durable task state and an append-only audit trail, so the Queen can crash and come back,
and every action is accounted for, with a Night Veil exception where execution records are not
retained after teardown.

**Depends on.** Phase 0. (Independent of phase 1; can be built in parallel.)

**Deliverables.** `hivemind/pheromone`, `hivemind/brood_chamber`, SQLite migrations, the v0 data
in `cell/needs.py`, `cell/tiers.py` and `forage/tempo.py`, `hive tasks` and `hive trail` CLI
commands.

### Steps

- [x] **2.1 Pheromone event model.** `pheromone/events.py`: `PheromoneEvent` base (id, hive_id,
  node_id, at, actor, kind, subject id, payload) plus one subclass per event family: `cell.*`
  (including `leased`, `released`, `touched_outside_scratch`), `task.*`, `alarm.*` (`raised`,
  `handled`, `escalated`, `resolved`), `forage.*` (`capacity_reported`, `granted`, `requested`,
  `denied`, `hosting_decided`), `memory.*` (`checkpoint`, `handoff`, `reset`, `compacted`,
  `wax_proposed`, `wax_written`, `wax_rejected`, `wax_cleared`, `wax_expired`),
  `queen.*` (`placed`, `woke`, `clustered`, `resumed`), `warden.*` (`spawned`, `offline`,
  `reconnected`, `migrated`), `tool.*`, `swarm.*`, `llm.*` (`call` with normalised `Usage`, slot
  and provider; `rebound`; `fallback`). The `kind` string is the stable audit vocabulary; document
  it in the module docstring. No event ever carries prompt or completion text.
- [x] **2.2 Pheromone Trail store.** `pheromone/trail.py` (`PheromoneTrail` protocol: `record`,
  `query`, `export_segment`, `merge_segment`), `pheromone/sqlite.py` (append-only table, no
  UPDATE/DELETE statements exist in the file; segments keyed by node id), `pheromone/memory.py`
  (tests). Contract suite over both, including a merge of two segments with interleaved timestamps.
  Add an explicit retention path for Night Veil execution records: ephemeral segmenting and
  teardown-time purge. The purge covers the VPN gateway's and Tor daemons' own per-Cell connection
  and circuit logs as well as the Trail, so the no-retention guarantee has no side channel.
- [x] **2.3 Migrations.** `hivemind/common/migrations.py`: applies numbered `.sql` files from a
  package directory, records applied versions. Used by every SQLite-backed subsystem. First
  migration creates the pheromone table.
- [x] **2.3a Cell v0 and Tempo.** `forage/tempo.py` (`Tempo`: optional latency budget in seconds
  and an accuracy bar `LOW | NORMAL | HIGH | CRITICAL`), `cell/tiers.py` (`AccessLevel`,
  `CombShieldLevel`, `HoneyClearance`: the three security dimensions, what a Cell may do, how it
  must do it, what data it may touch, each with its plain meaning in the docstring) and
  `cell/needs.py` (`TaskNeeds`: isolation `required | preferred | none`, exoskeleton, os, network
  scopes, disposability, `comb_shield`, `tempo`). Built here, ahead of the rest of `cell/` and
  `forage/`, because `TaskSpec` carries them; they are pure data with no other dependencies, and
  they sit low enough that `forage` and `llm` can read `Tempo` without importing `cell`.
- [x] **2.4 Task model and state machine.** `brood_chamber/task.py` (`Task`, `TaskSpec` including
  `TaskNeeds` from 2.3a, `TaskOutcome`), `brood_chamber/task_state.py` (`TaskStatus`
  enum: `PENDING`, `ASSIGNED`, `RUNNING`, `BLOCKED` (waiting on an `Answer`), `PAUSED`
  (Clustering), `SUCCEEDED`, `FAILED`, `CANCELLED`, plus the single transition table with a comment
  per edge). A test walks every edge and asserts every non-edge raises.
- [x] **2.5 Task graph.** `brood_chamber/graph.py`: pure functions over a set of tasks with
  `depends_on` edges: `ready_tasks()`, `is_acyclic()`, `descendants()`. Property-tested.
- [x] **2.6 Task store.** `brood_chamber/store.py` (`TaskStore` protocol), `brood_chamber/sqlite.py`,
  `brood_chamber/memory.py`. Every mutation records a `task.*` Pheromone event **in the same
  transaction** so the trail can never disagree with the store. Contract suite over both.
- [x] **2.7 Questions.** `brood_chamber/questions.py`: `Question` (id, task, asked by, text,
  options, asked at) and `Answer`. Asking moves the task to `BLOCKED`; answering moves it back to
  `RUNNING`. Stored with the task; the Queen's inbox reads pending questions from here.
- [x] **2.8 BroodChamber facade.** `brood_chamber/chamber.py`: the public API the Queen uses
  (`submit`, `assign`, `report_progress`, `ask`, `answer`, `pause`, `resume`, `complete`, `fail`,
  `cancel`, `next_ready`). Thin: it calls the state machine, then the store.
- [x] **2.9 CLI.** `cli/tasks.py` (`hive tasks list|show`) and `cli/trail.py` (`hive trail tail
  --follow`, `hive trail merge <segment>`). Output formatting only; no logic.

### Exit criteria

- Submit a small task graph from a JSON file via the CLI, advance it through states with a test
  driver including `BLOCKED` and `PAUSED`, restart the process, and see identical state and a
  complete trail.
- Two trail segments from different node ids merge into one ordered log with no duplicates.

### ADRs to write

- `sqlite-as-the-single-hive-store.md` (one file per Hive; what forces a move to Postgres).
- `pheromone-trail-append-only-transactional-and-segmented.md`.

---

## Phase 3: Queen kernel, Warden and Drone on the Hive Stand (first vertical slice)

**Goal.** The first end-to-end run through the real shape of the system: a goal goes into the
Queen's inbox, autopilot and awake mode decompose and place it, the Hive Stand's Warden spawns
Drones within a grant, an Alarm climbs the chain and is resolved by a rebind, a question blocks a
task until the human answers, and the host is left exactly as found. No infrastructure is
provisioned. The LLM layer is built provider-agnostic here, with two adapters.

**Depends on.** Phases 1 and 2.

**Deliverables.** `hivemind/manifest`, `hivemind/llm` (protocols, slots, ladders, two adapters),
`hivemind/cell` (abstraction + Hive Stand), `hivemind/forage` (v0), `hivemind/supervision`,
`hivemind/memory` (v0), `hivemind/workers` (runtime, Drone), `hivemind/wardens`, `hivemind/queen`
(kernel), `hive run`, `hive llm`, `hive cells`, `hive inbox`, `hive wardens`.

### Steps

- [ ] **3.1 Hive Manifest.** `manifest/schema.py` (pydantic `HiveManifest` with sections `[hive]`,
  `[queen]`, `[hive_stand]` (`enabled`, `scratch_root`, `address`, `capacity` overrides), `[llm]`,
  `[llm.providers.*]`, `[llm.slots]`, `[forage]` with `[forage.roles.<role>]` footprints (cpu,
  memory, seats per bee, estimated token rate, Exoskeleton extra), `[forage.map.<model-id>]`
  (grade 1 to 5, cost per token or per seat, context window, capability flags), and
  `[forage.reserve]` (the Royal Reserve: seats and memory held back for the Queen, the Attendant
  and the House Bees, plus a headroom margin), `[supervision]`
  (escalation policy file path, heartbeat interval, offline limits), `[memory]` (budget fraction,
  handoff threshold, per-item cap, Cell Wax cap per Cell and default expiry), `[security]`
  (`default_comb_shield`, per-tier egress profiles,
  OpenVPN profile settings, Tor proxy settings, and per-tier route and DNS leak checks where
  Propolis validates VPN-only egress and Night Veil validates VPN plus Tor egress for task traffic
  and VPN-only binding for its Waggle control channel), `[honey.clearance]`
  (default labels, allowed flows, per-tier read and write matrix), `[brood_chamber]`, `[pheromone]`, and per-phase sections added
  later), `manifest/loader.py` (TOML → model), `manifest/env.py` (the one place `HIVEMIND_*` env
  vars are read; provider secrets are `HIVEMIND_<PROVIDER>_API_KEY`). `docs/manifests/minimal.toml`
  (Claude only) and `docs/manifests/local.toml` (local only) are both loaded in tests. `[security]`
  and `[honey.clearance]` are declared here as schema with defaults, so the example manifests never
  drift; they are enforced in phases 5, 7 and 10. `[entrance]` is added in phase 10.
- [ ] **3.2 LLM boundary models.** `llm/models.py`: `LLMRequest` (system, messages, tools,
  response schema, slot, max output), `Message` with `ContentPart` union (text, image, tool call,
  tool result), `ToolDefinition` (name, description, JSON schema), `ToolCall`, `LLMResponse`
  (parts, stop reason enum, `Usage`), `Usage` (input, output, cached, cost). These are HiveMind's
  own types; no SDK type appears here. Round-trip tests.
- [ ] **3.3 Provider protocol and capabilities.** `llm/provider.py`: `LLMProvider` with
  `capabilities -> ProviderCapabilities`, `complete(request) -> LLMResponse`,
  `stream(request) -> AsyncIterator[LLMChunk]`, `count_tokens(request) -> int | None`, and
  `health() -> ProviderHealth`. `llm/capabilities.py`: the frozen `ProviderCapabilities` (native
  tool calls, schema-enforced output, JSON mode, vision, streaming, reasoning control, context
  window, system role, parallel tool calls). `llm/errors.py`: `LLMError` tree (`RateLimited`,
  `ProviderUnavailable`, `ContextTooLong`, `Refused`, `MalformedOutput`). `llm/fake.py`:
  `FakeLLMProvider` with scripted responses, a configurable capability set, and a switch to
  simulate an outage.
- [ ] **3.4 Model slots and registry.** `forage/slots.py`: the `ModelSlot` enum (`QUEEN`,
  `ATTENDANT`, `WARDEN`, `WORKER`, `RIPENER`, `SCAFFOLDER`, `EMBEDDER`, `JUDGE`, `TRANSCRIBER`),
  placed in `forage` so autopilot rules and grants can name a slot without importing
  `hivemind.llm`.
  `llm/slots.py`: `resolve(slot, manifest) -> BoundModel` (provider + model id + fallback chain
  + cost); a fallback may name a slot or a named binding that exists only in `[llm.slots]`. `llm/registry.py`: constructs
  providers from `[llm.providers.*]` by `kind`, enforces `offline = true` by refusing non-loopback
  base URLs, and hands out `BoundModel`s. The composition root is the only caller.
- [ ] **3.5 Degradation ladders.** `llm/structured.py`: `complete_structured(bound, request,
  schema) -> T` walking native schema output → JSON mode plus pydantic validation and retry →
  prompted JSON with fenced extraction, validation and retry; retry counts are commented constants.
  `llm/tools.py`: `run_tool_loop(bound, request, tools, executor)` using native tool calls when
  supported, else a prompted tool protocol we parse and validate. Both handle `ContextTooLong` by
  signalling the caller to shrink the budget. Tested at every capability level with the fake.
- [ ] **3.6 Anthropic adapter.** `llm/providers/anthropic/{provider,mapping,client}.py` using the
  `anthropic` SDK: maps our models to SDK types in `mapping.py` only; declares full capabilities;
  adaptive thinking, streaming for long outputs, native structured output, `strict` tool schemas,
  prompt-caching breakpoints on the stable prefix, token counting through the API, typed errors
  mapped to `LLMError` subclasses. Verify every call shape against the SDK docs at implementation
  time rather than memory.
- [ ] **3.7 OpenAI-compatible adapter (local models).** `llm/providers/openai_compat/{provider,
  mapping,client}.py` over `httpx` against `/v1/chat/completions` and `/v1/models`. Capabilities
  probed at startup where possible and otherwise set per provider in the manifest so a weak model
  honestly reports what it cannot do and the ladders take over. Token counting by estimate with a
  documented margin. No vendor SDK; one small client file.
- [ ] **3.8 Provider contract suite.** `tests/contracts/test_llm_provider_contract.py`
  parametrised over the fake, Anthropic and OpenAI-compatible adapters using recorded HTTP
  cassettes: completion, structured output at each rung, tool loop at each rung, streaming, error
  mapping, health, usage normalisation. A `live_llm` variant hits real endpoints on demand.
- [ ] **3.9 Prompt assets.** `llm/prompts/` with one file per prompt (`queen_system.md`,
  `decompose_goal.md`, `warden_system.md`, `drone_system.md`, `attendant_triage.md`), loaded by
  `llm/prompts/loader.py`; `llm/prompts/README.md` states the portability rules. Snapshot tests.
  Prompts label retrieved, hot-state and user-supplied content as such and delimit it.
- [ ] **3.10 The Cell abstraction.** `cell/models.py` (`Cell`: id, kind, capabilities, source,
  `ForageCapacity`, `comb_shield`; `CellKind` enum; `CellCapabilities`: os, arch, has_display,
  has_audio, has_browser, can_start_display, can_host_model, network scopes),
  `cell/session.py` (`CellSession` protocol: streaming `exec`, `put_file`, `get_file`,
  `scratch_dir`, `close`), `cell/lease.py` (`RealCellLease` with scratch root, started process
  ids, allowed paths, the Cell's `AccessLevel` and `CombShieldLevel` from 2.3a, idempotent
  `release()`), `cell/source.py` (`RealCellSource`), `cell/snapshot.py` (`Snapshotter` protocol:
  `snapshot(cell) -> SnapshotId`, `rollback(cell, snapshot)`; `NoopSnapshotter` for Real Cells
  with a documented warning; Virtual backends implement it in 5.10), `cell/fake.py`,
  `cell/errors.py`. Records `cell.leased` / `cell.released`.
- [ ] **3.11 The Hive Stand as a Real Cell.** `cell/local/`, a package, because source, session,
  lease directories and two platforms will not fit one file: `source.py` (`HiveStandSource`, one
  Cell whose capabilities and `ForageCapacity` are probed from the running machine), `probe.py`
  (platform, arch, cores, memory, GPU, display, browser, with POSIX and Windows shims side by
  side) and `session.py` (`LocalProcessSession`: asyncio subprocess, standard library only, POSIX
  and Windows). Each lease gets its own directory under `[hive_stand] scratch_root`,
  tracks child processes, and `release()` terminates survivors and removes the directory. Refuses
  to lease when disabled. `CellSession` contract suite over local and fake. A **left-as-found
  test** snapshots a temporary home and the process table before and after.
- [ ] **3.12 Forage v0: the models.** `forage/models.py`, one frozen model per concept, each
  field described:
  - `HostCapacity`: static (cores, memory, disk, GPUs with VRAM, arch, OS) and live (load, free
    memory, free VRAM) figures for one host.
  - `ModelSource`: one entry on the **Forage map**, the catalogue of everything that can serve a
    model: model id, provider or server, host it runs on, `grade` 1 to 5, context window,
    capability flags, cost per token or per seat, and the live figures `distance` (measured
    latency and tokens per second from a given Cell) and `abundance` (seats free, or rate-limit
    headroom for hosted).
  - `Seat`: one concurrent request on one model on one server; for hosted providers the
    equivalent is requests and tokens per minute plus spend caps.
  - `RoleFootprint`: what one bee of a role costs its Cell: cpu, memory, seats while mid-call
    (one), estimated token rate, extra memory with an Exoskeleton.
  - `ForageCapacity`: a Cell's report, `HostCapacity` plus the model servers on it plus its
    maximum concurrent sub-bees.
  - `ForageGrant`: the **shared** half a Warden receives from the Queen: grant id, holder,
    allowed bindings (slot, provider, model, maximum effort), seat reservations on shared servers
    and providers, token and spend budgets, spent so far, `expires_at`.
  - `LocalPool`: the half a Warden owns: its Cell's `HostCapacity` minus the footprint of any
    model server on it, the seats that server offers, and a small local reserve for the Warden's
    own awake mode and Patrol. Managed by `wardens/local_pool/` with the same allocator.
  - `Ceilings`: what the Queen sets once per device Warden or Nuc: maximum sub-bees, VRAM and
    disk usable for models, loadable Forage map entries, exportable seats.
  - `HostingPlan`: per Cell, for each slot a primary source and a fallback chain, plus a default;
    written by the Queen with a reason. Replaces any single hosting flag.
  - `ForageRequest`: grant id, delta wanted, reason; kinds `shared_seats`, `spend`, `binding`
    (a higher-grade source). `RoyalReserve`: what is held back from the shared pool before any grant.
  `forage/map.py` loads `[forage.map.*]` and `[llm.slots]` into the Forage map. `forage/allocate.py`
  v0: pure `grant(cell_capacity, role, tempo, map, reserve, manifest) -> ForageGrant` computing
  `max_sub_bees` as the minimum of the Cell's cap, free memory over the footprint's memory, free
  cores over its cpu, and reachable seats; `allowed` as the bindings whose grade meets tempo's
  floor and whose cost fits; static budgets from the manifest. Property-tested: a grant never
  exceeds capacity minus reserve. The live ledger comes in phase 4. `forage/tempo.py` and
  `forage/slots.py` already exist (2.3a, 3.4); `forage` imports nothing from `llm`, and
  `import-linter` (0.2) enforces it.
- [ ] **3.12a The Fanner v0.** `llm/fanner.py`: every model call from any bee passes through a
  per-binding semaphore sized to the seats that binding holds, with a queue ordered by tempo for
  excess requests and a rate limiter per hosted provider. It measures tokens per second and
  latency per call, updates `distance` and `abundance` on the Forage map, and emits usage to the
  ledger. Named after the bees that fan to regulate the hive; it is the only place seat counts are
  enforced, so a grant is a fact rather than a suggestion. It also implements **spill-over**
  along the hosting plan's chain: a call moves to the next binding when the current one's grade
  is below the tempo floor, the needed model is not loaded there, or the queue has waited past a
  threshold derived from the latency budget; each spill is an `llm.spill` event. It lives in
  `llm/`, so nothing under `autopilot/` may import it, and nothing needs to: autopilot never makes
  a model call. It imports no provider code, only the `LLMProvider` protocol and the Forage map.
- [ ] **3.13 Supervision protocol.** `supervision/supervisor.py` (`Supervisor` protocol:
  `children()`, `telemetry(child)`, `inspect(child) -> CompactView`, `intervene(child,
  Intervention)`), `supervision/alarm.py` (`Alarm`: id, kind, severity, origin, attempts, context
  reference, state machine `RAISED → HANDLING → ESCALATED | RESOLVED`; `AlarmKind` enum),
  `supervision/telemetry.py` (`ContextTelemetry`: tokens used and window, goal, last actions,
  blockers, spend), `supervision/intervention.py` (`Intervention` union: compact, checkpoint,
  handoff, rebind(slot), takeover, cancel), `supervision/policy.py` (`EscalationPolicy` loaded
  from TOML: `(kind, attempts) → action`; pure `decide`; `docs/supervision/default-policy.toml`
  loaded in a test), `supervision/attendant.py` (the `Attendant` any supervisor runs: one
  `InboxItem` type over Waggle messages, Alarms, questions, human messages, timers and watch
  observations; deterministic `score()` from kind, severity, age, task linkage, the task's latency
  budget and a per-principal weight table; an optional model tie-break on `ModelSlot.ATTENDANT`
  that the Queen enables and a Warden may enable only within its grant), `supervision/fake.py`.
- [ ] **3.13a Capability primitives.** `guard/capabilities.py`: the `Capability` families and the
  pure `CapabilitySet` with `allows()` and `attenuate(subset)` from 10.1, built now because 3.15
  and 3.16 gate on them; 10.1 adds the remaining families and 10.2 the policy engine.
  `guard/access.py`: what each `AccessLevel` (2.3a) permits, as data.
- [ ] **3.14 Memory v0.** `memory/handoff.py` (the `Handoff` schema with mandatory fields and
  capped notes), `memory/hot_state.py` (`assemble(principal, event, budget) -> Prompt`: v0 packs
  active tasks, open Alarms, pending questions, last N decisions, pins and notes by recency, then
  token-counts and trims), `memory/checkpoint.py` (write a Handoff, mark the raw transcript for
  Nectar deposit, record `memory.checkpoint`), `memory/pins.py` (manifest pins + a runtime table),
  `memory/notes.py` (the only thing a bee writes directly; bounded), `memory/episodes.py` (an
  `EpisodeRecord` for every awake episode and every autopilot decision: who, trigger, the
  assembled prompt by reference, the provider's reasoning summary where it exposes one, the
  decision, the action taken; stored in Bee Bread with a retention window, never on the trail,
  and streamable live so the Observation Hive can show any bee's thinking as it happens). Every
  memory-table row carries a `HoneyClearance` (2.3a) assigned from provenance, and `assemble`
  filters by the principal's allowance. Relevance scoring, Bee Bread and compaction come in
  phase 4.
- [ ] **3.15 Worker runtime.** `workers/base.py` (`Worker` protocol), `workers/runtime.py` (the
  loop: receive `TaskAssign`, run the role, send `TaskProgress`/`TaskResult`, honour `TaskCancel`,
  `TaskPause`, `TaskResume`; send `Heartbeat` with `ContextTelemetry`; honour every
  `Intervention`; checkpoint and reset at the manifest threshold; raise an `Alarm` instead of
  crashing), `workers/capabilities.py` (a Worker's `CapabilitySet` slice from 3.13a). The runtime receives a `Cell`, an open `CellSession`, a
  `BoundModel` and a `ForageGrant` slice; it never receives a provider, a subprocess handle, or the
  Cell's kind.
- [ ] **3.16 Drone role and built-in tools.** `workers/roles/drone.py` runs a bounded tool loop
  through `llm/tools.py` with prompts assembled by `memory.assemble`. `workers/tools/session.py`
  (`run_command`, `read_file`, `write_file` through `CellSession`, capability-gated outside
  scratch), `workers/tools/http.py`, `workers/tools/ask.py` (raise a `Question` up the chain).
  Tool calls are validated against schema and capabilities whichever rung produced them, and any
  call with a side effect outside scratch goes through Capping (3.17) first.
- [ ] **3.17 Capping gate v0.** `supervision/capping/`: `Proposal` (what a bee wants to do: a
  diff, a command, an action sequence; its declared **postconditions**; its risk tier),
  `tiers.py` (risk tiers as data in `docs/supervision/capping-tiers.toml`: `read_only`,
  `scratch_write`, `outside_scratch_write`, `network_egress`, `spend`, `device_command`,
  `irreversible`, each mapping to the checks it requires), `checks/deterministic.py` (schema,
  lint and type checks for code, path and command allowlists, diff size caps; autopilot, no
  model), `gate.py` (`propose → check → cap → apply → verify postconditions`, with `REJECTED` and
  `ROLLED_BACK` outcomes; `capping.*` trail events), `postconditions.py` (machine-checkable
  assertions: file exists, command exits zero, test passes, HTTP status, element text; checked
  after apply by the Warden, never by the bee that proposed). Uncapped work never leaves scratch.
  Judge review, snapshots, the flight recorder and sampled audit arrive in later phases as extra
  checks on the same gate.
- [ ] **3.18 Acceptance criteria.** The planner (3.20) emits `acceptance` for every subtask as a
  list of postconditions plus, where nothing machine-checkable exists, a rubric for a judge. A
  task reaches `SUCCEEDED` only when its Warden has run the acceptance checks and they pass; the
  bee that did the work cannot mark itself done. Failed acceptance raises an Alarm with the
  failing assertion attached.
- [ ] **3.19 Warden.** `wardens/warden.py` (standard loop; owns one Cell and its session; holds a
  `ForageGrant`; implements `Supervisor` over its sub-bees; reports its own `ContextTelemetry` and
  aggregate sub-bee telemetry to the Queen; runs the Capping gate and acceptance checks for its
  sub-bees), `wardens/state.py` (the Warden state machine: `STARTING → ACTIVE ↔ WATCH` for Real
  Cells with no active bees, `OFFLINE`, `CLUSTERED`, `MIGRATING`, `STOPPED`; single transition
  table), `wardens/autopilot/` (dispatch table for sub-bee events and Alarms: retry, respawn,
  rebind within the grant, escalate; heartbeats; watchdogs; never imports `hivemind.llm`),
  `wardens/awake/` (stateless episode on `ModelSlot.WARDEN`, prompt from `memory.assemble`,
  decides one action), `wardens/spawn/` (start a sub-bee on the Warden's Cell within the grant;
  attenuate capabilities; choose where the runtime process runs; the Worker state machine
  `SPAWNED → RUNNING ↔ HANDING_OFF`, `RUNNING ↔ PAUSED`, `→ DONE | FAILED | KILLED` lives in
  `workers/state.py`), `wardens/inbox/` (the Warden's Attendant over sub-bee heartbeats, results,
  Alarms, Queen messages and timers; autopilot-only by default), `wardens/local_pool/` (the
  Warden's own allocation over its `LocalPool`: sub-bee slots and local seats, never a request),
  `wardens/requests.py` (`ForageRequest` for shared Forage only, `CellRequest`, `ToolRequest` to the
  Queen, each with a reason), `wardens/offline/` and `wardens/watch/` (placeholders in this phase;
  real in phase 11). The Hive Stand's Warden runs on the Hive Stand, in the Queen's process over
  the memory transport by default, and exists whenever the Queen runs: `[hive_stand] enabled =
  false` refuses leases, it does not remove the Warden, which 11.9a and 13.2a rely on.
- [ ] **3.20 Queen kernel.** `queen/inbox/` (the Queen's Attendant, instantiated from
  `supervision/attendant.py` with the Queen's weight table: human messages heavy but not
  absolute, and `ModelSlot.ATTENDANT` enabled for ties and unknown kinds), `queen/autopilot/` (dispatch table:
  heartbeats, progress, results, grants within headroom, Alarms per policy, lease bookkeeping;
  sets the effort for any awake episode it hands off by event class; never imports
  `hivemind.llm`), `queen/awake/` (stateless episode on `ModelSlot.QUEEN` at the effort autopilot
  chose: assemble → decide one action → write back), `queen/planner/` (decompose a goal into a `TaskGraph` with `TaskNeeds` via
  `llm/structured.py`), `queen/placement/` (v0: the Hive Stand), `queen/dispatcher.py` (assign a
  task to a Warden, never to a Worker directly; issue the grant; track Warden liveness),
  `queen/questions.py` (decide whether a `Question` goes to the human; put the task in `BLOCKED`;
  route the `Answer` back), `queen/queen.py` (one tick = Attendant orders the inbox → autopilot
  handles → awake for `NEEDS_JUDGEMENT` → write back). The Queen implements `Supervisor` over
  Wardens. A test asserts she holds no session and no Comb Registry.
- [ ] **3.21 CLI.** `cli/run.py` (`hive run "goal" --manifest hive.toml`, streams progress),
  `cli/llm.py` (`hive llm providers|slots|test <slot>`), `cli/cells.py` (`hive cells list`),
  `cli/inbox.py` (`hive inbox` lists pending questions and Alarms at the human; `hive inbox answer
  <id> "..."`), `cli/wardens.py` (`hive wardens list` with grant, sub-bees, telemetry),
  `cli/capping.py` (`hive capping queue|show <proposal>`).
- [ ] **3.22 End-to-end tests.** `tests/e2e/test_kernel_on_hive_stand.py` with the fake provider:
  (a) the haiku goal completes through the Warden with the trail showing decompose → placed →
  leased → granted → spawned → proposed → capped → applied → verified → accepted → released;
  (b) a Drone killed by hand is respawned by the Warden's autopilot without an awake episode on
  the Queen; (c) a Drone that fails twice raises an Alarm that escalates to the Queen, who rebinds
  it to a stronger slot and it completes; (d) a Drone asks a question, the task blocks, `hive inbox
  answer` resumes it; (e) a Drone crossing the handoff threshold checkpoints, resets, and finishes
  from its Handoff; (f) a Drone proposing a write outside scratch without the capability is
  rejected at the gate and the file never appears; (g) a Drone whose declared postcondition fails
  after apply is rolled back and an Alarm is raised; (h) the left-as-found snapshot holds. Run
  with the fake at full and at zero capabilities.

### Exit criteria

- `hive run "write three haiku about bees to separate files"` completes on the Hive Stand through
  its Warden, with **both** `docs/manifests/minimal.toml` (Claude) and `docs/manifests/local.toml`
  (a local server), the scratch root is empty afterwards and no process the run started is alive.
- All eight e2e scenarios pass in CI in under 20 seconds at both capability levels, on Windows
  and Ubuntu.
- The hygiene scripts pass: no model ids or vendor imports outside their homes, no `subprocess`
  outside `cell/local/`, no `cell.kind` branch outside placement, no `hivemind.llm` import under
  any `autopilot/` or in `forage/`, no accumulating transcript outside `memory/`.

### ADRs to write

- `llm-provider-independence-and-model-slots.md`.
- `queen-never-executes-but-rebinds-and-takes-over.md`.
- `structured-output-and-tool-call-degradation-ladders.md`.
- `cells-are-real-or-virtual-terminal-first.md` (and why the Hive Stand is the first Cell).
- `kernel-shape-autopilot-then-awake.md` (autopilot never awaits a model; stateless awake).
- `wardens-alarms-and-the-escalation-chain.md` (one Warden per Cell, human last, policy as data).
- `forage-grants-and-attenuation.md`.
- `forage-map-seats-footprints-and-the-fanner.md` (capacity is several dimensions; seats are
  metered, not estimated; grades come from the map and are refined by evals; grants are leases;
  `llm` imports `forage` and never the reverse, which is why `ModelSlot` and `Tempo` live there).
- `forage-two-pools-ceilings-and-hosting-plans.md` (the Queen divides what is shared; a Warden
  divides what is on its Cell under ceilings set once; per-slot hosting plans with spill-over).
- `attendant-for-every-supervisor.md`.
- `capping-gate-postconditions-and-risk-tiers.md` (nothing lands uncapped; the proposer never
  verifies its own work; tiers as data).
- `tempo-speed-against-accuracy.md` (what reads tempo, what it may change, and the safety floors
  it cannot lower).

---

## Phase 4: Memory, Forage and Clustering

**Goal.** The kernel survives scale and outages. Hot state stays inside its budget no matter how
much happens; nothing that leaves hot state is lost; handoffs are good enough to resume from;
Forage is a live ledger the Queen divides; and a provider outage pauses and preserves instead of
failing.

**Depends on.** Phase 3.

**Deliverables.** `memory/` complete, `queen/forage/`, `queen/cluster/`, House Bee sweep duty,
`hive memory`, `hive forage`, `hive cluster`, `hive wake`.

### Steps

- [ ] **4.1 Relevance scoring and budget packing.** `memory/relevance.py`: pure `score(item,
  now, active_tasks, pins)` from recency decay, task linkage, Alarm severity and pins that never
  decay; property-tested for monotonicity. `memory/hot_state.py` packs by score to the budget,
  counts tokens through the provider or the estimate, drops lowest-scored first, replaces
  oversized items with references. Per-item cap from the manifest; large tool results become
  Nectar with a reference.
- [ ] **4.2 Bee Bread (warm tier).** `memory/bee_bread.py`: an index over Brood Chamber history
  and the trail by id, time and task, plus stored Handoffs and deposited transcripts; lookup only,
  no search; every entry carries its `HoneyClearance` and lookups filter by the reader's
  allowance. `memory/demote.py`: pure rules for what leaves hot state (task closed, Alarm resolved,
  age past a manifest window).
- [ ] **4.2a Cell Wax (cell notes).** `memory/cell_wax.py`: `CellWax` (id, Cell id, `WaxSeverity`
  `NOTE | CAUTION | BLOCK`, text capped by the manifest, proposer, reason, `HoneyClearance`,
  optional `expires_at`) with its own table and the transition table `PROPOSED → WRITTEN →
  CLEARED | EXPIRED`, `PROPOSED → REJECTED` (Appendix C). Any bee, Warden or the human may
  propose one, over Waggle (`CellWaxProposed`, 1.3) or from the chat; a proposal is an inbox item
  the Attendant scores low. Only the Queen writes, rejects or clears: autopilot accepts a Warden's
  `NOTE` or `CAUTION` about its own Cell within the per-Cell cap, and every `BLOCK`, every
  proposal from a Worker or about another Cell, and every clear is an awake decision. Wax enters
  hot state only when its Cell is a candidate for placement or assignment, a relevance rule in 4.1
  bounded by the cap, so a caution about a flaky device costs nothing until the device is in
  play. The House Bee sweep (4.3) expires wax past `expires_at` and hands cleared and expired wax
  to ripening once phase 7 lands, so the history compounds as Honey at `cell:<id>` scope.
  `memory.wax_*` events. Named for the wax bees use to cap and mend a cell: it marks the cell, it
  is not the honey inside.
- [ ] **4.3 Compaction.** `memory/compact.py`: summarise from source records on `ModelSlot.RIPENER`,
  never from a previous summary; pins verbatim; one level of summary; records `memory.compacted`.
  A House Bee **sweep** duty (`workers/roles/house_bee.py`, first version) runs demotion and
  compaction on a timer, and ripens Bee Bread into Honey once phase 7 lands.
- [ ] **4.4 Overflow recovery.** `ContextTooLong` from any provider shrinks the budget for that
  episode and retries, recording `memory.overflow`; three overflows raise an Alarm. A synthetic
  flood test pushes ten thousand events through the Queen and asserts the assembled prompt never
  exceeds the budget and every dropped item is findable in Bee Bread.
- [ ] **4.5 Handoff quality eval.** `tests/evals/handoff/`: a bee is stopped mid-task, a fresh bee
  resumes from the Handoff alone and must complete; graded on completion and on not repeating
  do-not-redo steps. Run with the fake in CI and with real providers under `live_llm`.
- [ ] **4.6 Context interventions end to end.** The Queen watches Warden telemetry and orders
  `compact` or `handoff` past thresholds; Wardens do the same to sub-bees; `inspect` returns a
  compacted view under a size cap. A test asserts no full transcript ever appears in a Waggle
  message.
- [ ] **4.7 Forage ledger and allocation v1.** `queen/forage/ledger.py`: the live book. It holds
  every Cell's latest `ForageCapacity`, rolling measurements per host and per binding from the
  Fanner, seats in use and free per server and provider, spend per grant and per goal, the Royal
  Reserve, and headroom as shared totals minus reserve minus the sum of live shared grants. Local
  pools appear in the ledger as **reported**, not granted: each Warden's usage report against its
  ceilings, plus any seats it has exported to the shared pool. The Queen's allocator never hands
  out local capacity; it only reads it to size hosting plans and to know what a Nuc can bear. `forage/allocate.py`
  v1 adds to v0: the goal's remaining bee cap and spend cap as inputs, tempo (urgent work may get
  more parallelism, thorough work more spend), reachability under the Cell's network policy and
  hosting decision, and recomputation when measurements drift past a manifest threshold.
  `queen/forage/grants.py`: grants are leases, renewed on the Warden's heartbeat and returned to
  the pool at `expires_at` if the Warden is dead or offline; they can be shrunk or revoked; a
  Warden over its grant gets an Alarm, not a crash. `queen/forage/requests.py`: `ForageRequest`
  handled by autopilot rule within headroom, by awake when contested; `forage.granted` /
  `forage.denied` with the reason. A synthetic test starts fifty Wardens against a small fake
  capacity and asserts the sum of grants never exceeds capacity minus reserve.
- [ ] **4.8 Hosting plans and ceilings.** `queen/forage/hosting.py`: per Cell, write a
  `HostingPlan` (per slot a primary source and a fallback chain, plus a default) from the Forage
  map and ledger: the Cell's free VRAM against each model's requirement, seat pressure on the Hive
  Stand, the measured distance from the Cell to each candidate source against the task's tempo,
  the task's need to survive disconnection, and cost caps; local sources first wherever the Cell
  has them; recorded as `forage.plan_written` with the reason. `queen/forage/ceilings.py`: set
  and change a Warden's `Ceilings` (maximum sub-bees, VRAM and disk for models, loadable map
  entries, exportable seats), recorded as `forage.ceilings_set`. Routing (phase 8) consumes the
  plan; a local source is only selectable once phase 8.3 can start a server on a Cell.
- [ ] **4.9 Clustering.** `queen/cluster/protocol.py`: triggered per provider by `ProviderHealth`
  failing with no fallback within Forage, by a cost cap, or by `hive cluster`: checkpoint every
  affected bee, move their tasks to `PAUSED`, keep leases and Cells alive, keep heartbeats and
  watchdogs, poll health with backoff, resume from Handoffs on `hive wake` or recovery, record
  `queen.clustered` / `queen.resumed`. Bees on other providers continue. The Queen's own awake mode
  being unavailable is handled by her autopilot running the same protocol.
- [ ] **4.10 Judge review and sampled audit.** `supervision/capping/checks/judge.py`: an
  independent review on `ModelSlot.JUDGE` with no shared context with the proposing bee, a rubric
  per risk tier, and a structured verdict (approve, request changes, reject) with reasons; the
  manifest may pin `JUDGE` to a different provider than `WORKER` so blind spots do not correlate.
  `supervision/capping/audit.py`: for tiers the table marks as not gated in real time, sample
  completed work at a per-tier rate, review it with the judge after the fact, deposit findings as
  Nectar, raise an Alarm on a failed audit, and feed rates to the Guard Bee (phase 10). Both are
  extra checks on the gate from 3.17; the tier table decides where each applies, and the task's
  tempo may shorten or lengthen the ladder only above the tier's floor (an urgent `scratch_write`
  may skip the judge; `irreversible` never skips anything).
- [ ] **4.11 CLI.** `cli/memory.py` (`hive memory show <bee>`, `hive memory pins add|list|remove`,
  `hive memory compact <bee>`, `hive memory wax <cell> list|propose|clear`), `cli/forage.py` (`hive forage status|grants|grant <warden> ...`),
  `cli/cluster.py` (`hive cluster [provider]`, `hive wake`), `hive capping audit --sample`.

### Exit criteria

- The flood test holds the budget; the handoff eval passes; the phase 3 scenarios still pass with
  a hot-state budget deliberately set to a quarter of the default.
- Kill the fake provider mid-run: the trail shows checkpoint → `PAUSED` for every affected bee,
  leases stay open, `hive wake` after restoring the provider resumes every task from its Handoff
  and the goal completes with no duplicated work.
- A Warden asking for more sub-bees than the Hive Stand can bear is denied with a reason on the
  trail; the same request under headroom is granted by autopilot with no awake episode.
- With the fake provider limited to two seats, six Drones run with at most two model calls in
  flight at any moment, the Fanner's queue orders them by tempo, and the Forage view (phase 12)
  would show seats at 2 of 2 throughout. A Warden whose heartbeat stops has its grant back in the
  pool after expiry.
- A Warden's `CAUTION` about its own Cell is written by autopilot with no awake episode and
  appears in the planner's prompt only when that Cell is a candidate, never in any other episode;
  a `BLOCK` proposed by a Drone reaches the Queen's awake mode; an expired note leaves hot state
  on the next sweep.

### ADRs to write

- `memory-tiers-relevance-and-compaction.md` (including Cell Wax as a hot-state item with its own
  relevance rule and cap, and why only the Queen writes it).
- `forage-ledger-and-model-hosting-decisions.md`.
- `clustering-protocol.md`.

---

## Phase 5: The Hive (Virtual Cells) and placement

**Goal.** The Queen can choose, per task, between borrowing a Real Cell and provisioning an
isolated, disposable Virtual Cell. Every Virtual Cell gets its own Warden. Undertakers destroy
Virtual Cells and release Real ones. An Overwintering pool keeps warm Virtual Cells for reuse,
except Night Veil Cells which are always just-in-time and teardown-only.

**Depends on.** Phases 3 and 4.

**Deliverables.** `hivemind/hive`, `queen/placement/` for real, `images/base-ubuntu`, Undertaker
role, `hive cells` CLI.

### Steps

- [ ] **5.1 Virtual Cell model.** `hive/models.py`: `VirtualCellSpec` (image, cpu, memory, disk,
  lifetime, network policy, exoskeleton flag, `ForageCapacity` the image promises),
  `VirtualCellStatus` enum + transition table in `hive/cell_state.py`. A provisioned Virtual Cell
  is returned as a `cell.Cell` of kind `VIRTUAL`.
- [ ] **5.2 CellBackend protocol and registry.** `hive/backends/base.py` (from codingrules
  Appendix A.1), `hive/registry.py`. `hive/backends/fake.py` for tests.
- [ ] **5.3 Base image.** `images/base-ubuntu/Dockerfile`: Ubuntu 24.04 LTS + Python + the
  `hivemind` runtime + a Waggle client that connects **out** to the Queen. Terminal-first. The
  image's entry point starts a **Warden**, which then spawns sub-bees inside the Cell.
  `images/base-ubuntu/README.md` explains every layer.
- [ ] **5.3a Night Veil image.** `images/night-veil-ubuntu/Dockerfile` on `desktop-ubuntu`
  (6.1, so this step lands after it): OpenVPN client, Tor daemon, Tor Browser, nftables
  kill-switch rules, and the location-blind defaults baked in (UTC, fixed locale, hostname
  randomised at boot, metadata endpoints null-routed). README documents each package and each
  rule. Built in CI with the other images; attestation (5.7b) checks this image and installs
  nothing at runtime.
- [ ] **5.4 Docker backend.** `hive/backends/docker.py` using the Docker SDK under
  `asyncio.to_thread`: create container from spec with resource limits and a network policy
  (`none`, `egress-only`, `allowlist`), wait for `CellReady` and the Warden's first `Heartbeat`,
  destroy with volume cleanup. Contract suite passes. Integration test marked.
- [ ] **5.5 In-Cell session and Warden spawn strategy.** `cell/in_cell.py`: `InCellSession`, used
  when the Warden and its sub-bees run inside the Virtual Cell. `wardens/spawn/` gains the
  `in_cell` strategy. `CellSession` contract suite runs over local, in-cell and fake.
- [ ] **5.6 Virtual Cell lifecycle.** `hive/lifecycle.py`: `provision → Warden ready → grant →
  release → (overwinter | teardown)`, each transition a `cell.*` event. Night Veil path is always
  `provision → Warden ready → grant → teardown` (no Overwinter branch).
- [ ] **5.7 Placement policy.** `queen/placement/decide.py`: pure `decide(needs, inventory,
  forage, policy) -> Placement` over Real Cells with free capacity and Virtual backends with
  headroom, honouring `[placement]` (`prefer = "real" | "virtual"`, `allow_hive_stand`, per-role
  overrides). Rules, each with a test: `isolation = "required"` always Virtual; Exoskeleton needs a
  display or the ability to start one; OS and network scopes must match; Forage must cover the
  grant; a `BLOCK` Cell Wax (4.2a) excludes the Cell and a `CAUTION` counts against it; otherwise
  honour `prefer`. Records `queen.placed` with the reason, the wax that weighed on it included.
- [ ] **5.7a Night Veil placement and routing constraints.** `queen/placement/policy.py` and
  `queen/forage/hosting.py`: if `TaskNeeds.comb_shield = NIGHT_VEIL`, placement is Virtual-only,
  request must be explicitly human-originated, network profile must be OpenVPN + Tor with direct
  egress blocked, and every model slot in the hosting plan must resolve to local providers only
  (no Hive Stand or hosted fallback). The Cell's Waggle transport (`waggle/transport/websocket.py`)
  connects out over the Tor SOCKS proxy to the Hive Stand's `.onion` hidden-service address, never
  the OpenVPN tunnel and never the default route, so supervision traffic never touches a clearnet
  Hive Stand address that a network observer could link back to the operator.
  The profile also enforces location-blind defaults
  (UTC timezone, fixed locale, randomized hostname, blocked metadata endpoints). A Real Cell's
  tier is an enrolment attribute set by the operator (11.8a); placement never changes it.
- [ ] **5.7b Night Veil deterministic bootstrap attestation.** `hive/night_veil.py`: before
  `CellReady`, attest the `night-veil-ubuntu` image (5.3a) deterministically, installing nothing
  at runtime: firewall kill-switch active,
  default route via VPN tunnel, Tor daemon healthy, Tor Browser installed and launchable, the
  Hive Stand's Waggle hidden service reachable and the Waggle client's socket routed through the
  Tor SOCKS proxy (not the VPN interface), DNS leak
  checks passing, and direct egress blocked. Location-blind checks must also pass: geolocation APIs
  denied, metadata endpoints unreachable, timezone pinned to UTC, locale pinned to profile, and
  WebRTC local-IP leak test blocked. Fail placement if any check is red.
- [ ] **5.8 Undertaker role.** `workers/roles/undertaker.py`: destroys Virtual Cells idempotently,
  releases Real Cell leases idempotently, revokes their grants, retires a destroyed Virtual Cell's
  Cell Wax (handed to ripening once phase 7 lands; a Real Cell's wax outlives its leases), retries
  with backoff. On Queen
  startup sweeps orphans of both kinds from backend labels and the trail.
- [ ] **5.9 Overwintering pool.** `hive/overwinter/policy.py` (pure, Virtual only),
  `hive/overwinter/pool.py`. Placement prefers a dormant Cell with the right image. Clustering uses
  the pool for long outages. Night Veil Cells are excluded.
- [ ] **5.10 Snapshots for Capping.** `hive/snapshot.py`: `DockerSnapshotter` and
  `QemuSnapshotter` implementing the `Snapshotter` protocol from `cell/snapshot.py` (3.10)
  (Docker commit, QEMU snapshot; a documented no-op with a warning for backends that cannot). The
  Warden injects its Cell's `Snapshotter` into the Capping gate, so Capping never imports `hive`.
  The Capping gate takes a snapshot
  before any proposal in the `irreversible` or `device_command` tiers on a Virtual Cell and rolls
  the whole Cell back when postconditions fail. Snapshots are accounted as Forage (disk) and
  expire with the manifest's retention.
- [ ] **5.11 QEMU backend.** `hive/backends/qemu.py`: prebuilt qcow2 (`images/base-ubuntu/vm/` from
  `scripts/build_cell_image.py`), cloud-init for the Waggle endpoint and Warden bootstrap, serial
  console for readiness. Same contract suite.
- [ ] **5.12 Cloud backend interface.** `hive/backends/cloud/base.py` with credentials via
  `SecretStr`, region, pricing tag as Forage cost, and one reference implementation chosen in the
  ADR. Optional for Brood 1.0.
- [ ] **5.13 CLI.** `cli/cells.py` grows `inspect`, `destroy <virtual>`, `release <lease>`,
  `snapshot <cell>`, `rollback <cell> <snapshot>`, and `abscond` (destroy every Virtual Cell
  tagged with this Hive id and release every lease, from backend labels and the trail alone).

### Exit criteria

- The haiku run completes three ways: `prefer = "real"` uses the Hive Stand and zero containers;
  `prefer = "virtual"` uses three containers, each with its own Warden visible in `hive wardens
  list`; a task with `isolation = "required"` uses a Virtual Cell regardless.
- A second run with `prefer = "virtual"` reuses Overwintered Cells and is measurably faster, while
  Night Veil runs always provision fresh Cells and always teardown at completion.
- `hive cells abscond` leaves zero containers, zero open leases, zero live grants, and the
  left-as-found snapshot holds.
- A `BLOCK` Cell Wax on the Hive Stand makes `prefer = "real"` place on a Virtual Cell with the
  wax named in the `queen.placed` reason; clearing it restores the first result.

### ADRs to write

- `cell-backends-docker-first-qemu-second.md`.
- `virtual-cells-connect-outbound-only-and-boot-a-warden.md`.
- `placement-policy-real-versus-virtual.md`.
- `overwintering-policy.md`.
- `night-veil-retention-and-clearance-boundary.md` (which events survive teardown, coding rules
  12; the image; why Honey the work ripened is the one intentional export).

---

## Phase 6: Exoskeleton (virtual peripherals, on any Cell, on demand)

**Goal.** A Worker can drive a full desktop session on whichever Cell it is bound to: see a
framebuffer, send keyboard and mouse input, play or capture audio. Attached only when the task asks
and, on a Real Cell, everything started is stopped on release. The purpose is GUI automation of
applications that have no API; it is not a stealth layer (coding rules section 15).

**Depends on.** Phase 5.

**Deliverables.** `hivemind/exoskeleton`, `images/desktop-ubuntu`, Forager and Scout roles.

### Steps

- [ ] **6.1 Desktop image.** `images/desktop-ubuntu/Dockerfile` on `base-ubuntu`: Xvfb, a light
  window manager, `xdotool`, PulseAudio null sink and virtual source, a browser, fonts. Reports
  `has_display`, `has_audio`, `has_browser`. README documents each package.
- [ ] **6.2 Protocols.** `CompoundEye`, `Antennae`, `Buzz` under `exoskeleton/*/base.py`, each
  operating through a `CellSession` so the same backend works inside a Virtual Cell and on a Linux
  Real Cell. Every method documents latency class and failure mode.
- [ ] **6.3 X11 backends.** `compound_eye/x11.py`, `antennae/xdotool.py`, `buzz/pulseaudio.py`.
  On a Real Cell with a display and the `exoskeleton:real_display` capability they use it;
  otherwise, if `can_start_display`, attach starts an Xvfb and null sink owned by the lease.
- [ ] **6.4 Attach on demand.** `exoskeleton/attach.py`: `attach(cell, session, needs) ->
  ExoskeletonHandle` picks backends from capabilities, starts what is missing through the session,
  registers every process with the lease, and `detach()` stops exactly those. A test asserts no
  display process exists after a terminal-only task.
- [ ] **6.5 Exoskeleton tools.** `workers/tools/exoskeleton.py`: `see`, `click`, `type`, `press`,
  `scroll`, `listen`, `say`. Screenshots never logged. `see` is offered only when the bound model
  declares `vision`; `listen` returns a `Transcript` through 6.5a unless the bound model declares
  `audio`. Every action tool takes an optional declared postcondition (expected URL, element text,
  or a region that should change) that the Capping gate verifies afterwards.
- [ ] **6.5a Ears: the transcription provider.** `llm/transcription.py` (`TranscriptionProvider`:
  `transcribe(audio, language) -> Transcript` with segments and timestamps, `stream(chunks)` for
  push-to-talk, `capabilities`, `health`; `Transcript` and `TranscriptSegment` are HiveMind's own
  models, no library type leaves an adapter), `llm/providers/whisper/` (faster-whisper in
  process, an optional extra, GPU when present, one seat per loaded model instance),
  `llm/providers/openai_compat/transcription.py` (`/v1/audio/transcriptions`, which covers hosted
  Whisper APIs and local servers alike), `FakeTranscription` with scripted transcripts. Bound
  through `ModelSlot.TRANSCRIBER` (3.4), metered by the Fanner, listed on the Forage map as a
  source with its own grade. Contract suite over all three with fixture clips. The same provider
  serves the human's voice at the Entrance (10.5f); Buzz is only its first caller.
- [ ] **6.6 Flight recorder.** `exoskeleton/recorder.py`: while an Exoskeleton is attached,
  record every action with its arguments, the screenshot before and after, the accessibility tree
  or DOM snapshot where the fast path has one, the declared postcondition and its result. The
  recording is stored as Nectar with references from the episode record, never in logs or on the
  trail, redacted for secrets at the source. A vision-capable judge (4.10) reviews a recording
  against the goal for the `irreversible` tier before the next step, and sampled audit (4.10)
  reviews the rest after the fact. The Observation Hive plays recordings back (12.4).
- [ ] **6.7 Structural assertions and rehearsal.** For browser work the gate prefers assertions
  on structure over pixels: URL, accessibility tree, element text. A browser procedure that will
  be reused is rehearsed against a fixture or staging site first and then promoted as a tool
  through the Royal Jelly Lab (9.3), so production runs execute a capped procedure rather than an
  improvised one.
- [ ] **6.8 Fake exoskeleton.** `exoskeleton/*/fake.py`; contract suite over real and fake,
  including the recorder.
- [ ] **6.9 Forager role.** `workers/roles/forager.py`: bounded see/act loop; page content to
  `Nectar`.
- [ ] **6.10 Scout role.** `workers/roles/scout.py`: recon with a strict budget returning a
  `ScoutReport` the Queen uses before committing Foragers.
- [ ] **6.11 Playwright fast path.** `exoskeleton/browser/playwright.py`: headed in the Cell's
  display when one exists, headless otherwise; `browser_*` tools that also work without vision
  through the accessibility tree. The only Exoskeleton on Windows and macOS Real Cells for 1.0.
- [ ] **6.12 Placement integration.** `TaskNeeds.exoskeleton` drives placement: a Real Cell
  qualifies if it has or can start a display, or the task is browser-only; otherwise a
  `desktop-ubuntu` Virtual Cell.
- [ ] **6.13 Pheromone Mask tactics (policy-gated, callable overlays).**
  Replace persistent mode semantics with two callable tactics. `supervision/mask.py` holds the
  per-Cell mask state (`OFF`, `WARDEN`, `QUEEN_FORCED`) with reason and expiry and its one
  transition table (Appendix C). The tactics live where they act:
  `workers/tactics/write_like_human.py`, because prose is shaped on terminal-only Cells too, and
  `exoskeleton/tactics/mouse_like_human.py`.
  Invocation is per task segment with explicit reason, max-step or time budget, and auto-expiry.
  `mouse_like_human` varies key timing, pointer speed and pause cadence inside bounded windows,
  while preserving replay metadata in the flight recorder. `write_like_human` tunes prose rhythm
  and tone for user-facing output. `write_like_human` enforces a writing profile: ban em dashes,
  vary sentence length, prefer concrete wording over filler, allow natural contractions where
  appropriate, and avoid repetitive boilerplate transitions. Both are disabled by default and
  require explicit capability and policy opt-in. Add a Queen-controlled Cell-scope override path (`QUEEN_FORCED`) with explicit
  reason, expiry and clear semantics that the Warden must enforce while active.

### Exit criteria

- The fixture-site login goal succeeds in three placements: a `desktop-ubuntu` Virtual Cell, a
  Linux Real Cell on the Ubuntu runner with a lease-started Xvfb, and the Windows Hive Stand via
  Playwright.
- After each Real Cell run no lease-started display, audio or browser process is alive and the
  left-as-found snapshot holds; no screenshot bytes in logs or trail.
- The login scenario's recording plays back in the Observation Hive with before and after frames
  per action, and a deliberately wrong click fails its declared postcondition, is rolled back on
  the Virtual Cell, and raises an Alarm.

### ADRs to write

- `exoskeleton-on-x11-with-playwright-fast-path.md`.
- `transcription-provider-whisper-first.md` (one slot for ears and for the human's voice;
  faster-whisper in process versus a server; default model size; where audio is and is not kept).
- `exoskeleton-scope-and-pheromone-mask-boundary.md` (what the Exoskeleton is for, what is out
  of scope, and where the two mask tactics stop).
---

## Phase 7: Honey Store (knowledge base, the cold tier)

**Goal.** Knowledge compounds across runs. Workers deposit Nectar; House Bees ripen it into Honey;
Bee Bread ripens into Honey on schedule; the Queen and Workers query Honey before acting.

**Depends on.** Phases 3 and 4. Can run in parallel with 5 and 6.

**Deliverables.** `hivemind/honey_store`, `llm/embedding.py` with two adapters, House Bee ripening
duty, `hive honey` CLI.

### Steps

- [ ] **7.1 Embedding protocol and adapters.** `llm/embedding.py` (`EmbeddingProvider`),
  `llm/providers/openai_compat/embedding.py`, `llm/providers/sentence_transformers/embedding.py`
  (in-process, optional extra), `FakeEmbedding`. Bound through `ModelSlot.EMBEDDER`. Contract
  suite over all three.
- [ ] **7.2 Schema.** Migrations for `nectar`, `honey` (with embedding model id and provenance),
  `clearance` column (`C0`, `C1`, `C2`) on both tables, FTS5 and `sqlite-vec` virtual tables.
  Changing the embedder slot triggers `hive honey reembed`.
- [ ] **7.3 Models.** `Nectar`, `Honey`, `HoneyQuery`, `HoneyHit`; provenance mandatory (task,
  Worker, Cell, time) and clearance mandatory. `honey_store/clearance.py` assigns the label
  deterministically from provenance at intake (anything from a Real Cell, a human message or watch
  mode is `C2`); a model may raise a label during ripening and never lower one; lowering is a
  Capping proposal reviewed by the judge or a human.
- [ ] **7.4 Nectar intake.** `honey_store/nectar/intake.py` over Waggle; hash, dedupe, store,
  size cap. Deposited transcripts from checkpoints arrive here.
- [ ] **7.5 Ripening pipeline.** `chunk.py`, `summarise.py` (via `llm/structured.py` on
  `ModelSlot.RIPENER`), `embed.py`, `dedupe.py`, `index.py`, composed by `pipeline.py`.
- [ ] **7.6 House Bee, complete.** `workers/roles/house_bee.py` now has three duties: sweep hot
  state (phase 4), ripen Nectar, and ripen aged Bee Bread into Honey. Runs in the Queen's process
  by default.
- [ ] **7.7 Retrieval.** Hybrid FTS + vector search with manifest weights; result token budget
  scaled by the bound model's window and folded into `memory.assemble` as the cold tier.
- [ ] **7.8 Waggle integration.** `HoneyQuery` / `HoneyResponse`; results injected as delimited,
  labelled untrusted content.
- [ ] **7.9 Queen pre-check.** The planner queries Honey for the goal's targets and for the chosen
  Cell's known quirks, attaching hits to `TaskAssign`; `queen.honey_consulted`.
- [ ] **7.9a Cell Wax ripens and is consulted.** Cleared and expired Cell Wax (4.2a) is ripened
  into Honey at `cell:<id>` scope with the note's provenance and severity, so a Cell's history of
  cautions compounds without living in hot state. The pre-check in 7.9 attaches that history for
  the chosen Cell alongside its live wax, and the browser (7.10) lists a Cell's live wax under its
  folder. Live wax stays a memory-table item, never a Honey row.
- [ ] **7.10 Honey scoping and browser.** `honey_store/scope.py`: every Honey row carries a
  scope derived from provenance (`hive`, `cell:<id>`, `bee:<id>`, `task:<id>`) and a visibility
  rule; a bee's `honey:read:<scope>` capabilities decide what its queries can return. Query
  filtering applies both scope and clearance, with Comb Shield policy denying Royal data on
  Night Veil Cells.
  `honey_store/browse.py`: a read-only virtual folder tree over the store (`/hive/...`,
  `/cells/<cell>/...`, `/bees/<bee>/...`, `/tasks/<task>/...`, `/bee-bread/...`) with listing,
  reading and search per folder, so the Observation Hive and the CLI can walk the Hive's knowledge
  the way you walk a filesystem. Browsing never writes; every write to Honey goes through the
  Queen (intake, ripening, pins), and the browser exposes "propose a note" as a message into her
  inbox.
- [ ] **7.11 CLI.** `hive honey query|stats|ripen --now|reembed|ls <path>|cat <path>`.

### Exit criteria

- The phase 6 Forager goal run twice: the second `TaskAssign` carries Honey about the site and the
  Forager takes fewer steps.
- A Handoff deposited in phase 4 is retrievable through Honey a day later with full provenance.
- Ripening runs with `RIPENER` and `EMBEDDER` on local adapters in the `local_llm` job.

### ADRs to write

- `honey-store-sqlite-fts5-sqlite-vec.md`.
- `embedding-provider-and-reembedding-policy.md`.

---

## Phase 8: Local models and provider routing

**Goal.** Moving from Claude to locally hosted models is a configuration exercise: every slot can
run on a local model, the Hive can run with no external network, routing weighs Forage and where
the model runs, and there is measured evidence for which slots should stay hosted.

**Depends on.** Phases 4 and 7.

**Deliverables.** Routing policy, offline mode proven end to end, evaluation harness, per-provider
prompt overlays, `hive llm eval`.

### Steps

- [ ] **8.1 Routing policy.** `llm/routing.py`: pure `choose_binding(slot, tempo,
  required_capabilities, forage, hosting, manifest) -> BoundModel` (it takes `Tempo` and the
  capability flags, not `TaskNeeds`, so `llm` stays below `cell`) honouring fallback chains,
  required capabilities, Forage
  headroom, model location relative to the bee, cost caps, `offline`, and the task's tempo. It
  reads the Forage map: tempo's accuracy bar sets a minimum grade, its latency budget a maximum
  distance, and among sources that satisfy both and have abundance the cheapest wins. The binding
  carries the model's effort setting, chosen from the same two inputs, so an urgent low-bar task
  lands on a fast local model at low effort and a critical one on the strongest slot at high
  effort. Each role has a default grade floor in the manifest (the Queen the highest available;
  the Attendant and Ripener low) that tempo raises but never lowers, and a rebind on escalation
  raises the floor for that bee. Routing follows the Cell's hosting plan: local first where the
  Cell has local sources, then the chain, with the Fanner's spill-over rule (3.12a) deciding when
  to move along it at call time. Fallbacks are `llm.fallback` events; a fallback that needs more
  Forage becomes a `ForageRequest`. The eval harness (8.5) reports quality and latency per tempo
  so the mapping is tuned from measurements, not guesses.
- [ ] **8.2 Local server management.** `llm/providers/ollama/manage.py` (optional): list, pull,
  `keep_alive`, health. `hive doctor` uses it. Generic health probe covers vLLM and llama.cpp.
- [ ] **8.3 Model server on a Cell.** `wardens/local_pool/hosting.py` (it needs the Cell's
  `CellSession`, so it lives with the Warden that owns the Cell, not in `forage`): start and stop
  an OpenAI-compatible server on a Cell that reports `can_host_model` (through its session), register
  it as a provider with a loopback-only or Cell-local base URL, add its models to the Forage map
  as new sources with measured distance from that Cell, and report its seats to the ledger as
  part of that Cell's local pool. The Cell's Warden loads and unloads models on it autonomously
  within its ceilings and allowlist, reporting each as `forage.source_added` / `removed`; the
  Queen may evict a model only to reclaim seats she has exported. This is what local sources in a
  hosting plan and Nucs (phase 11) build on.
- [ ] **8.4 Prompt portability pass.** Run every prompt snapshot and the phase 3 scenarios against
  at least two local models of different sizes; add overlays under `llm/prompts/overlays/<provider>/`
  where needed, each with a header saying why.
- [ ] **8.5 Evaluation harness.** `tests/evals/` with a fixed goal set and graders (decomposition
  validity, `TaskNeeds` correctness, tool-call validity rate, ladder rung reached, Alarm rate,
  handoff-resume success, step count, cost, wall time), run by `hive llm eval`, writing
  `docs/evals/<date>.md`. `JUDGE` may itself be local. With `--update-map` the harness writes
  measured per-slot scores back onto the Forage map as `measured_grade`, so hand-set grades are
  replaced by evidence and routing's grade floors are grounded in the Hive's own results.
  `TRANSCRIBER` is graded by word error rate and latency on a fixture clip set, so the default
  Whisper size is chosen from measurements.
- [ ] **8.6 Offline mode end to end.** `docs/manifests/offline.toml`; a `local_llm` e2e job runs
  the haiku goal on the Hive Stand and in a Virtual Cell, the ripening scenario, a spoken goal
  transcribed on the in-process Whisper, and a Clustering round trip, with no network egress.
- [ ] **8.7 Local vision.** The Forager scenario with a local vision model in `WORKER`, or the
  accessibility-tree path where none is available.
- [ ] **8.8 Cost and token accounting for local models.** Zero or amortised prices in the
  manifest; usage from the response or a documented estimate; both uniform on the trail.
- [ ] **8.9 Runbook.** `docs/runbooks/moving-a-slot-to-local.md`.

### Exit criteria

- The haiku goal, ripening, the browser Forager scenario and a Clustering round trip complete with
  `offline.toml`; every `llm.call` on a local provider.
- `docs/evals/` holds a table comparing Claude and two local models across every slot.
- Switching any slot between providers changes only the manifest; a checksum of `src/` is
  identical between the two configurations in the e2e job.

### ADRs to write

- `local-model-serving-via-openai-compatible-api.md`.
- `provider-routing-forage-aware-fallbacks-and-offline-mode.md`.

---

## Phase 9: Royal Jelly Lab + Comb Registry

**Goal.** The system extends itself safely: a Worker or Warden requests a capability, the Lab
scaffolds a tool, the Quarantine Comb proves it in a sandbox Cell, and only then is it promoted, at
hive scope by the Queen or at cell scope by the requesting Warden.

**Depends on.** Phases 5 and 7.

**Deliverables.** `hivemind/royal_jelly`, `hive tools` CLI.

### Steps

- [ ] **9.1 Tool spec format.** `ToolSpec` (name, version, model-facing description, input
  schema with `additionalProperties: false`, required capabilities, entry point, dependencies,
  supported OSes, `scope = "hive" | "cell"`). `validate.py` rejects specs requesting capabilities
  beyond the requester's own.
- [ ] **9.2 Comb Registry.** SQLite table + package dir; `CombRegistry` with `list`, `get`,
  `promote(spec, report, scope)`, `retire`. `promote` refuses without a passing `QuarantineReport`;
  hive scope requires the Queen principal; cell scope requires the owning Warden. A test asserts
  there is no bypass.
- [ ] **9.3 Scaffolder.** From a `ToolRequest` produce spec, source and tests through
  `llm/structured.py` on `ModelSlot.SCAFFOLDER`; tests first. The request carries the target
  Cell's capability report (OS, distro, arch, package manager, shell, Python availability) so the
  scaffolder writes for that platform and declares it in `supported_os`. Generated tools act only
  through the `CellSession` they are handed; a direct `subprocess` import fails the Comb. A Warden
  may run the scaffolder locally within its grant.
- [ ] **9.3a Quarantine for non-Ubuntu targets.** The Comb's sandbox is an Ubuntu Virtual Cell,
  so a tool written for Windows, macOS or another distro cannot be fully exercised there. Until
  Virtual Cell images exist for those platforms (post-1.0), such tools pass static checks and
  schema fuzzing in the Ubuntu sandbox, then run their tests inside a lease's scratch directory on
  a Real Cell of the target platform under a dedicated `tool:quarantine_on_real` capability, with
  the flight recorder on and the tier set to `device_command`. The report says which path was
  used; hive-scope promotion of such a tool needs the Queen's awake review, not just autopilot.
- [ ] **9.4 Sandbox protocol.** `Sandbox` protocol; `sandbox_cell.py` (a Virtual Cell with
  `network = none`, limits, read-only mounts; never a Real Cell); `sandbox_subprocess.py` (dev
  only). A Warden needing a sandbox files a `CellRequest`; the Queen provisions one for the Comb.
- [ ] **9.5 Quarantine Comb.** Gates in order: static checks with a banned-imports list, the
  tool's tests, schema round-trip, fuzz over the input schema, resource limits, capability audit.
  Emits a `QuarantineReport`.
- [ ] **9.6 Promotion flow.** request → scaffold → sandbox request → quarantine → (promote at
  scope | reject with report), a `tool.*` event per stage, one retry with the report.
- [ ] **9.7 Worker integration.** Workers load hive-scope tools plus their Cell's cell-scope tools
  at start and on `ToolPromoted`; `workers/tools/invoke.py` re-validates inputs, capabilities and
  OS before every call.
- [ ] **9.8 Honey link.** Every promotion deposits Nectar describing the tool.
- [ ] **9.9 Versioning and rollback.** Immutable versions; `retire`; `hive tools rollback`.
- [ ] **9.10 CLI.** `hive tools list|show|request|retire|rollback|quarantine <path>`.

### Exit criteria

- A Forager needing a missing capability triggers scaffold → sandbox → Comb → promote → the same
  Forager completing with the new tool, all on the trail.
- A Warden requests a cell-scope tool for its machine, gets a sandbox from the Queen, promotes it
  after the Comb passes, and only bees on that Cell can see it.
- A malicious scaffold (socket, `subprocess`, reads outside its dir) fails the Comb with the reason.
- The scaffold scenario passes with `SCAFFOLDER` local in the `local_llm` job.

### ADRs to write

- `tool-spec-comb-registry-and-scopes.md`.
- `quarantine-comb-gates-and-sandbox.md`.

---

## Phase 10: Guard Bees, Hive Entrance, auth and permissions

**Goal.** Every action in the Hive, including every lease and every grant, is authorised against an
explicit capability model, and the Hive has one door: an authenticated, versioned API (the Landing
Board) that only devices enrolled at the Hive Stand can use, with two-factor login bound to the
device, push for anything that needs the human, a human inbox, and remote exposure over a VPN. It
can then be driven from a phone, from another program, and by the dashboard.

**Depends on.** Phase 3, plus 6.5a for the voice route in 10.5f. Should land before phases 11 and 12.

**Deliverables.** `hivemind/guard`, `hivemind/entrance` (two listeners, enrolment, auth, push,
exposure, the Entrance Reducer), Guard Bee role, `docs/entrance/`, `hive entrance` and `hive
keys` CLI.

### Steps

- [ ] **10.1 Capability model.** Extends the `CapabilitySet` from 3.13a with every family:
  `tool:<name>`, `tool:scope:cell`,
  `net:<scope>`, `cell:virtual`, `cell:hive_stand`, `cell:real:<node>`, `cell:outside_scratch:<path>`,
  `cell:comb_shield:<tier>`, `exoskeleton`, `exoskeleton:real_display`,
  `tactic:write_like_human`, `tactic:mouse_like_human`,
  `honey:read:<scope>`, `honey:write`,
  `honey:clearance:<c0|c1|c2>`, `wax:propose`,
  `tool:request`, `llm:<slot>`, `warden:spawn`, `forage:request`, `question:human`,
  `observe`, `observe:thoughts`, `observe:honey:<scope>`, `entrance:submit`, `entrance:answer`,
  `entrance:push`, `entrance:steward`, `supersede`, `sting_cut`. `CapabilitySet` with `allows()`
  and `attenuate(subset)`; pure.
- [ ] **10.2 Policy engine.** `[guard]` manifest section (per-role default sets, deny lists,
  escalation rules), pure `evaluate` with a reason; denials are `guard.*` events.
- [ ] **10.3 Enforcement points.** Placement, lease creation, grant issue, Warden spawn, tool
  invocation, session calls outside scratch, Exoskeleton attach on a real display, Honey access,
  slot binding and rebinding, question routing to the human, Nuc promotion, device commands,
  tactic invocation, and Comb Shield egress policy activation. A test enumerates them and fails if a new state-changing action
  lacks one.
- [ ] **10.3a Night Veil guardrails.** Enforce at policy level that `cell:comb_shield:night_veil`
  implies `cell:virtual` and forbids `cell:real:*`; enforce that Night Veil model slot bindings are
  local-only; enforce that a Night Veil Cell's Waggle transport may reach the Hive Stand only
  through the Tor SOCKS proxy to its hidden-service address, never the VPN interface or the default
  route; and enforce that Night Veil capability
  sets allow `honey:clearance:c0` and `honey:clearance:c1` while denying any attempt to read or
  write `c2` Honey.
- [ ] **10.3b Tier inheritance enforcement.** Dispatch binds a task to the target Cell's
  `CombShieldLevel`; no runtime path may weaken controls after placement. A task moved between
  Cells is re-evaluated and re-bound to the new Cell's tier before resume.
- [ ] **10.3c Night Veil initiation policy.** Enforce that Night Veil placement may only be
  initiated by explicit human request through the inbox or API, never by autonomous escalation.
- [ ] **10.3d Night Veil location guardrails.** Enforce deny-by-default for location-sensitive
  capabilities on Night Veil Cells (`geo:*`, Wi-Fi scan, host metadata access), and reject task
  tool plans that request them.
- [ ] **10.4 Principals, the operator and device keys.** Principals: the human operator, Queen,
  Warden, Worker, Swarm device, and **enrolled client device**. Brood 1.0 has exactly one
  operator; the password is Argon2id-hashed in the Entrance tables and set at `hive init`.
  Clients never hold a shared API key: each enrolled device holds its own keypair, a WebAuthn
  passkey with user verification in browsers (and through the Android credential manager in the
  Capacitor build) or an Ed25519 key in secure storage for programs. Every Waggle-side principal
  keeps its Ed25519 keypair as before. Secrets are hashed or in the secret store; nothing in the
  manifest.
- [ ] **10.5 Hive Entrance.** `entrance/app.py` (composition root) running **two listeners**:
  loopback on `[entrance] bind`, always on, and a remote listener on `remote_bind` only when
  exposed (10.5a). `entrance/routes/` with one file per resource under `/v1/` (`goals`, `tasks`,
  `cells`, `wardens`, `forage`, `inbox`, `chat`, `episodes`, `tools`, `honey`, `trail`, `swarm`,
  `llm`, `devices`); every route declares which listeners serve it, and the loopback-only set
  (approve, deny, unlock, widen capabilities, reopen, operator add) is asserted by the route test
  from coding rules 8.11, where a loopback-only route on the remote listener is a 404 and not a
  403. `entrance/streams/` with one live WebSocket stream per view: trail events, telemetry per
  bee, Forage ledger deltas, task graph deltas per principal (the Queen and every Warden), episode
  records, Cell status, Entrance security events. The **human inbox and chat**: free-text
  messages from the human enter the Queen's inbox as `HumanMessage` items the Attendant scores;
  the Queen's replies, her questions, and Alarms that reached her come back on the same channel,
  so the chat is the human end of the inbox rather than a separate path into the system.
  `entrance/landing_board.py` generates the OpenAPI document from the route models, marking
  loopback-only routes; it is committed as `docs/entrance/openapi.json` and CI fails when the
  generated document differs. The Entrance also serves `packages/observation-web/`'s build as
  static files.
- [ ] **10.5a Remote exposure.** `entrance/expose.py` and the `[entrance]` manifest section
  (coding rules 13): `expose = "loopback" | "vpn" | "lan" | "tunnel"`, `remote_bind`,
  `public_url`, `tls`, `mutual_tls`, `rate_limit_per_device`. `loopback` is the default and the
  loopback listener never goes away. `vpn` is the recommended remote path: the remote listener
  binds to the overlay interface (WireGuard or Tailscale, chosen in the ADR) and nothing else, so
  unauthenticated packets never reach the Entrance. `lan` and `tunnel` require TLS and mutual
  TLS with the device certificate on top of login, and refuse to start without both; `tunnel`
  runs the tunnel client as a supervised child of the Entrance. There is no `public` value.
  Per-device rate limiting, CORS only for `public_url`. A test starts the Entrance in every mode
  and asserts every refusal.
- [ ] **10.5b Push channel.** `entrance/push/`: `PushChannel` protocol; `websocket.py` for live
  clients, `webhook.py` (signed with the Hive key, retried with backoff, idempotent by event id),
  `web_push.py` (VAPID; keys from `HIVEMIND_ENTRANCE_VAPID_*`), and the native channel the
  Android build registers through (12.12). Subscriptions are per device, filtered by capability,
  persisted, and re-validated on start. Pushed: a question for the human, an Alarm that reached
  the human, a reply from the Queen, completion of a goal the device submitted, every Entrance
  security event. Payloads say only that something is waiting, never the content. A question
  answered on any device is withdrawn from every other; a test asks from the CLI, answers by
  webhook, and asserts the web-push copy is withdrawn.
- [ ] **10.5c Landing Board contract and client guide.** `docs/entrance/landing-board.md`: the
  three calls a client needs (submit a goal, subscribe, answer) with `curl` examples, the
  enrolment flow from the device's side, the push contract, and the versioning rule (additive
  within `/v1/`, breaking means `/v2/` with `/v1/` kept for one Brood). A conformance test drives
  the Entrance through the committed OpenAPI document with a generic client and nothing else, so
  a third-party program written from the document alone is known to work. A Python client
  package is post-1.0.
- [ ] **10.5d Device enrolment, approved at the Hive Stand.** `entrance/enrol/`: `hive entrance
  invite --device "phone"` on loopback mints a short-lived, single-use invite shown as a code and
  a QR; the device opens the Entrance, presents the invite, generates its keypair and sends the
  public key with a self-description; the request lands in a pending table and is pushed to every
  enrolled device as "a device is asking to join"; `hive entrance approve <id> --capabilities ...
  --spend-cap ... --expires ...`, or the Observation Hive on loopback, approves it; `deny` refuses
  it; pending requests expire. Approval, denial, unlock, capability widening and revocation are
  loopback-only routes. `[entrance] steward_devices` (off by default) lets a device flagged
  `entrance:steward` approve after full step-up, and nothing else can. The device state machine
  (`INVITED → PENDING → APPROVED`, `PENDING → DENIED | EXPIRED`, `APPROVED ↔ LOCKED`,
  `→ REVOKED`) lives in `entrance/enrol/state.py` (Appendix C); every edge is a
  `guard.entrance.*` event.
- [ ] **10.5e Login, sessions, step-up and the Entrance Reducer.** `entrance/auth/`: login is
  the device key (a passkey assertion with user verification, or a signed challenge) plus the
  operator password; sessions carry `session_ttl_hours` and `idle_timeout_minutes` and are bound
  to the device key (a token presented without a matching device signature is refused); step-up
  re-runs both factors, is valid for `step_up_window_minutes`, and is required for spend above
  `step_up_spend`, key and capability changes, Supersedure, Sting Cut, Absconding and reopening;
  break-glass actions add the typed confirmation phrase on every path. Lockout after
  `lockout_attempts` failures moves the device to `LOCKED` until a loopback unlock; rate limits
  per device and per address. `entrance/reducer.py`: `hive entrance reduce` drops the Entrance to
  loopback only and revokes every remote session; a Guard Bee autopilot rule does the same on
  failure bursts, cross-device lockouts, or an unknown client hammering the invite route;
  `hive entrance open` on loopback with step-up reopens. `travel_lock` (off by default) forces
  step-up and a notification when a known device appears from a new network and never approves
  anything. Tests: a stolen session token without the device key is refused; an approve request
  on the remote listener is a 404; the Reducer closes a live WebSocket subscription within a
  second.
- [ ] **10.5f Voice in at the Landing Board.** `entrance/voice.py`: `POST /v1/chat/audio` for
  clips and an audio frame type on the chat WebSocket for push-to-talk, both from enrolled devices
  only; transcription on `ModelSlot.TRANSCRIBER` through 6.5a, so the Hive Stand's own Whisper
  serves it by default; the transcript becomes a `HumanMessage` in the Queen's inbox. A spoken
  goal is echoed back to the device for confirmation before it is submitted
  (`[entrance.voice] confirm_goals`, on by default), so a misheard sentence never spends
  anything; answers and chat go straight through. Audio and transcript are `C2`; audio is
  discarded after transcription unless `keep_audio` is set, in which case it is Nectar with a
  retention window; `max_clip_seconds` caps a clip and the rate limiter counts audio seconds per
  device. Tests with fixture clips through `FakeTranscription`; a clip from a pending or revoked
  device is refused before any model runs.
- [ ] **10.6 Guard Bee role.** Watches the trail for denial rates, out-of-scratch touches,
  over-grant spend, unexpected network attempts, Capping rejection and rollback rates, failed
  sampled audits, and Entrance events (failure bursts, lockouts, invite-route abuse, travel-lock
  triggers); can ask the Queen to quarantine a bee or raise a tier's audit rate, and may trigger
  the Entrance Reducer itself by autopilot rule, since narrowing access is always safe;
  `guard.alert`.
- [ ] **10.7 Access levels.** `guard/access.py` (3.13a) grows the full permission data for
  `AccessLevel` (2.3a) on every Real Cell (`read_only`, `scratch`, `full`), stored with the node
  and the lease and shown in the UI. Virtual Cells are
  always `full`. The Pollen Packet requests `full` at enrolment by default; the operator may grant
  less, and the level caps every capability set issued for that Cell. `watch:<node>` capabilities
  bound what watch mode (11.10) may observe: `read_only` allows process list, resource use, logs
  in allowed roots and file-change events in allowed roots; screen or input capture is never part
  of watch mode and needs an explicit, separately granted capability.
- [ ] **10.8 CLI.** `hive entrance invite|pending|approve|deny|devices|revoke|steward|
  reduce|open|status|expose|operator add`; `hive keys create|revoke|list` for Waggle-side
  principals; `hive run --remote`, `hive inbox --remote` (the CLI on a laptop is an enrolled
  device like any other).

### Exit criteria

- A Drone without `net:*` is denied `http_get` with the reason on the trail; a task without
  `cell:hive_stand` lands on a Virtual Cell even under `prefer = "real"`; a Warden without
  `forage:request` cannot ask for more.
- `hive run --remote` and `hive inbox --remote` work against `hive serve` from an enrolled
  laptop; unauthenticated requests, and requests from a device that is pending, locked or revoked,
  are rejected.
- A phone on a different network joins the VPN, enrols by QR, waits pending until approved on
  the Hive Stand, then logs in with passkey plus password, submits a goal, receives the Queen's
  question by web push and answers it; from the remote listener the same phone gets a 404 on the
  approve route.
- A program using only `docs/entrance/openapi.json` and its enrolled Ed25519 key submits a goal
  and receives the question by signed webhook; the same key is refused a route its capabilities
  do not cover, and a goal above its spend cap is refused pending step-up.
- Five bad passwords lock a device until a loopback unlock; `hive entrance reduce` closes every
  remote session; `expose = "lan"` without mutual TLS refuses to start.
- A goal spoken into the phone is transcribed on the Hive Stand's local Whisper, echoed back for
  confirmation, confirmed, and runs; the trail shows one `llm.call` on `TRANSCRIBER` and no audio
  bytes anywhere. A spoken answer to a waiting question resumes the task without a confirmation
  step.

### ADRs to write

- `capability-model-attenuation-and-enforcement-points.md`.
- `hive-entrance-http-websocket-api-and-human-inbox.md`.
- `landing-board-enrolment-two-factor-login-and-exposure.md` (one operator; devices enrolled and
  approved on loopback only; device-bound sessions and step-up; VPN overlay choice; mutual TLS for
  LAN and tunnel; the Entrance Reducer; push payloads carry no content).
- `landing-board-versioning-and-push.md`.

---

## Phase 11: The Swarm: Pollen Packet gateways, Wardens for devices, and Nucs

**Goal.** External devices join the Hive as Real Cells through a thin gateway. Each gets a Warden
on the Hive Stand that drives it over a signed terminal session. A capable device can be promoted
to a Nuc, with the Warden and a model server on the device, and then keeps working when the link
to the Hive Stand is lost.

**Depends on.** Phases 1, 8 and 10.

**Deliverables.** `packages/pollen`, `hivemind/swarm`, `PollenSession`, `wardens/offline/`, Nuc
promotion, `hive swarm` CLI.

### Steps

- [ ] **11.1 Enrolment protocol.** `docs/waggle/enrolment.md`: one-time token from `hive swarm
  invite`; the packet presents it with its public key, its `CellCapabilities` and its
  `ForageCapacity`; the Queen replies with the Hive's key and the node's capabilities. Tokens
  expire and are single use.
- [ ] **11.2 Swarm registry and source.** `SwarmNode`, `NodeStatus`, SQLite registry, enrolment,
  heartbeat, and `SwarmSource` (a `RealCellSource` whose `lease()` and `release()` round-trip with
  the device and wait for restore confirmation).
- [ ] **11.3 Pollen gateway.** `pollen/agent/packet.py`: connect out, heartbeat, verify signatures,
  check every request against the node's capabilities locally, hand `session.*` and `lease` traffic
  to the executors, keep an outbox. It persists the Queen's address from enrolment and replaces it
  only on a `QueenMoved` signed by the Hive key (13.2a). No supervision logic; no model client.
- [ ] **11.4 Device-side lease, executors and dead-man switch.** `pollen/lease/` (scratch dir,
  started pids, restore; **dead-man**: when the link is lost longer than the enrolled limit and no
  Warden runs on the device, kill what the lease started and release), `pollen/executors/session.py`
  (persistent shell with streaming, argument lists only), `files.py`, `info.py`.
- [ ] **11.5 PollenSession.** `swarm/session.py`: the Hive Stand side `CellSession` over the
  `session.*` messages. Passes the `CellSession` contract suite against a packet in a container.
- [ ] **11.6 Level 0: a Warden per device, on the Hive Stand.** Enrolment starts a Warden on
  the Hive Stand bound to the device through `PollenSession`, with a shared grant and a local
  pool consisting of the device's host compute for sub-bee slots (their brains run beside the
  Warden on the Hive Stand; only their hands are on the device). Placement sees the device; the
  Undertaker releases its leases. Documented limit: remote exec latency; no autonomy when the
  link drops.
- [ ] **11.6a Level 1: the Warden moves onto the device (colonization).** `swarm/level.py` +
  `pollen/bootstrap/`:
  when the Queen decides the device should carry its own Warden (its Forage suffices and the Hive
  Stand needs relief, or the task needs to survive short outages), the packet installs the
  `hivemind` runtime, the Hive Stand Warden checkpoints and hands off, a Warden starts on the
  device and resumes from the Handoff, its sub-bees are respawned beside it, and the Queen sets
  its `Ceilings`. Models still come from the hosting plan's shared sources. `warden.migrated`.
  The Warden is always the first bee to move; a device never runs sub-bees without their Warden.
- [ ] **11.7 Platform shims and packaging.** `pollen/platform/{linux,windows,macos}.py` (any
  Linux distro through generic systemd and XDG paths, with Arch and Ubuntu tested; Windows; macOS);
  `pollen install|uninstall`; single-file builds in CI for Linux x86-64 and ARM64 (Raspberry Pi),
  Windows x86-64, macOS ARM64 and x86-64. The packet reports OS, distro, arch, package manager,
  shell and Python availability at enrolment, and that report is what every later decision about
  the device reads: placement, tool eligibility, scaffolding targets, Nuc eligibility.
- [ ] **11.7a Gateway contract for other runtimes.** `docs/waggle/gateway-contract.md`: the
  minimal message set a Pollen-equivalent must implement (enrol, heartbeat, capability report,
  lease open and release, the `session.*` family, outbox replay, signature verification), with
  the conformance suite from 1.9 runnable against any implementation over WebSocket. This is the
  path for devices the Python packet cannot reach, such as phones and constrained IoT boards: a
  small compiled gateway speaking the same contract, written after Brood 1.0. The protocol is the
  product; the Python packet is its first implementation.
- [ ] **11.8 Level 2: Nuc promotion.** `swarm/nuc.py`: on a Level 1 device the Queen decides
  from the device's free VRAM, the Hive Stand's seat pressure and the tasks' autonomy needs; she
  writes a hosting plan with local sources first, sets the model ceilings, and sends `NucPromote`.
  The device's Warden starts a model server through 8.3, loads allowlisted models within its
  ceilings, and reports the new sources; from then on its local pool includes seats. Demotion is
  the reverse. Guard-gated. A device cannot go straight from Level 0 to Level 2; the Warden moves
  first.
- [ ] **11.8a Real Cell Comb Shield is operator-set.** `swarm/enrolment.py` records the tier the
  operator chose at enrolment, Meadow or Propolis, changed only by `hive swarm shield <node>`;
  placement never changes a Real Cell's tier. For Propolis the Warden verifies the device's own
  VPN before each lease (default route via the tunnel, DNS through it, direct egress denied) and
  refuses to place otherwise; HiveMind never installs, starts or reroutes a VPN on a Real Cell.
  Night Veil requests are denied on Real Cells and redirected to Virtual placement.
- [ ] **11.9 Offline Wardens.** `wardens/offline/`: on link loss a device Warden keeps working
  with what it owns. A Nuc keeps its sub-bees running on its local pool, and shared grants are
  frozen; a Level 1 Warden clusters its sub-bees at once, since it has no models to think with, and
  keeps them warm. Both queue results, Alarms and any `ForageRequest` in the outbox, write a local
  trail segment, retry the link with backoff, refuse anything that needs the Queen (new Cells,
  shared Forage, human-bound questions), and run full Clustering past the offline limit. On
  reconnection: outbox replay, `TrailSegmentSync`, grant and ceiling reconciliation, local pool
  report, `warden.reconnected`. The Queen's side: mark unreachable, hold the node's tasks for a
  grace period, spawn the diagnostic Drone below, then decide.
- [ ] **11.9a Active reconnection: a diagnostic Drone on both ends.** Retry-with-backoff (11.9) is
  the floor for every offline Cell, but wherever a model is actually reachable to do the thinking,
  the Warden does not just wait on it. A Nuc's Warden (it has its own model server) spawns a
  short-lived **Drone** from its own local pool — no Cell, Forage or permission needed from the
  Queen, since a local pool is already owned outright, 8.10 — with the objective "diagnose and, if
  possible, restore the link." On the Hive Stand side the same thing happens in reverse: for a
  still-Level-0 device its Warden is still resident on the Hive Stand and notices the drop itself;
  for a colonized device whose own Warden has migrated away, the Hive Stand's own Warden (`W0`)
  picks it up on the Queen's behalf and spawns a Drone on the Hive Stand's local pool to check
  reachability from this end (DNS/route to the device, whether other Swarm nodes are affected too,
  ruling out "my network" versus "that device"). A Level 1 Warden with no local model skips this
  entirely and clusters at once exactly as 11.9 already says, because it has nothing to think with.
  The Drone runs ordinary Capping-gated scratch work through its `CellSession` (`run_command`
  diagnostics, one-off scripts written to scratch) and never touches the Royal Jelly Lab or the
  Quarantine Comb, since nothing here is being promoted as a reusable tool for other bees. It is
  bounded by a `[supervision] reconnect_budget` (attempts and wall-clock) carved out of, not added
  to, the existing offline limit, so Clustering always still has the same deadline as a backstop.
  Whatever it tries is still capped by the Cell's access level; being cut off never grants more
  access than being connected would. It reports back to its Warden — fixed, diagnosed but can't
  fix, or no cause found — and deposits its findings as Nectar either way, then 11.9's existing
  timeline resumes untouched.
- [ ] **11.10 Watch mode and the hourly Patrol.** Real Cells stay enrolled between sessions, so
  their Wardens are long-lived. `wardens/watch/`: when a Real Cell has no active bees its Warden
  enters `WATCH`, a read-only autopilot state that observes within the Cell's access level
  (process list, resource use, logs and file-change events in allowed roots, network counters)
  and writes observations to Bee Bread with a retention window. On the manifest's Patrol interval
  (initially hourly) the Warden runs one awake episode over the observations since the last
  Patrol, and either raises an Alarm, deposits a Nectar summary, proposes Cell Wax for the device
  (a disk nearly full, a service that keeps restarting), or records `warden.patrol` with "nothing
  notable" and discards. Watch mode never writes to the device, never captures
  screen or input, and is visible in the UI as the Cell's mode. Applies to the Hive Stand as well.
  A bee assignment moves the Warden back to `ACTIVE`.
- [ ] **11.11 Exoskeleton on devices.** Through `PollenSession` the X11 backends from phase 6 work
  on a Linux device unchanged; on a Nuc they run locally under the device's Warden.
- [ ] **11.12 CLI.** `hive swarm invite|list|revoke|promote <node>|demote <node>|access <node>
  <level>|shield <node> meadow|propolis|run <node> "<cmd>"`; `hive cells list` shows devices as `REAL` with Warden location
  (`hive_stand` or `nuc`), access level and mode (`ACTIVE` or `WATCH`).

### Exit criteria

- Enrol a second machine; `hive cells list` shows it `REAL` with its Warden on the Hive Stand; a
  Drone placed on it completes; the device's scratch dir is gone and its process table matches the
  pre-lease snapshot.
- Move its Warden onto it (Level 1) without the running task failing; then promote it to a Nuc;
  cut the network between the two machines mid-task: the task completes on the device using only
  its local pool, a deliberately high-bar subtask that needs a shared source waits in the outbox
  rather than failing, the trail segment and results arrive after reconnection, and nothing is
  duplicated. The Nuc's Warden loads an allowlisted model within its ceiling with no request on
  the trail, and is refused when it tries one outside the allowlist.
- A Night Veil task is always placed on a Virtual Cell; any attempt to place it on a Real Cell is
  denied. On that Virtual Cell, placement is blocked until OpenVPN and Tor are both
  healthy, Tor Browser is present, the Hive Stand's Waggle hidden service is reachable through Tor,
  direct egress is blocked, and leak checks pass. The task may
  read Wildflower (C0) and Apiary (C1) Honey but cannot read or write Royal (C2); any attempt is
  denied.
- A Night Veil Cell's Waggle heartbeats, Alarms and results are confirmed to transit only Tor to
  the Hive Stand's hidden service: a network capture on the host during the scenario shows no
  Waggle traffic to any clearnet address, on the VPN interface or otherwise, and killing the Tor
  daemon while leaving the VPN and default route up clusters the Cell instead of falling back to a
  direct or VPN-only connection.
- A Night Veil Cell is location-blind by attestation: geolocation APIs denied, metadata endpoints
  unreachable, UTC timezone enforced, fixed locale enforced, randomized hostname confirmed, and
  WebRTC local-IP leak tests blocked before scheduling.
- A Propolis task placed on a Real Cell enrolled as Propolis is blocked until the device's own
  OpenVPN tunnel is verified healthy, VPN-only egress checks pass, and direct non-VPN egress is
  denied and trailed; HiveMind changes nothing on the device to get there, and the left-as-found
  snapshot holds. On a Virtual Cell the same checks run against the image, with Tor disabled for
  that tier.
- Cut the network to a non-Nuc device: the dead-man switch releases the lease and the Queen's
  Warden raises an Alarm.
- Leave an enrolled device idle overnight: its Warden sits in `WATCH`, the device shows no writes
  from HiveMind, the Patrol runs on schedule, and a planted anomaly (a new process eating CPU)
  surfaces as an Alarm at the next Patrol while an uneventful hour records "nothing notable".
- A replayed or tampered session message is rejected; `pollen` installs on a clean Raspberry Pi OS.
- Cut a Nuc's link with a planted, reversible problem (e.g. a wrong resolver entry) within reach of
  its access level: its diagnostic Drone finds and fixes it inside the `reconnect_budget`, and the
  task completes with no Queen or human involvement. Cut it with an unfixable problem (egress
  genuinely gone): the Drone gives up inside budget and falls through to 11.9's existing Clustering
  path unchanged. Symmetrically, unplug a colonized device: `W0` on the Hive Stand spawns its own
  diagnostic Drone and deposits a Nectar finding before the grace period's existing "then decide"
  fires.

### ADRs to write

- `pollen-packet-is-a-gateway-and-the-trust-model.md`.
- `runtime-ladder-warden-moves-first.md` (Levels 0, 1 and 2; co-location of a Warden and its
  sub-bees; why the Warden is the first bee onto a device).
- `nucs-warden-migration-and-offline-operation.md`.
- `access-levels-watch-mode-and-the-patrol.md` (what watch mode may observe per level; never
  screen or input; Patrol cadence; retention).
- `active-reconnection-diagnostic-drones.md` (why a Drone, not a new role; the reconnect budget
  is carved out of the offline limit, never added to it; why this never touches the Royal Jelly
  Lab; who investigates on the Hive Stand side once a device's own Warden has migrated away).

---

## Phase 12: Observation Hive (the UI)

**Goal.** One place to watch and talk to the Hive. Everything is live and everything is readable:
the Queen's thinking and any bee's thinking, every Cell and what it is doing, where its models run,
how Forage is divided, what the Queen and each Warden are working on, and the Hive's knowledge as a
browsable tree. The UI is read-only except for one path: the chat and answer channel into the
Queen's inbox. No view writes to any store directly.

**Depends on.** Phases 2, 7 and 10. Every view reads the streams and read API from 10.5; nothing
in this phase adds a new write path.

### Steps

- [ ] **12.1 Read API and metrics.** `observation/api.py`: aggregated views for every screen
  below, paginated trail and episode queries, all behind `observe` with `observe:thoughts` and
  `observe:honey:<scope>` for the sensitive ones. `observation/metrics.py`: Cells by kind and
  status, Wardens by location, grants and headroom, Alarms by kind and level, awake episodes per
  hour per principal, tasks by status, tool promotions, denials, tokens and cost per task per slot
  per provider; Prometheus endpoint. Add first-class counters for total active bees, active versus
  inactive Cells, Real versus Virtual Cell totals, and current LLM versus autopilot activity.
- [ ] **12.2 Shell and live plumbing.** `packages/observation-web/` (scaffolded in 0.1):
  TypeScript (strict) + React, built with Vite and served by the Entrance as static files;
  `src/landing_board/` holds the TS types generated from `docs/entrance/openapi.json` (CI
  regenerates and fails on a diff) and the one client module (login with passkey plus password,
  session, streams, push registration); one directory per view below, all fed by the Entrance
  streams so nothing needs a refresh; responsive from phone width up; installable as a PWA with a
  manifest and service worker the Entrance serves. Layout and colour follow
  `docs/observation/design.md`, written in this step; the supervision tree, Cell diagrams and
  Forage diagram are React components rendering live SVG. The shell includes
  a persistent summary strip for the core counters: active bees, active/inactive Cells,
  Real/Virtual split, and LLM/autopilot split.
- [ ] **12.3 Thoughts view.** The Queen's thinking as it happens: the Attendant's current ordering
  of her inbox with scores, each autopilot decision and the rule that fired, and each awake
  episode's trigger, assembled context (by section, expandable), reasoning summary where the
  provider exposes one, decision and action. The same view opens for **any bee** from the fleet
  view, with full read access to its episode records and telemetry, and a "follow" mode that
  streams new episodes as they happen. Backed by `memory/episodes.py` (3.14) and the episode stream
  (10.5). Secrets and screenshot bytes are redacted at the source, not in the UI.
- [ ] **12.4 Cell pages and diagrams.** One page per Cell with a live diagram: its Warden and
  where it runs (Hive Stand or on the Cell), every sub-bee with role, status, what it is doing
  right now and a context gauge, the session, whether an Exoskeleton is attached, the lease or
  Virtual Cell status, the model hosting for its bees. Alongside the diagram: the Cell's current
  tasks and goals, the Forage the Warden holds (grant used against issued, pending requests), the
  Honey the Warden can see (a scoped entry point into the Honey browser), the Cell's
  `CombShieldLevel`, Pheromone Mask state (`OFF`, `WARDEN`, `QUEEN_FORCED`), the Cell's access level and current mode (`ACTIVE` or `WATCH`, with the last
  Patrol's report), its Cell Wax with severity, proposer and expiry, open Alarms, and Capping
  activity (proposals in flight, verdicts, rollbacks)
  with flight-recorder playback for
  Exoskeleton actions. Redrawn from telemetry and Cell status deltas.
  `CombShieldLevel` is shown in the page header as a persistent, high-contrast badge.
- [ ] **12.5 Forage view.** A live diagram of total capacity and how it is divided: per host
  (Hive Stand, each Nuc, each Virtual Cell backend) the cores, memory, GPU and model seats in use
  versus available, the Royal Reserve held back, and a flow from the Queen's shared pool to each
  Warden's grant to each sub-bee's spend, plus hosted spend against cost caps. Each Nuc and Level 1
  device shown with its local pool beside the shared flow: what its Warden owns, its ceilings, its
  local reserve, seats it has exported, and spills from local to shared over time. The Forage map as
  a table: every source with grade, measured grade, distance from each Cell, abundance and cost.
  The Fanner's queues per binding with waiting requests and their tempo. Pending `ForageRequest`s
  with their reasons and the Queen's decision when it lands, and grants nearing expiry.
- [ ] **12.6 Fleet list.** Every Cell in one table with a **Real / Virtual / All** filter: kind,
  source, status and mode, `CombShieldLevel`, Pheromone Mask state, access level, two badges (where the Warden runs: Hive Stand or on the
  Cell; where its models come from: local, Hive Stand, hosted, or hybrid per its hosting plan),
  current task and role, open Alarms, a Cell Wax indicator showing the highest live severity, lease
  age, and links into its page, its Warden's Attendant view and its bees' thoughts. `CombShieldLevel` appears as a persistent, high-contrast badge in
  every row, and the view includes a fixed legend for Meadow, Propolis, and Night Veil.
- [ ] **12.7 Attendant views.** For the Queen: the task graph she is currently concerned with,
  what recently finished, what is coming next, and the ordered inbox behind it, with each task's
  placement and Alarms. The same view for **every Warden** over its sub-bees' tasks and its own
  inbox, reachable from the fleet list and the Cell diagram. Both read the per-principal task graph
  stream from 10.5.
- [ ] **12.8 Chat.** A chatbox for the human: request tasks, ask questions, answer the Queen's
  questions, resolve Alarms that reached the human, typed or spoken (a push-to-talk button using
  the browser's recorder, sent through 10.5f, with the transcript shown for confirmation when the
  message is a goal). Messages go into the Queen's inbox through the chat route (10.5) and the
  Queen's side of the conversation is her replies, questions and Alarms.
  This is the only write path in the UI. Each message links to the tasks and episodes it produced.
  Any break-glass Absconding path in UI requires password re-auth, a typed confirmation phrase,
  and a final scope review; no one-click trigger is permitted.
- [ ] **12.8a Enrolment and step-up screens.** On loopback only: the invite screen (code and QR),
  the pending list, approve with name, capabilities, spend cap and expiry, deny, unlock, revoke,
  and the Entrance Reducer with its reopen flow. On every device: the join screen a new device
  lands on when it opens the Entrance with an invite, the passkey login, the step-up prompt, and
  the device list showing where each enrolled device last connected from. The remote build never
  renders the loopback-only screens, and the route test proves it could not use them anyway.
- [ ] **12.9 Honey browser.** The Hive's knowledge as a folder tree from `honey_store/browse.py`
  (7.10): the main store under `/hive`, then what each Cell and each bee can see under `/cells`
  and `/bees` (each Cell's folder includes its live Cell Wax), task folders, and Bee Bread.
  Read-only navigation, reading and search per folder, provenance and `HoneyClearance` on every
  item, and a "propose a note" action that sends a message to the Queen rather than writing (a
  Honey note, or Cell Wax when made from a Cell's folder). Scope and clearance filtering match the viewer's
  capabilities.
- [ ] **12.10 Cost view.** Per-goal spend by slot and provider, and what moving a slot local would
  save, from normalised `Usage` on the trail.
- [ ] **12.11 Capping view.** Hive-wide queue of proposals by tier and state, judge verdicts with
  reasons, rollbacks, sampled-audit findings and rates per tier, and a link from every item to the
  bee's thoughts and, for Exoskeleton actions, the recording.
- [ ] **12.12 Android client.** `packages/observation-web/android/`: a Capacitor project wrapping
  the same React app as an APK. Native push through the channel chosen in the ADR (FCM or
  UnifiedPush) registered with 10.5b, passkeys through the Android credential manager, the
  share sheet as an intake so any app on the phone (a glasses companion app included) can share
  text, an image or an audio clip into the Hive as a `HumanMessage` or Nectar labelled `C2`, a
  push-to-talk button that streams to 10.5f, and a foreground notification while a question is
  waiting. The phone is an enrolled device like any other; the
  APK is built in CI and is a release artifact from phase 14.

### Exit criteria

- Running the phase 6 or 9 scenario with the Observation Hive open shows Cells appearing in the
  fleet list and diagrams, grants flowing in the Forage view, the Queen's and a Forager's thoughts
  streaming, the task graph moving in the Queen's and the Cell's Attendant views, and the trail
  scrolling, with no manual refresh.
- During those scenarios, the summary strip updates live for active bee count, active/inactive
  Cell counts, Real/Virtual Cell counts, and LLM/autopilot activity, and matches the underlying
  telemetry stream.
- In both scenarios, every visible Cell shows `CombShieldLevel` as a clear badge in Fleet and Cell
  page header, and operators can distinguish Meadow, Propolis, and Night Veil without opening a
  detail panel.
- In both scenarios, every visible Cell shows Pheromone Mask state as a clear badge in Fleet and
  Cell page header, and operators can tell `OFF`, `WARDEN`, and `QUEEN_FORCED` without opening a
  detail panel.
- A task requested from the chatbox runs end to end, its question comes back in the same chatbox,
  and the answer resumes it.
- The Honey browser shows the shared store and a per-Cell view that differ exactly as the
  viewer's scopes say; no request from the UI ever hits a write endpoint other than chat.
- The same UI opens on a phone over the VPN from 10.5a, installs as a PWA, and the summary strip,
  the fleet list and the chat work there; the Android APK enrols by QR, logs in with a passkey,
  receives a push while in the background, and answers the question; text shared from another app
  arrives in the Queen's inbox.
- The loopback-only screens render only on the Hive Stand, and `pnpm lint`, `tsc --noEmit` and
  `vitest` are green with the generated types matching the committed OpenAPI document.

### ADRs to write

- `observation-hive-dashboard-stack.md` (TypeScript + React + Vite; state and stream handling;
  why one codebase serves the page, the PWA and the APK).
- `android-client-capacitor-and-push-delivery.md` (Capacitor over the React app; FCM or
  UnifiedPush; passkeys through the credential manager; the share sheet as intake).

---

## Phase 13: Resilience (Requeening, Supersedure, Swarming, Absconding, Overwintering at scale)

**Goal.** The Hive survives the Queen dying, can move its Hive Stand to another machine without
losing a task, scales up and down under load, and can always be torn down cleanly, releasing every
borrowed device and revoking every grant.

**Depends on.** Phases 5, 10 and 11 (Supersedure needs a colonized device to move to).

### Steps

- [ ] **13.1 Requeening.** `queen/requeening/recover.py`: rebuild from the Brood Chamber and trail,
  reconcile with every backend and `RealCellSource`, reattach to live Wardens, replay their
  outboxes, reconcile grants against the ledger, release leases whose Warden is gone, reassign
  orphaned tasks, resume `PAUSED` tasks from Handoffs, `queen.requeened`. e2e kills the Queen with
  tasks on the Hive Stand, in a container and on a Nuc; all finish.
- [ ] **13.2 Queen state snapshots.** The Queen's own periodic Handoff, so recovery is bounded;
  `docs/runbooks/requeening.md`.
- [ ] **13.2a Supersedure: moving the Hive Stand.** `queen/supersedure/` (coding rules 8.16):
  `preflight.py` (the candidate must be a colonized Real Cell, Level 1 or a Nuc, with `FULL`
  access, the same runtime version, disk for the stores plus headroom, Forage covering the Royal
  Reserve, reachability from every Swarm node and every Virtual Cell backend, the Hive's secrets
  present in its secret store, clock skew within bound; surfaced as `hive doctor --supersedure
  <node>`), `copy.py` (cluster every provider with 4.9, write `queen.supersedure_started`, `hive
  backup` streamed over the candidate's `CellSession`, `hive restore` on the far side, checksums;
  the Hive keypair moves through the secret store, never over Waggle; the Entrance tables,
  enrolled devices included, move with the stores), `handover.py` (a `QueenMoved` signed with the
  Hive key to every Warden, Pollen Packet and enrolled client device: new address, effective at,
  grace until; both addresses honoured during the grace window; the new Queen starts in
  `REQUEENING` through 13.1 from the copy and resumes every paused bee; the old Queen enters
  `SUPERSEDED` and stops; its machine keeps its Warden and is enrolled as a Swarm node in `WATCH`),
  `rollback.py` (if the candidate is not ready before the grace window ends, the old Queen wakes,
  sends a `QueenMoved` pointing back, records `queen.supersedure_aborted`). Invariant, tested:
  never two Queens running. The old Queen never leaves `CLUSTERED` once the copy starts and on any
  restart stays put until it finds the outcome of the unfinished `queen.supersedure_started` in
  its own trail; the new Queen leaves `REQUEENING` only on an acknowledged `SUPERSEDED` or an
  expired grace window with the old Queen unreachable. `hive supersede <node>` is human-initiated
  on loopback with step-up and confirmation, never an autopilot rule or an awake decision.
  `docs/runbooks/supersedure.md`.
- [ ] **13.3 Swarming policy.** `queen/scheduler/swarming.py`: pure policy for provisioning more
  Virtual Cells from queue depth and Forage headroom, per-backend limits, cost caps.
- [ ] **13.4 Absconding.** `hive abscond`: Virtual Cells, dormant Cells, every lease on the Hive
  Stand and on devices, every grant, Worker processes, temp dirs; from labels and the trail alone.
  Absconding is human-only: callable only by a human principal, never by Queen, Warden, Worker,
  autopilot rule, or tool. Execution requires password re-auth, an explicit typed confirmation
  phrase, and a final scope review prompt. Records actor, reason, and confirmation evidence in the
  trail. Persistent stores are not wiped by default.
- [ ] **13.4a Sting Cut (per-Cell emergency disconnect).** `hive cells sting-cut <lease|cell>`:
  immediate lease revocation for one Cell, session key/token invalidation, termination of
  Hive-started processes for that lease, idempotent scratch cleanup, and deletion of lease-local
  volatile traces (ephemeral caches, temp artifacts, transient credentials) per policy. Records
  `cell.sting_cut` with reason and actor in the trail and leaves central audit state intact for
  Meadow and Propolis. For Night Veil, retained logs and persisted records are destroyed at
  teardown.
- [ ] **13.5 Overwintering at scale.** QEMU snapshots and cloud stop; disk accounting; eviction.
- [ ] **13.6 Chaos tests.** Kill Cells, drop links, drop a Nuc mid-task, corrupt replies, return
  refusals, malformed JSON and rate limits from the fake, take a provider down mid-run and assert
  Clustering then resume, flood the inbox, exhaust a grant, kill the old Queen mid-Supersedure,
  kill the candidate before it is ready, hammer the Entrance's invite route from an unknown
  client and assert the Reducer fires. Every goal finishes or fails
  explicitly; nothing hangs; no lease or grant is left open.
- [ ] **13.7 Backups.** `hive backup` / `hive restore`; `docs/runbooks/backup.md`.

### Exit criteria

- Chaos suite green nightly, including no-lease-left-open and no-grant-left-open assertions.
- A 20-subtask goal with `max_parallel = 8` swarms to 8 Virtual Cells and scales to zero; the same
  goal under `prefer = "real"` with two devices spreads leases within their grants.
- A non-human principal (Queen/Warden/Worker/tool) cannot execute `hive abscond`; policy tests
  assert denial. Human flow requires password re-auth and typed confirmation phrase.
- Supersede the Hive Stand onto an enrolled Nuc while a goal is running with tasks on the Hive
  Stand, in a container and on the Nuc itself: every task finishes with no duplicated work, every
  device and every enrolled phone reconnects to the new address, the old machine appears in `hive
  cells list` as a Real Cell with its Warden in `WATCH`, and the trail has exactly one
  `queen.superseded`. Abort a second Supersedure by killing the candidate before it is ready: the
  old Queen wakes inside the grace window and the goal still finishes.

### ADRs to write

- `requeening-recovery-sources-of-truth.md`.
- `swarming-policy-and-cost-caps.md`.
- `supersedure-moving-the-hive-stand.md` (the candidate test; freeze before copy; the signed
  relocation notice and the grace window; never two Queens; what the old machine becomes).

---

## Phase 14: Brood 1.0

**Goal.** A release someone else can install and run.

### Steps

- [ ] **14.1 Getting Started.** Rewrite the README's Getting Started: install `uv`, choose a
  provider (API key, or Ollama and a model), `hive init` (starter manifest with the Hive Stand
  enabled), `hive run` with no Docker needed, then Docker Desktop for Virtual Cells, then `hive entrance
  expose` plus an invite to enrol a phone and speak a goal into it. Tested on
  clean Windows and Ubuntu machines, once per provider path.
- [ ] **14.2 `hive init` and `hive doctor`.** Manifest generator with `--provider`; checks for
  scratch root, Docker if enabled, every provider reachable, every slot's model present, Forage
  sanity, sqlite-vec loads, escalation policy file valid.
- [ ] **14.3 Docs pass.** Subsystem READMEs, ADRs, protocol spec, runbooks including
  `moving-a-slot-to-local.md`, `real-cells.md`, `wardens-and-alarms.md`, `clustering.md`, `nucs.md`,
  `supersedure.md`, `remote-access.md` (the VPN, enrolling a phone or a program, a lost device, the
  Entrance Reducer).
- [ ] **14.4 Security review.** Coding rules section 15 across the tree; `pip-audit`;
  `docs/security.md` covering the Swarm, leases, grants and attenuation, Nucs offline, the Lab,
  the Exoskeleton, and prompt injection through Honey, hot state and tool results.
- [ ] **14.5 License.** Choose and add `LICENSE`; update README.
- [ ] **14.6 Release.** Tag `brood-1.0`, wheels, `pollen` binaries, the Observation Hive build
  and the Android APK, changelog from conventional commits.

### Exit criteria

- A newcomer completes the haiku goal on their own machine as the Hive Stand in under 15 minutes,
  and in a Virtual Cell in under 30, using only the README, on either provider path.

---

## Cross-cutting tracks

| Track | Rule |
|---|---|
| ADRs | Written before the code that depends on the decision. Listed in each phase. |
| Manifest | Every new subsystem adds its `[section]` to `manifest/schema.py` and `docs/manifests/full.toml`, loaded in a test. |
| Pheromone Trail | Every new state-changing action gets an event kind, documented in `pheromone/events.py`; never prompt or completion text. |
| Contract suites | Every new Protocol gets one; every implementation joins the parametrisation. `CellSession`, `RealCellSource`, `Supervisor` and `LLMProvider` grow implementations across phases. |
| Autopilot purity | Nothing under `autopilot/` imports `hivemind.llm`; every event kind has a row in the dispatch table before it has an awake handler. |
| Context budget | Every new item that can enter hot state has a relevance rule, a size cap and a demotion rule; the flood test is extended. |
| Forage | Every new resource a bee can consume is in `ForageCapacity` and `ForageGrant`; every new role has a `RoleFootprint`; every new model or server is a `ModelSource` on the Forage map; every new spender is attenuated and metered by the Fanner. |
| Provider neutrality | Model calls go through a `ModelSlot` and a ladder; new LLM features land in the protocol and both adapters or not at all. |
| Left as found | Any step that lets a bee act on a Real Cell adds a restore path and extends the left-as-found test. |
| Capping | Any new side-effecting tool or action declares its risk tier, accepts a postcondition, and goes through the gate; a new tier gets rows in the tier table and an audit rate. |
| Cell images | Changes to `images/` rebuild in CI and run the Docker contract suite. |
| Security | `pip-audit` on every PR; the section 15 checklist on every phase exit. |
| Clearance | Every new row in a memory table, Nectar or Honey carries a `HoneyClearance`; every new reader filters by allowance. |
| Night Veil boundary | Every new event kind states whether it survives a Night Veil teardown (coding rules 12). |
| Landing Board | Every new Entrance route is versioned, appears in the committed OpenAPI document, is capability-scoped, is loopback-only if it approves, widens, unlocks or reopens anything, and has a push event if it can produce something the human must see; the generated TS types are regenerated in the same PR. |
| Roadmap | Tick the box in the PR that lands the step; add steps when scope grows. |

---

## Open decisions (ADR backlog, in the order they block work)

1. Waggle transport for v1: WebSocket + JSON is the working assumption (phase 1).
2. Default escalation policy table contents: initial retry and respawn counts per `AlarmKind`
   (phase 3.13).
3. Relevance scoring weights and the initial budget fraction and handoff threshold (phase 4.1).
3a. Initial Forage map grades per model and initial role footprints (phase 3.12); both are
   placeholders until the eval harness (8.5) and the Fanner's measurements replace them.
4. Which local server the OpenAI-compatible adapter is validated against first: Ollama (phase 3.7).
5. Embedding model and dimension; whether `sentence-transformers` is required or optional (phase 7.1).
6. Web framework for the Entrance: FastAPI vs Starlette (phase 10).
7. Nuc promotion criteria: minimum device Forage and Hive Stand pressure thresholds (phase 11.8).
7a. Whether to keep quarantining non-Ubuntu tools on Real Cells (9.3a) or to bring forward
   Windows and macOS Virtual Cell images; and the language for the compiled gateway for phones
   and IoT (11.7a).
8. First cloud Virtual Cell backend (phase 5.11).
9. Dashboard stack (phase 12): resolved to TypeScript + React + Vite with Capacitor for Android;
   still open are the state and stream library and push delivery on Android (FCM or UnifiedPush).
10. Packaging for `pollen` (phase 11.7).
11. License (phase 14).
12. VPN overlay for `expose = "vpn"`: WireGuard or Tailscale; and the tunnel client for
   `expose = "tunnel"` (phase 10.5a).
13. WebAuthn server library for passkeys, and web push delivery (phase 10.5e, 10.5b).
14. Default Whisper model size for `TRANSCRIBER`, and whether `faster-whisper` is a required or
   optional extra (phase 6.5a; the eval harness in 8.5 settles the size).
15. Speech synthesis for spoken replies, and whatever a glasses vendor's SDK allows beyond the
   share sheet (post-1.0).

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Hot state grows past what a model can take and bees break. | Budget-packed assembly with token counting before every call (4.1), per-item caps, `ContextTooLong` recovery (4.4), demotion as a House Bee duty (4.3), the flood test. |
| A bee's work lands wrong and nobody checks it until it matters. | Capping on every side effect outside scratch (3.17), postconditions checked by the Warden not the bee (3.18), judge review on a different provider for high tiers (4.10), snapshots and rollback on Virtual Cells (5.10), the flight recorder for GUI work (6.6), sampled audit for what cannot be gated (4.10). |
| Handoffs lose the thread and bees repeat or undo work. | Schema'd Handoff with tried-and-failed and do-not-redo (3.14), transcripts kept as Nectar (7.4), the handoff eval (4.5). |
| The Queen or a Warden becomes a chatbot with a growing transcript and unpredictable cost. | Stateless awake episodes assembled by `memory.assemble` (3.14), the no-transcript hygiene check (0.3), autopilot-first dispatch (3.17, 3.18). |
| A provider outage takes the whole Hive down. | Autopilot never awaits a model (0.2), per-provider Clustering that pauses and preserves (4.9), routing fallbacks within Forage (8.1). |
| Wardens overspend, fork-bomb their Cell or escalate their own capabilities. | Grants sized from reported capacity (3.12, 4.7), attenuation down the tree (10.1), over-grant Alarms, the Guard Bee (10.6). |
| An offline Nuc diverges from the Queen's view. | Grant is frozen while offline (11.9), outbox and trail segments replay on reconnection (1.8, 2.2), the Queen holds the node's tasks for a grace period, chaos tests cut the link (13.6). |
| Workers pollute Real Cells. | Leases with scratch roots and process tracking, capability gating outside scratch, left-as-found tests, Undertaker release, Requeening sweep. |
| Terminal-first tempts bees and generated tools to shell out around the capability system. | All process execution through `CellSession`; `subprocess` import-banned outside `cell/`, `hive/backends/`, the dev sandbox and `pollen`; the Comb bans it in generated tools. |
| Windows Home cannot run Hyper-V. | The Hive Stand is a Real Cell from phase 3 so nothing blocks on a hypervisor; Docker first, QEMU second, both behind `CellBackend`. |
| Code grows a dependency on one provider's behaviour. | Two adapters from phase 3, ladders in one place, prompts tested on a weak fake, `import-linter`, the eval table (8.5). |
| Local models are too weak for the Queen slot. | Slots are independent: move `RIPENER`, `EMBEDDER`, `ATTENDANT`, `WARDEN`, `WORKER` first; keep `QUEEN` hosted until the eval table says otherwise. |
| The Exoskeleton is dual-use and the README's wording invites misuse. | Scope fixed by the exoskeleton-scope-and-pheromone-mask-boundary ADR (phase 6) and coding rules section 15; review declines evasion features. |
| Self-authored tools are the largest attack surface. | Comb gates (9.5), no-bypass promotion (9.2), scopes (9.1), tools run under Worker capabilities (9.7), sandbox is always a Virtual Cell (9.4). |
| SQLite becomes a bottleneck. | Deliberate v1 choice (the single-store ADR) with the store protocols as the swap point. |
| Scope creep before the first vertical slice runs. | Phase 3 is the first thing that runs end to end, on the Hive Stand, with no infrastructure; nothing in phases 5+ starts until 3 and 4 exit. |
| An exposed Entrance is the biggest new attack surface. | Loopback always; VPN overlay by default and mutual TLS for LAN and tunnel; no public mode; devices enrolled and approved on loopback only; two-factor login bound to the device key; step-up for anything sensitive; lockout, rate limits and the Entrance Reducer; every security event trailed and pushed to every other device (10.5a, 10.5d, 10.5e); the route test (coding rules 8.11). |
| Supersedure leaves two Queens or loses a task. | The old Queen stays clustered from the first byte of the copy; the new one refuses to run until acknowledged or the grace window expires; checksummed restore; `QueenMoved` signed with the Hive key; rollback inside the window; chaos tests (13.2a, 13.6). |
| A lost or stolen phone. | Its key is one device among several; revoking it on loopback kills its sessions instantly; every login and step-up is pushed to the other devices; the passkey needs the phone's biometric or PIN; push payloads carry no content. |
