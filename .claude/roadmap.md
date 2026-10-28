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
| 5 | The Hive (Virtual Cells) + placement | A task can leave files on a Cell when its plan says so and policy or the human agrees (Leavings); the Queen chooses Real or Virtual per task; each Virtual Cell gets a Warden; Undertakers destroy or release; Overwintering pool (never for Night Veil) | 3, 4 |
| 6 | Exoskeleton | A Forager sees a screen, clicks, types, hears audio, on either Cell kind, only when asked; Pheromone Mask behavior is callable tactics, not a persistent mode | 5 |
| 7 | Honey Store | Nectar ripens into Honey with clearance labels; the cold tier of memory is live; Workers query it before acting | 3, 4 |
| 8 | Local models + provider routing | Every slot can run local; the Hive runs fully offline; routing weighs Forage and model location | 4, 7 |
| 9 | Royal Jelly Lab | A Worker or Warden requests a tool; it is scaffolded, quarantined, promoted at hive or cell scope, used | 5, 7 |
| 10 | Guard Bees + Hive Entrance | Every dispatch, lease and grant is authorised, including tactic invocation and clearance enforcement; the Hive has a versioned API (the Landing Board) that only devices enrolled at the Hive Stand can use, two-factor login bound to the device, push for questions and Alarms, a human inbox, and remote exposure over a VPN | 3 |
| 11 | The Swarm (Pollen Packet, Nucs) | A device enrols through a gateway, gets a Warden on the Hive Stand, can be promoted to a Nuc, enforces Meadow/Propolis on Real Cells, and keeps working when cut off | 1, 8, 10 |
| 12 | Observation Hive | One live UI in TypeScript + React, on any enrolled device including an Android app: the Queen's and any bee's thoughts, Cell diagrams, the Forage split, the fleet list with model hosting, Attendant views for the Queen and every Warden, a chatbox, device enrolment screens, and a browsable Honey tree | 2, 7, 10 |
| 13 | Resilience | Requeening restores a crashed Queen and her leases and grants; Supersedure moves the Hive Stand to another machine with no task lost; Swarming and Absconding work under load | 5, 10, 11 |
| 14 | Brood 1.0 | Packaged release with docs, runbooks, Getting Started | 0 to 13 |
| 15 | Inspection (manual Cell control) | The operator opens a terminal on, and sees and drives the desktop of, any connected Cell from the CLI or the Observation Hive, with the Cell's bees paused, step-up, device opt-in, an on-device notice and a trail of counts; post-1.0 | 6, 10, 11, 12, 13, 14 |

Phase 15 is post-1.0 and starts after 14 ships; its Track A (terminal) lands before Track B (remote desktop). Phases 6, 7 and 10 can proceed in parallel once phase 5 lands. Phase 8 needs 7 because embeddings
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

- [x] **3.1 Hive Manifest.** `manifest/schema.py` (pydantic `HiveManifest` with sections `[hive]`,
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
- [x] **3.2 LLM boundary models.** `llm/models.py`: `LLMRequest` (system, messages, tools,
  response schema, slot, max output), `Message` with `ContentPart` union (text, image, tool call,
  tool result), `ToolDefinition` (name, description, JSON schema), `ToolCall`, `LLMResponse`
  (parts, stop reason enum, `Usage`), `Usage` (input, output, cached, cost). These are HiveMind's
  own types; no SDK type appears here. Round-trip tests.
- [x] **3.3 Provider protocol and capabilities.** `llm/provider.py`: `LLMProvider` with
  `capabilities -> ProviderCapabilities`, `complete(request) -> LLMResponse`,
  `stream(request) -> AsyncIterator[LLMChunk]`, `count_tokens(request) -> int | None`, and
  `health() -> ProviderHealth`. `llm/capabilities.py`: the frozen `ProviderCapabilities` (native
  tool calls, schema-enforced output, JSON mode, vision, streaming, reasoning control, context
  window, system role, parallel tool calls). `llm/errors.py`: `LLMError` tree (`RateLimited`,
  `ProviderUnavailable`, `ContextTooLong`, `Refused`, `MalformedOutput`). `llm/fake.py`:
  `FakeLLMProvider` with scripted responses, a configurable capability set, and a switch to
  simulate an outage.
- [x] **3.4 Model slots and registry.** `forage/slots.py`: the `ModelSlot` enum (`QUEEN`,
  `ATTENDANT`, `WARDEN`, `WORKER`, `RIPENER`, `SCAFFOLDER`, `EMBEDDER`, `JUDGE`, `TRANSCRIBER`),
  placed in `forage` so autopilot rules and grants can name a slot without importing
  `hivemind.llm`.
  `llm/slots.py`: `resolve(slot, manifest) -> BoundModel` (provider + model id + fallback chain
  + cost); a fallback may name a slot or a named binding that exists only in `[llm.slots]`. `llm/registry.py`: constructs
  providers from `[llm.providers.*]` by `kind`, enforces `offline = true` by refusing non-loopback
  base URLs, and hands out `BoundModel`s. The composition root is the only caller.
- [x] **3.5 Degradation ladders.** `llm/structured.py`: `complete_structured(bound, request,
  schema) -> T` walking native schema output → JSON mode plus pydantic validation and retry →
  prompted JSON with fenced extraction, validation and retry; retry counts are commented constants.
  `llm/tools.py`: `run_tool_loop(bound, request, tools, executor)` using native tool calls when
  supported, else a prompted tool protocol we parse and validate. Both handle `ContextTooLong` by
  signalling the caller to shrink the budget. Tested at every capability level with the fake.
- [x] **3.6 Anthropic adapter.** `llm/providers/anthropic/{provider,mapping,client}.py` using the
  `anthropic` SDK: maps our models to SDK types in `mapping.py` only; declares full capabilities;
  adaptive thinking, streaming for long outputs, native structured output, `strict` tool schemas,
  prompt-caching breakpoints on the stable prefix, token counting through the API, typed errors
  mapped to `LLMError` subclasses. Verify every call shape against the SDK docs at implementation
  time rather than memory.
- [x] **3.7 OpenAI-compatible adapter (local models).** `llm/providers/openai_compat/{provider,
  mapping,client}.py` over `httpx` against `/v1/chat/completions` and `/v1/models`. Capabilities
  probed at startup where possible and otherwise set per provider in the manifest so a weak model
  honestly reports what it cannot do and the ladders take over. Token counting by estimate with a
  documented margin. No vendor SDK; one small client file.
- [x] **3.8 Provider contract suite.** `tests/contracts/test_llm_provider_contract.py`
  parametrised over the fake, Anthropic and OpenAI-compatible adapters using recorded HTTP
  cassettes: completion, structured output at each rung, tool loop at each rung, streaming, error
  mapping, health, usage normalisation. A `live_llm` variant hits real endpoints on demand.
- [x] **3.9 Prompt assets.** `llm/prompts/` with one file per prompt (`queen_system.md`,
  `decompose_goal.md`, `warden_system.md`, `drone_system.md`, `attendant_triage.md`), loaded by
  `llm/prompts/loader.py`; `llm/prompts/README.md` states the portability rules. Snapshot tests.
  Prompts label retrieved, hot-state and user-supplied content as such and delimit it.
- [x] **3.10 The Cell abstraction.** `cell/models.py` (`Cell`: id, kind, capabilities, source,
  `ForageCapacity`, `comb_shield`; `CellKind` enum; `CellCapabilities`: os, arch, has_display,
  has_audio, has_browser, can_start_display, can_host_model, network scopes),
  `cell/session.py` (`CellSession` protocol: streaming `exec`, `put_file`, `get_file`,
  `scratch_dir`, `close`), `cell/lease.py` (`RealCellLease` with scratch root, started process
  ids, allowed paths, the Cell's `AccessLevel` and `CombShieldLevel` from 2.3a, idempotent
  `release()`), `cell/source.py` (`RealCellSource`), `cell/snapshot.py` (`Snapshotter` protocol:
  `snapshot(cell) -> SnapshotId`, `rollback(cell, snapshot)`; `NoopSnapshotter` for Real Cells
  with a documented warning; Virtual backends implement it in 5.10), `cell/fake.py`,
  `cell/errors.py`. Records `cell.leased` / `cell.released`.
- [x] **3.11 The Hive Stand as a Real Cell.** `cell/local/`, a package, because source, session,
  lease directories and two platforms will not fit one file: `source.py` (`HiveStandSource`, one
  Cell whose capabilities and `ForageCapacity` are probed from the running machine), `probe.py`
  (platform, arch, cores, memory, GPU, display, browser, with POSIX and Windows shims side by
  side) and `session.py` (`LocalProcessSession`: asyncio subprocess, standard library only, POSIX
  and Windows). Each lease gets its own directory under `[hive_stand] scratch_root`,
  tracks child processes, and `release()` terminates survivors and removes the directory.
  `[hive_stand] scratch_quota_mb` caps each lease's directory (default 4096): the lease samples
  the directory's size on the watchdog tick while a command runs and after every command, and
  on POSIX also sets `RLIMIT_FSIZE` to the quota on each child; over quota it terminates the
  child, the command returns a quota error, and the Worker raises a `QUOTA_EXCEEDED` Alarm
  (a waggle minor bump to `AlarmKind`) whose policy row is roll back then fail, never a
  `ForageRequest`, since scratch disk is local capacity nobody grants more of. Refuses to
  lease when disabled or when the host's free disk is under `[hive_stand] disk_reserve_mb`.
  `CellSession` contract suite over local and fake. A **left-as-found test** snapshots a
  temporary home and the process table before and after, and a quota test extracts a nested
  archive and asserts the lease stops it under the cap.
- [x] **3.12 Forage v0: the models.** `forage/models.py`, one frozen model per concept, each
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
- [x] **3.12a The Fanner v0.** `llm/fanner.py`: every model call from any bee passes through a
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
- [x] **3.13 Supervision protocol.** `supervision/supervisor.py` (`Supervisor` protocol:
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
- [x] **3.13a Capability primitives.** `guard/capabilities.py`: the `Capability` families and the
  pure `CapabilitySet` with `allows()` and `attenuate(subset)` from 10.1, built now because 3.15
  and 3.16 gate on them; 10.1 adds the remaining families and 10.2 the policy engine.
  `guard/access.py`: what each `AccessLevel` (2.3a) permits, as data.
- [x] **3.14 Memory v0.** `memory/handoff.py` (the `Handoff` schema with mandatory fields and
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
- [x] **3.15 Worker runtime.** `workers/base.py` (`Worker` protocol), `workers/runtime.py` (the
  loop: receive `TaskAssign`, run the role, send `TaskProgress`/`TaskResult`, honour `TaskCancel`,
  `TaskPause`, `TaskResume`; send `Heartbeat` with `ContextTelemetry`; honour every
  `Intervention`; checkpoint and reset at the manifest threshold; raise an `Alarm` instead of
  crashing), `workers/capabilities.py` (a Worker's `CapabilitySet` slice from 3.13a). The runtime receives a `Cell`, an open `CellSession`, a
  `BoundModel` and a `ForageGrant` slice; it never receives a provider, a subprocess handle, or the
  Cell's kind.
- [x] **3.16 Drone role and built-in tools.** `workers/roles/drone.py` runs a bounded tool loop
  through `llm/tools.py` with prompts assembled by `memory.assemble`. `workers/tools/session.py`
  (`run_command`, `read_file`, `write_file` through `CellSession`, capability-gated outside
  scratch), `workers/tools/http.py`, `workers/tools/ask.py` (raise a `Question` up the chain).
  Tool calls are validated against schema and capabilities whichever rung produced them, and any
  call with a side effect outside scratch goes through Capping (3.17) first.
- [x] **3.17 Capping gate v0.** `supervision/capping/`: `Proposal` (what a bee wants to do: a
  diff, a command, an action sequence; its declared **postconditions**; its risk tier),
  `tiers.py` (risk tiers as data in `docs/supervision/capping-tiers.toml`: `read_only`,
  `scratch_write`, `outside_scratch_write`, `network_egress`, `spend`, `device_command`,
  `irreversible`, each mapping to the checks it requires), `checks/deterministic.py` (schema,
  lint and type checks for code, path and command allowlists, diff size caps; autopilot, no
  model), `gate.py` (`propose → check → cap → apply → verify postconditions`, with `REJECTED` and
  `ROLLED_BACK` outcomes; `capping.*` trail events), `postconditions.py` (machine-checkable
  assertions: file exists, command exits zero, test passes, HTTP status, element text; checked
  after apply by the Warden, never by the bee that proposed). Uncapped work never leaves scratch.
  An `outside_scratch_write` on a Real Cell (an install the Hive makes, for instance) carries
  its restore path, recorded on the lease and replayed by `release()`, unless the operator
  approved it to persist, in which case it is recorded with the node the way a colonized
  device's resident tools are (phase 11), so Absconding can still remove it.
  Judge review, snapshots, the flight recorder and sampled audit arrive in later phases as extra
  checks on the same gate.
- [x] **3.18 Acceptance criteria.** The planner (3.20) emits `acceptance` for every subtask as a
  list of postconditions plus, where nothing machine-checkable exists, a rubric for a judge. A
  task reaches `SUCCEEDED` only when its Warden has run the acceptance checks and they pass; the
  bee that did the work cannot mark itself done. Failed acceptance raises an Alarm with the
  failing assertion attached.
- [x] **3.19 Warden.** `wardens/warden.py` (standard loop; owns one Cell and its session; holds a
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
- [x] **3.20 Queen kernel.** `queen/inbox/` (the Queen's Attendant, instantiated from
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
- [x] **3.21 CLI.** `cli/run.py` (`hive run "goal" --manifest hive.toml`, streams progress),
  `cli/llm.py` (`hive llm providers|slots|test <slot>`), `cli/cells.py` (`hive cells list`),
  `cli/inbox.py` (`hive inbox` lists pending questions and Alarms at the human; `hive inbox answer
  <id> "..."`), `cli/wardens.py` (`hive wardens list` with grant, sub-bees, telemetry),
  `cli/capping.py` (`hive capping queue|show <proposal>`).
- [x] **3.22 End-to-end tests.** `tests/e2e/test_kernel_on_hive_stand.py` with the fake provider:
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

- [x] `hive run "write three haiku about bees to separate files"` completes on the Hive Stand through
  its Warden, with **both** `docs/manifests/minimal.toml` (Claude) and `docs/manifests/local.toml`
  (a local server), the scratch root is empty afterwards and no process the run started is alive.
  **Met 2026-09-15** on the local-server leg: a file-writing goal ran end to end against LM Studio
  (Heretic Qwen3.8-27B, the operator's own `hive.toml`) through the Hive Stand's Warden; the lease's
  scratch directory was removed on release and no process survived the run. The Claude leg
  (`minimal.toml`) is not yet exercised. Note for later phases: the same cleanup deletes every file
  a Drone produces, so until the Basket (9.2a) lands, a task's only durable output is its summary.
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

- [x] **4.1 Relevance scoring and budget packing.** `memory/relevance.py`: pure `score(item,
  now, active_tasks, pins)` from recency decay, task linkage, Alarm severity and pins that never
  decay; property-tested for monotonicity. `memory/hot_state.py` packs by score to the budget,
  counts tokens through the provider or the estimate, drops lowest-scored first, replaces
  oversized items with references. Per-item cap from the manifest; large tool results become
  Nectar with a reference.
- [x] **4.2 Bee Bread (warm tier).** `memory/bee_bread.py`: an index over Brood Chamber history
  and the trail by id, time and task, plus stored Handoffs and deposited transcripts; lookup only,
  no search; every entry carries its `HoneyClearance` and lookups filter by the reader's
  allowance. `memory/demote.py`: pure rules for what leaves hot state (task closed, Alarm resolved,
  age past a manifest window).
- [x] **4.2a Cell Wax (cell notes).** `memory/cell_wax.py`: `CellWax` (id, Cell id, `WaxSeverity`
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
- [x] **4.3 Compaction.** `memory/compact.py`: summarise from source records on `ModelSlot.RIPENER`,
  never from a previous summary; pins verbatim; one level of summary; records `memory.compacted`.
  A House Bee **sweep** duty (`workers/roles/house_bee.py`, first version) runs demotion and
  compaction on a timer, and ripens Bee Bread into Honey once phase 7 lands.
- [x] **4.4 Overflow recovery.** `ContextTooLong` from any provider shrinks the budget for that
  episode and retries, recording `memory.overflow`; three overflows raise an Alarm. A synthetic
  flood test pushes ten thousand events through the Queen and asserts the assembled prompt never
  exceeds the budget and every dropped item is findable in Bee Bread.
- [x] **4.5 Handoff quality eval.** `tests/evals/handoff/`: a bee is stopped mid-task, a fresh bee
  resumes from the Handoff alone and must complete; graded on completion and on not repeating
  do-not-redo steps. Run with the fake in CI and with real providers under `live_llm`.
- [x] **4.6 Context interventions end to end.** The Queen watches Warden telemetry and orders
  `compact` or `handoff` past thresholds; Wardens do the same to sub-bees; `inspect` returns a
  compacted view under a size cap. A test asserts no full transcript ever appears in a Waggle
  message.
- [x] **4.7 Forage ledger and allocation v1.** `queen/forage/ledger.py`: the live book. It holds
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
  capacity and asserts the sum of grants never exceeds capacity minus reserve. Rename
  `hivemind.wardens.local_pool.pool.LocalPool` to `SubBeeSlots` as part of this step:
  codingrules 6.1 gives `LocalPool` to `forage` (a Cell's cores, memory, disk, VRAM and
  seats), the Warden's is a bare counter against a grant's `max_sub_bees`, both are exported
  from their own package's `__init__`, and the ledger is the first module that has to read
  both meanings in one file.
- [x] **4.7a Hosted headroom is measured, not assumed.** `Abundance`'s two rate fields
  (`requests_per_minute_left`, `tokens_per_minute_left`) are written by nothing today:
  `cli/stores.py` builds every source as `Abundance(seats_free=spec.seats)` and
  `ForageMap.set_abundance` carries the old values forward, so 3.12a's "updates `distance` and
  `abundance`" is half done and a hosted provider's real pressure never reaches the map. Close
  the loop where the measurement already exists. `llm/models.py`: `LLMResponse` grows an optional
  `RateLimitSnapshot` (requests left, tokens left, when the window resets) that each adapter
  fills from its provider's own response headers and leaves `None` where the provider publishes
  none, which is every local server. `ForageMap.set_abundance` takes that snapshot alongside
  `seats_free` and writes both halves, so an unmetered source keeps its `None`s rather than being
  handed invented numbers. A `RateLimitedError`'s `retry_after_s` feeds the same path (both
  adapters already parse it and both drop it), putting that source's headroom at zero until the
  window passes, so routing (8.1) and the ladders stop choosing a provider that has just said to
  wait; recorded as `llm.throttled` with the source and the wait. `ProviderRateLimiter` prefers
  reported figures over the manifest's configured ones once it has them, so a manifest number is
  a starting guess and never a permanent ceiling.
- [x] **4.8 Hosting plans and ceilings.** `queen/forage/hosting.py`: per Cell, write a
  `HostingPlan` (per slot a primary source and a fallback chain, plus a default) from the Forage
  map and ledger: the Cell's free VRAM against each model's requirement, seat pressure on the Hive
  Stand, the measured distance from the Cell to each candidate source against the task's tempo,
  the task's need to survive disconnection, and cost caps; local sources first wherever the Cell
  has them; recorded as `forage.plan_written` with the reason. `queen/forage/ceilings.py`: set
  and change a Warden's `Ceilings` (maximum sub-bees, VRAM and disk for models, scratch disk
  per lease, resident Basket disk, loadable map entries, exportable seats), recorded as
  `forage.ceilings_set`. Routing (phase 8) consumes the plan; a local source is only selectable
  once phase 8.3 can start a server on a Cell.
- [x] **4.9 Clustering.** `queen/cluster/protocol.py`: triggered per provider by `ProviderHealth`
  failing with no fallback within Forage, by a cost cap, or by `hive cluster`: checkpoint every
  affected bee, move their tasks to `PAUSED`, keep leases and Cells alive, keep heartbeats and
  watchdogs, poll health with backoff, resume from Handoffs on `hive wake` or recovery, record
  `queen.clustered` / `queen.resumed`. Bees on other providers continue. The Queen's own awake mode
  being unavailable is handled by her autopilot running the same protocol.
- [x] **4.10 Judge review and sampled audit.** `supervision/capping/checks/judge.py`: an
  independent review on `ModelSlot.JUDGE` with no shared context with the proposing bee, a rubric
  per risk tier, and a structured verdict (approve, request changes, reject) with reasons; the
  manifest may pin `JUDGE` to a different provider than `WORKER` so blind spots do not correlate.
  `supervision/capping/audit/sampler.py`: for tiers the table marks as not gated in real time, sample
  completed work at a per-tier rate, review it with the judge after the fact, deposit findings as
  Nectar, raise an Alarm on a failed audit, and feed rates to the Guard Bee (phase 10). Both are
  extra checks on the gate from 3.17; the tier table decides where each applies, and the task's
  tempo may shorten or lengthen the ladder only above the tier's floor (an urgent `scratch_write`
  may skip the judge; `irreversible` never skips anything).
- [x] **4.11 CLI.** `cli/memory.py` (`hive memory show <bee>`, `hive memory pins add|list|remove`,
  `hive memory compact <bee>`, `hive memory wax <cell> list|propose|clear`), `cli/forage.py` (`hive forage status|grants|grant <warden> ...`),
  `cli/cluster.py` (`hive cluster [provider]`, `hive wake`), `hive capping audit --sample`.

### Exit criteria

- [x] The flood test holds the budget; the handoff eval passes; the phase 3 scenarios still pass with
  a hot-state budget deliberately set to a quarter of the default.
- [x] Kill the fake provider mid-run: the trail shows checkpoint → `PAUSED` for every affected bee,
  leases stay open, `hive wake` after restoring the provider resumes every task from its Handoff
  and the goal completes with no duplicated work.
- [x] A Warden asking for more sub-bees than the Hive Stand can bear is denied with a reason on the
  trail; the same request under headroom is granted by autopilot with no awake episode.
- [x] With the fake provider limited to two seats, six Drones run with at most two model calls in
  flight at any moment, the Fanner's queue orders them by tempo, and the Forage view (phase 12)
  would show seats at 2 of 2 throughout. A Warden whose heartbeat stops has its grant back in the
  pool after expiry.
- [x] A fake hosted provider that answers one call with a 429 and a `retry-after` has that
  source's headroom at zero on the Forage map until the window passes: nothing is routed to
  it meanwhile, the trail shows one `llm.throttled`, and the next source in the chain absorbs
  the work. A provider that publishes no limits keeps `None` on both rate fields throughout.
- [x] A Warden's `CAUTION` about its own Cell is written by autopilot with no awake episode and
  appears in the planner's prompt only when that Cell is a candidate, never in any other episode;
  a `BLOCK` proposed by a Drone reaches the Queen's awake mode; an expired note leaves hot state
  on the next sweep.

**Met 2026-09-16.** Every bullet is a test in `tests/e2e/test_phase4_exit_criteria.py` and
`test_phase4_exit_criteria_forage.py` (13 tests, all passing; the eight phase 3 scenarios also run
at a quarter of the default `budget_fraction`). The real-task rule was exercised three times against
LM Studio (Heretic Qwen3.8-27B, the operator's `hive.toml`): a mixed run with fake planning and
real Drones, an instrumented full run, and the plain `hive run` command; each planned a multi-task
graph, ran three Drones in parallel on the real server (one proposal rolled back and retried), and
completed with the scratch root removed. One earlier full run "timed out in 417.9s" with both
Drone requests cancelled a second after being sent: `hive run`'s default `--timeout` (120 s) counts
from submission and planning alone took 418 s (five structured-output attempts on the local model),
so the poll loop expired the instant dispatch happened and the normal teardown stopped the Warden.

**Follow-up, later on 2026-09-16** (see `.claude/phase-4-handoff.md` section 4): the scenario (g)
flake was a real race (a cancelled `asyncio.to_thread` hop leaving a SQLite transaction open under
the next caller's `BEGIN`; fixed with one worker thread per connection, `hivemind.common.sqlite.
ConnectionThread`); the planner's five attempts were the model reasoning about a schema it never saw
plus an 8192 output cap the manifest could not raise (fixed: the schema is shown on every rung, a
cut-off reply gets a targeted correction, and `[llm.slots.<key>] max_output_tokens` now reaches the
request; with 12288 the planner validated first time, 78 s, 5470 reasoning tokens); a task's tempo
now reaches its sub-bees' and the judge's Fanner lanes; and 4.9's first trigger, `ProviderHealth`
DOWN with no fallback, is implemented in `run_cluster_tick`. The provider outage was then run for
real: LM Studio stopped three seconds after the first `worker.spawned`, `queen.clustered`
(`cause = provider_down`) 26 s later with no operator order, `lms server start` then `hive wake`,
`queen.resumed` one second after the order, the three files written once, goal succeeded in 55 s
of outage-to-finish.
Pass `--timeout 900` with a local 27B model. Not yet exercised for real: a live provider outage
through `hive cluster`/`hive wake` (covered by the fake-provider e2e only).

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

**Deliverables.** `cell/leavings`, `supervision/capping/leave`, the HUMAN rung and the `keep`
tool (5.0a to 5.0e); `hivemind/hive`, `queen/placement/` for real, `images/base-ubuntu`, Undertaker
role, `hive cells` CLI.

### Steps

Steps 5.0a to 5.0e are **Leavings**: what a task may leave behind on a Cell after its lease is
released. They run on the Hive Stand alone, need no Virtual Cell, and land before 5.1 so every
real run from here on can keep its output. The rule they keep: scratch is still removed wholesale
on release and a Cell is still left as found, *plus exactly the paths the Leavings ledger lists*.
What stays is declared by the plan, decided by policy, and asked of the human only when the policy
says so; a bee never decides it alone. Leavings are files left in place on a Cell; bytes the Hive
keeps in its own store are the Basket (9.2a), a separate thing.

- [x] **5.0a Persist flag and the Leavings ledger.** `RealCellLease.note_restore_path` and
  `supervision/capping/lease_view.py` grow a `persist` argument (default `False`), so the
  `RestoreRecord.persist` the releaser already honours (3.17) can finally be set.
  `cell/leavings/`, a package: `Leaving` (Cell id, path, sha256, size, task, lease, `approved_by`
  = `POLICY | HUMAN`, the reason, left and removed times), a `LeavingsStore` protocol and its
  SQLite store in the Brood Chamber's store pattern. A persisted record writes its ledger row and
  a `cell.left` trail event in the same call; `LeaseReleaseReport` gains `left_paths`, and
  `is_restored` stays true when the only paths not put back are ledgered ones. `hive cells
  leavings list|remove <cell>`: `remove` replays the stored prior bytes (or unlinks), marks the
  row and writes `cell.leaving_removed`. The left-as-found test is extended: after release the
  temporary home equals its snapshot plus exactly the ledger's paths, and after `remove` it
  equals the snapshot.
- [x] **5.0b The plan declares what stays.** `PlannedTask` gains `leaves`: a bounded tuple of
  `PlannedLeaving` (an absolute or `~`-rooted path pattern, a one-line reason), empty by default,
  carried through `TaskDraft`, the Task row and the Assignment (a waggle minor bump) and rendered
  into the Drone's brief by `brief_for()` beside the acceptance criteria. The validator refuses a
  pattern inside scratch, a bare root or drive, and any `..` segment, inside the
  structured-output ladder so a bad one is retried. The planner prompt says to declare only what
  the goal itself asks to remain ("install X", "set up a project in Y"), never working files;
  snapshot test. A Drone cannot widen `leaves`; it can only raise a `Question`. A path no plan
  declared is never persisted, whatever a tool result or a bee says.
- [x] **5.0c Leave policy.** `supervision/capping/leave/`: pure autopilot
  `decide(request, cell, declared) -> ALLOW | ASK | DENY`, its table as data in
  `leave-policy.toml` beside `capping-tiers.toml`. Inputs, all already known: the Cell's
  `AccessLevel` (`READ_ONLY` and `SCRATCH` always `DENY`) and `CombShieldLevel` (`NIGHT_VEIL`
  always `DENY`), whether the Cell is the Hive Stand or a borrowed device, the path's class
  (`keep_root`, home, system or startup location), size, whether the file is executable, and
  whether the path matches the task's `leaves`. It reads capabilities and levels, never
  `cell.kind`. The gate consults it when applying an `outside_scratch_write`: `DENY` does not
  reject the write, it applies with `persist=False` and is restored on release exactly as today;
  `ALLOW` sets `persist=True` with `approved_by = POLICY`; `ASK` goes to 5.0d. The judge rubric
  for `outside_scratch_write` gains one criterion, "does leaving this match the task's stated
  objective", so an over-declaring plan is caught by a different model.
- [x] **5.0d The HUMAN rung, for Leavings first.** `supervision/capping/checks/human.py`:
  `HumanCheck` raises a `Question` up the existing chain (3.19 to 3.21) with closed options, *keep*,
  *keep for this whole goal*, *discard*, and the task blocks until it is answered. Only an
  `Answer` with `source = HUMAN` approves; a Queen or Warden answer is refused. *Keep for this
  whole goal* is remembered by the Queen per goal and Cell so one goal asks once, not once per
  file. An unanswered question past its timeout means *discard*, never a failed task. No tempo
  removes this rung. Phase 10 moves the same question onto push and step-up; nothing here
  changes then.
- [x] **5.0e The `keep` tool and `keep_root`.** `workers/tools/keep.py`: `keep(source,
  destination)` proposes moving a scratch file to a path outside it, an ordinary
  `outside_scratch_write` through the same gate, policy and ledger. A diff cannot carry a binary,
  so this adds a `COPY` action kind (a waggle minor bump) capped by a per-tier `max_copy_bytes`.
  `[hive_stand] keep_root`: an optional directory outside `scratch_root` that outlives leases,
  `ALLOW` by default in the policy, counted against `disk_reserve_mb`; the loader refuses one
  inside `scratch_root`. Commands: `run_command` effects cannot carry a restore record, so v0
  scans only the task's declared `leaves` patterns before and after each command and ledgers
  what appeared or changed there; anything else a command touches stays a
  `cell.touched_outside_scratch` event as today.
- [x] **5.1 Virtual Cell model.** `hive/models.py`: `VirtualCellSpec` (image, cpu, memory, disk,
  lifetime, network policy, exoskeleton flag, `ForageCapacity` the image promises),
  `VirtualCellStatus` enum + transition table in `hive/cell_state.py`. A provisioned Virtual Cell
  is returned as a `cell.Cell` of kind `VIRTUAL`.
- [x] **5.2 CellBackend protocol and registry.** `hive/backends/base.py` (from codingrules
  Appendix A.1), `hive/registry.py`. `hive/backends/fake.py` for tests.
- [x] **5.3 Base image.** `images/base-ubuntu/Dockerfile`: Ubuntu 24.04 LTS + Python + the
  `hivemind` runtime + a Waggle client that connects **out** to the Queen. Terminal-first. The
  image's entry point starts a **Warden**, which then spawns sub-bees inside the Cell.
  `images/base-ubuntu/README.md` explains every layer.
- [x] **5.3a Night Veil image.** `images/night-veil-ubuntu/Dockerfile` on `desktop-ubuntu`
  (6.1, so this step lands after it): OpenVPN client, Tor daemon, Tor Browser, nftables
  kill-switch rules, and the location-blind defaults baked in (UTC, fixed locale, hostname
  randomised at boot, metadata endpoints null-routed). README documents each package and each
  rule. Built in CI with the other images; attestation (5.7b) checks this image and installs
  nothing at runtime.
- [x] **5.4 Docker backend.** `hive/backends/docker.py` using the Docker SDK under
  `asyncio.to_thread`: create container from spec with resource limits and a network policy
  (`none`, `egress-only`, `allowlist`), wait for `CellReady` and the Warden's first `Heartbeat`,
  destroy with volume cleanup. Contract suite passes. Integration test marked.
- [x] **5.5 In-Cell session and Warden spawn strategy.** `cell/in_cell.py`: `InCellSession`, used
  when the Warden and its sub-bees run inside the Virtual Cell. `wardens/spawn/` gains the
  `in_cell` strategy. `CellSession` contract suite runs over local, in-cell and fake.
- [x] **5.6 Virtual Cell lifecycle.** `hive/lifecycle.py`: `provision → Warden ready → grant →
  release → (overwinter | teardown)`, each transition a `cell.*` event. Night Veil path is always
  `provision → Warden ready → grant → teardown` (no Overwinter branch).
- [x] **5.7 Placement policy.** `queen/placement/decide.py`: pure `decide(needs, inventory,
  forage, policy) -> Placement` over Real Cells with free capacity and Virtual backends with
  headroom, honouring `[placement]` (`prefer = "real" | "virtual"`, `allow_hive_stand`, per-role
  overrides). Rules, each with a test: `isolation = "required"` always Virtual; Exoskeleton needs a
  display or the ability to start one; OS and network scopes must match; Forage must cover the
  grant; a `BLOCK` Cell Wax (4.2a) excludes the Cell and a `CAUTION` counts against it; otherwise
  honour `prefer`. Records `queen.placed` with the reason, the wax that weighed on it included.
- [x] **5.7a Night Veil placement and routing constraints.** `queen/placement/policy.py` and
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
- [x] **5.7b Night Veil deterministic bootstrap attestation.** `hive/night_veil.py`: before
  `CellReady`, attest the `night-veil-ubuntu` image (5.3a) deterministically, installing nothing
  at runtime: firewall kill-switch active,
  default route via VPN tunnel, Tor daemon healthy, Tor Browser installed and launchable, the
  Hive Stand's Waggle hidden service reachable and the Waggle client's socket routed through the
  Tor SOCKS proxy (not the VPN interface), DNS leak
  checks passing, and direct egress blocked. Location-blind checks must also pass: geolocation APIs
  denied, metadata endpoints unreachable, timezone pinned to UTC, locale pinned to profile,
  randomized hostname confirmed, and WebRTC local-IP leak test blocked. Fail placement if any check
  is red.
  **Judged properly 2026-10-14 (phase 11 review).** Four checks had passed on too little, and a
  fourteenth was missing. `hidden_service_reachable` required a `2` anywhere in curl's output and so
  passed on `502` or on the listener's own `426` by accident; it now requires exactly `426`, a
  WebSocket endpoint's answer to a plain GET. `dns_leak_free` passed on any readable `resolv.conf`;
  it now places every resolver, Tor's own `DNSPort` on loopback or an address reached by a tunnel
  (the Propolis tier's judge and readers, `cell/tunnel/`). `randomized_hostname` is new: a name of
  the `night-veil-<8 hex>` shape that is neither an image's default nor the Hive Stand's, which the
  Docker backend now gives a Night Veil container (it carried the daemon's container id). And
  `webrtc_leak_blocked` was always `NOT_APPLICABLE`: it now reads the Tor Browser profile and
  requires `media.peerconnection.enabled` false, baked into the image (`tor-browser-user.js`), and
  does not apply only on a Cell with no browser at all. `hive/night_veil/judges/` holds the pure
  judges.
- [x] **5.8 Undertaker role.** `workers/roles/undertaker.py`: destroys Virtual Cells idempotently,
  releases Real Cell leases idempotently, revokes their grants, retires a destroyed Virtual Cell's
  Cell Wax (handed to ripening once phase 7 lands; a Real Cell's wax outlives its leases), retries
  with backoff. On Queen
  startup sweeps orphans of both kinds from backend labels and the trail. Destroying a Virtual
  Cell marks its Leavings ledger rows (5.0a) removed, since the paths died with the Cell;
  releasing a Real Cell never touches a ledgered path.
- [x] **5.9 Overwintering pool.** `hive/overwinter/policy.py` (pure, Virtual only),
  `hive/overwinter/pool.py`. Placement prefers a dormant Cell with the right image. Clustering uses
  the pool for long outages. Night Veil Cells are excluded. A dormant Cell's volume keeps its
  resident Basket blobs (9.2a), so a reused Cell starts with them.
- [x] **5.10 Snapshots for Capping.** `hive/snapshot.py`: `DockerSnapshotter` and
  `QemuSnapshotter` implementing the `Snapshotter` protocol from `cell/snapshot.py` (3.10)
  (Docker commit, QEMU snapshot; a documented no-op with a warning for backends that cannot). The
  Warden injects its Cell's `Snapshotter` into the Capping gate, so Capping never imports `hive`.
  The Capping gate takes a snapshot
  before any proposal in the `irreversible` or `device_command` tiers on a Virtual Cell and rolls
  the whole Cell back when postconditions fail. Snapshots are accounted as Forage (disk) and
  expire with the manifest's retention.
- [x] **5.11 QEMU backend.** `hive/backends/qemu.py`: prebuilt qcow2 (`images/base-ubuntu/vm/` from
  `scripts/build_cell_image.py`), cloud-init for the Waggle endpoint and Warden bootstrap, serial
  console for readiness. Same contract suite.
- [x] **5.12 Cloud backend interface.** `hive/backends/cloud/base.py` with credentials via
  `SecretStr`, region, pricing tag as Forage cost, and one reference implementation chosen in the
  ADR. Optional for Brood 1.0.
- [x] **5.13 CLI.** `cli/cells.py` grows `inspect`, `destroy <virtual>`, `release <lease>`,
  `snapshot <cell>`, `rollback <cell> <snapshot>`, and `abscond` (destroy every Virtual Cell
  tagged with this Hive id and release every lease, from backend labels and the trail alone).

### Exit criteria

- Leavings, on a real run against the Hive Stand: a goal that asks for a file to be kept leaves
  it under `keep_root` after release with a ledger row and a `cell.left` event, and scratch is
  still gone; the same goal without that ask leaves nothing; a goal whose `leaves` path the policy
  marks `ASK` blocks until `hive inbox answer`, *discard* restores the path and the task still succeeds;
  `hive cells leavings remove` returns the host to its left-as-found snapshot.
  **Met, 2026-09-21**, on the local 27B model with a copy of the operator's manifest plus
  `access_level = "FULL"`, `keep_root`, and `[llm.slots.judge] max_output_tokens = 6144`: (1) the
  keep goal succeeded in 456 s, `capping.leave_decided` `KEEP_ROOT ALLOW`, `cell.left`, one
  ledger row `approved_by = policy`, scratch gone, `is_restored = true`; (2) the plain goal
  succeeded in 77 s and left nothing; (3) an executable leaving raised the closed Question into
  `hive inbox`, `hive inbox answer --option 2` unblocked it, `leave_decided` recorded
  `human_answer = DISCARD`, the file was gone after release and the task succeeded; (4)
  `hive cells leavings remove <cell>` emptied the directories and marked the rows removed.
  Found on the way: the model judge had never run for real before (scratch writes skip it), and
  with its call-site budget of 1024 tokens (`wardens/judge.py`) this model spends every token
  thinking and returns nothing, nine times, which crashes the Drone; the manifest cap fixes it.
  The judge also rejected every outside-scratch `DIFF` the Drone proposed without postconditions
  (three to six rejections per run, about 30 s each) until the Drone reached the path through a
  command (`write_file` already attached `FILE_EXISTS`; the rubric demanded what no v0 kind can
  prove). Every `hive run` mints a new Cell id, so `leavings remove` needed the id from `cell.left`.
  All fixed 2026-09-22: a judge that cannot answer is a FAILED check the Drone reads, the code
  budget is 4096, the rubric is v3, `leavings list` with no Cell lists everything and `remove` takes
  `--path`; the phase 4 zero-grant hang and the missing `RELEASING -> ORPHANED` edge went with them.
- The haiku run completes three ways: `prefer = "real"` uses the Hive Stand and zero containers;
  `prefer = "virtual"` uses three containers, each with its own Warden visible in `hive wardens
  list`; a task with `isolation = "required"` uses a Virtual Cell regardless.
- A second run with `prefer = "virtual"` reuses Overwintered Cells and is measurably faster, while
  Night Veil runs always provision fresh Cells and always teardown at completion.
- `hive cells abscond` leaves zero containers, zero open leases, zero live grants, and the
  left-as-found snapshot holds.
- A `BLOCK` Cell Wax on the Hive Stand makes `prefer = "real"` place on a Virtual Cell with the
  wax named in the `queen.placed` reason; clearing it restores the first result.

**Met 2026-09-22 against the fake backend** for every bullet but Leavings (proven on
`feat/phase-5-leavings`): `tests/e2e/test_virtual_cells.py` and `test_virtual_cells_night_veil.py`
run each scenario with a real in-Cell Warden per Cell in-process, a real `CellListener` on
loopback and the real lifecycle, provider and pool; "measurably faster" is asserted as one fewer
`cell.provisioning` per reused Cell, since the clocks are fake. Night Veil is proven at the
provider level (attest green proceeds, one red check tears down, never overwinters); a Night Veil
placement through `hive run` waits on `.claude/phase-5-virtual-cells-handoff.md` open item 3.
**Met on real Docker 2026-09-22** (Docker Desktop 4.91, WSL 2): the haiku goal on
`prefer = "virtual"` provisions a container whose own Warden runs the Drone against LM Studio
through the host gateway and verifies it (71 s end to end; 140 s on the Hive Stand), a two-step
goal's second task reuses the first's Overwintered Cell in-process, the Cell's trail segment
merges before teardown, and `hive cells abscond` leaves zero containers. Cross-process dormant
reuse and Night Veil on real infrastructure remain open (`.claude/phase-5-virtual-cells-handoff.md`
section 4a); QEMU is not installed.

### ADRs to write

- `leavings-declared-by-the-plan-decided-by-policy.md` (why the planner declares and the bee
  cannot widen; why `DENY` restores rather than rejects; Leavings versus the Basket; what
  "left as found" means once a ledger exists).
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

- [x] **6.1 Desktop image.** `images/desktop-ubuntu/Dockerfile` on `base-ubuntu`: Xvfb, a light
  window manager, `xdotool`, PulseAudio null sink and virtual source, a browser, fonts. Reports
  `has_display`, `has_audio`, `has_browser`. README documents each package. Landed with nothing
  started at boot: the probe reports `can_start_display` (not `has_display`), `has_audio` and
  `has_browser`, and `tests/integration/test_exoskeleton_docker.py` attaches a desktop, audio and
  a browser inside a real container as the unprivileged `hive` user and checks detach leaves
  nothing running.
- [x] **6.2 Protocols.** `CompoundEye`, `Antennae`, `Buzz` under `exoskeleton/*/base.py`, each
  operating through a `CellSession` so the same backend works inside a Virtual Cell and on a Linux
  Real Cell. Every method documents latency class and failure mode.
- [x] **6.3 X11 backends.** `compound_eye/x11.py`, `antennae/xdotool.py`, `buzz/pulseaudio.py`.
  On a Real Cell with a display and the `exoskeleton:real_display` capability they use it;
  otherwise, if `can_start_display`, attach starts an Xvfb and null sink owned by the lease.
- [x] **6.4 Attach on demand.** `exoskeleton/attach.py`: `attach(cell, session, needs) ->
  ExoskeletonHandle` picks backends from capabilities, starts what is missing through the session,
  registers every process with the lease, and `detach()` stops exactly those. A test asserts no
  display process exists after a terminal-only task.
- [x] **6.5 Exoskeleton tools.** `workers/tools/exoskeleton.py`: `see`, `click`, `type`, `press`,
  `scroll`, `listen`, `say`. Screenshots never logged. `see` is offered only when the bound model
  declares `vision`; `listen` returns a `Transcript` through 6.5a unless the bound model declares
  `audio`. Every action tool takes an optional declared postcondition (expected URL, element text,
  or a region that should change) that the Capping gate verifies afterwards. Landed as the
  `workers/tools/exoskeleton/` package (plus `move`, the `browser_*` tools and read-only
  `browser_snapshot`/`browser_read`); media tools are offered only when every fallback binding
  takes their media too, and images and audio travel as `ToolResultPart.media`.
- [x] **6.5a Ears: the transcription provider.** `llm/transcription.py` (`TranscriptionProvider`:
  `transcribe(audio, language) -> Transcript` with segments and timestamps, `stream(chunks)` for
  push-to-talk, `capabilities`, `health`; `Transcript` and `TranscriptSegment` are HiveMind's own
  models, no library type leaves an adapter), `llm/providers/whisper/` (faster-whisper in
  process, an optional extra, GPU when present, one seat per loaded model instance),
  `llm/providers/openai_compat/transcription.py` (`/v1/audio/transcriptions`, which covers hosted
  Whisper APIs and local servers alike), `FakeTranscription` with scripted transcripts. Bound
  through `ModelSlot.TRANSCRIBER` (3.4), metered by the Fanner, listed on the Forage map as a
  source with its own grade. Contract suite over all three with fixture clips. The same provider
  serves the human's voice at the Entrance (10.5f); Buzz is only its first caller.
- [x] **6.6 Flight recorder.** `exoskeleton/recorder.py`: while an Exoskeleton is attached,
  record every action with its arguments, the screenshot before and after, the accessibility tree
  or DOM snapshot where the fast path has one, the declared postcondition and its result. The
  recording is stored as Nectar with references from the episode record, never in logs or on the
  trail, redacted for secrets at the source. A vision-capable judge (4.10) reviews a recording
  against the goal for the `irreversible` tier before the next step, and sampled audit (4.10)
  reviews the rest after the fact. The Observation Hive plays recordings back (12.4). Landed as the
  `exoskeleton/recorder/` package: the SQLite store in the Hive's database (two tables), a Bee
  Bread `RECORDING` reference, the judge's review through `exoskeleton/surface/evidence.py` and
  `AuditingCappingGate`, and `hive recordings list/show/export` (a self-contained HTML playback)
  until the Observation Hive exists.
- [x] **6.7 Structural assertions and rehearsal.** For browser work the gate prefers assertions
  on structure over pixels: URL, accessibility tree, element text. A browser procedure that will
  be reused is rehearsed against a fixture or staging site first and then promoted as a tool
  through the Royal Jelly Lab (9.3), so production runs execute a capped procedure rather than an
  improvised one. Landed as: URL_MATCHES and ELEMENT_TEXT acceptance, allowed by the planner only
  on Exoskeleton subtasks and checked by the Warden on the attached browser before it detaches;
  `exoskeleton/rehearsal/` (export a verified recording as a `BrowserProcedure`, `rebase` it onto
  a fixture origin, `rehearse` it into a `RehearsalReport`), proven on a real Chromium against the
  served fixture site. The command that promotes a procedure is the Royal Jelly Lab's (9.3).
- [x] **6.8 Fake exoskeleton.** `exoskeleton/*/fake.py`; contract suite over real and fake,
  including the recorder. Landed as three suites: `test_exoskeleton_contract.py` (display, input,
  audio over the fakes and real X11/PulseAudio), `test_browser_contract.py` (the fake browser and
  real Chromium) and `test_recording_store_contract.py` (in-memory and SQLite).
- [x] **6.9 Forager role.** `workers/roles/forager.py`: bounded see/act loop; page content to
  `Nectar`. Landed as the `workers/roles/forager/` package over `workers/roles/bounded_loop/`
  (the Drone's tool loop, factored out: role, prompt, round cap, tools and a per-result hook):
  at most 16 rounds, refused without an attached Exoskeleton, its brief showing its Scout
  dependencies' reports; every `browser_read` and `browser_snapshot` is deposited as Nectar (a
  Bee Bread `TOOL_RESULT` entry until phase 7: scrubbed, capped, at the task's clearance, never a
  frame). The planner allows a Forager only with `needs.exoskeleton`; its grant uses its own
  `[forage.roles]` footprint when the manifest has one, else the Drone's.
- [x] **6.10 Scout role.** `workers/roles/scout.py`: recon with a strict budget returning a
  `ScoutReport` the Queen uses before committing Foragers. Landed as the `workers/roles/scout/`
  package: 6 rounds of read-only tools (page reads, `browser_navigate`, a GET-only
  `http_request`, `read_file`) and `report_findings`, which writes `scout-report.json` through
  the capped write path and ends the loop; running out of rounds files a `feasible=False` report.
  A Scout's acceptance is exactly FILE_EXISTS on that file. The report rides up on
  `TaskResult.scout_report` onto the chamber outcome, and the dispatcher hands the reports of a
  task's SUCCEEDED Scout dependencies (at most 4, newest first) down as `TaskAssign.recon`. An
  infeasible report fails the Scout without retry and cancels every task that waited on it, with
  its reason. `tests/e2e/test_scout_then_forager.py` runs both through a real Queen and Warden.
- [x] **6.11 Playwright fast path.** `exoskeleton/browser/playwright.py`: headed in the Cell's
  display when one exists, headless otherwise; `browser_*` tools that also work without vision
  through the accessibility tree. The only Exoskeleton on Windows and macOS Real Cells for 1.0.
  Landed as the `exoskeleton/browser/playwright/` package, with `ChromiumLauncher` starting the
  lease's own Chromium over CDP; the Windows and macOS locator paths are written but not yet run
  on those systems (ADR-0031's known limits).
- [x] **6.12 Placement integration.** `TaskNeeds.exoskeleton` drives placement: a Real Cell
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
  More detailed writing instructions for pheromone mask be given will be given at time of implementation, ask me (user) for more info when you go to implement
  Not built on this branch (2026-09-24); where it stands is in `.claude/phase-6-handoff.md`
  section 4.1.

### Exit criteria

- The fixture-site login goal succeeds in three placements: a `desktop-ubuntu` Virtual Cell, a
  Linux Real Cell on the Ubuntu runner with a lease-started Xvfb, and the Windows Hive Stand via
  Playwright.
- After each Real Cell run no lease-started display, audio or browser process is alive and the
  left-as-found snapshot holds; no screenshot bytes in logs or trail.
- The login scenario's recording plays back in the Observation Hive with before and after frames
  per action, and a deliberately wrong click fails its declared postcondition, is rolled back on
  the Virtual Cell, and raises an Alarm.

**Met 2026-09-24 on real peripherals in a Linux sandbox, all but the Windows leg**:
`tests/e2e/test_phase6_exit_criteria.py` runs the fixture login, planned as a Forager, through the
real Queen, Warden, Capping gate, flight recorder and Forager: on the Hive Stand with a
lease-started Xvfb, openbox and Chromium; on a `desktop-ubuntu` Virtual Cell (the in-Cell Warden
in process on the same peripherals), where a deliberately wrong click fails its `expect`, is
rolled back by GUI checkpoint and raises `POSTCONDITION_FAILED`; and browser-only with no X11, the
path the Windows Hive Stand takes. After each Real Cell run no lease-started process is alive, the
left-as-found snapshot holds, and neither the captured logs nor any trail payload holds PNG bytes
or their base64 prefix; the recording exports with both frames for every action (`hive recordings
export`, until the Observation Hive's step 12.4). **Met on real Docker 2026-09-24**: the same login
in a real `desktop-ubuntu` container against a scripted OpenAI-compatible server, the container's
own Warden running the Forager and judging a sampled audit (11.6 s, no container left). The
Windows leg is the operator's to run: `docs/runbooks/phase6-windows-exit-check.md`.

**Windows leg met 2026-10-02 on the Windows 11 dev host (Edge 154), scripted model**: runbook
step 2 passes (`1 passed, 2 skipped` for the module), and the real browser contract suite and
the Playwright backend's real-browser tests run there too. Getting there found three defects.
Edge relaunches itself when it inherits an application compatibility layer (`__COMPAT_LAYER=
RunAsInvoker`, handed down by the terminal), so the started process exited, the launch failed,
and the relaunched browser stayed alive outside the lease's process tree. The browser's
environment now clears the layer, and a launch whose started process exits after writing its
port is refused. A loopback site on a Real Cell needs `net:loopback` since ADR-0059, but the Hive
Stand reported no reachable network scopes, so placement could never take such a task; it now
reports `loopback`, and the exit scenario's plan asks for it. After the run, no live process names
the lease's scratch (checked from the process table on Windows as well as /proc). Runbook step 3
(a real model) was tried twice with the local Qwen3.8-27B on LM Studio and got no further than
planning, for serving reasons rather than code ones. LM Studio reloaded the model with 8,192
tokens per slot, so the planner's reasoning hit the context limit, and generation stalled to
nearly 0 t/s while another GPU application was running. Both runs ended in a provider
ReadTimeout and left the stand as found.

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

- [x] **7.1 Embedding protocol and adapters.** `llm/embedding.py` (`EmbeddingProvider`),
  `llm/providers/openai_compat/embedding.py`, `llm/providers/sentence_transformers/embedding.py`
  (in-process, optional extra), `FakeEmbedding`. Bound through `ModelSlot.EMBEDDER`. Contract
  suite over all three.
- [x] **7.2 Schema.** Migrations for `nectar`, `honey` (with embedding model id and provenance),
  `clearance` column (`C0`, `C1`, `C2`) on both tables, FTS5 and `sqlite-vec` virtual tables.
  Changing the embedder slot triggers `hive honey reembed`.
- [x] **7.3 Models.** `Nectar`, `Honey`, `HoneyQuery`, `HoneyHit`; provenance mandatory (task,
  Worker, Cell, time) and clearance mandatory. `honey_store/clearance.py` assigns the label
  deterministically from provenance at intake (anything from a Real Cell, a human message or watch
  mode is `C2`); a model may raise a label during ripening and never lower one; lowering is a
  Capping proposal reviewed by the judge or a human.
- [x] **7.4 Nectar intake.** `honey_store/nectar/intake.py` over Waggle; hash, dedupe, store,
  size cap. Deposited transcripts from checkpoints arrive here.
- [x] **7.5 Ripening pipeline.** `chunk.py`, `summarise.py` (via `llm/structured.py` on
  `ModelSlot.RIPENER`), `embed.py`, `dedupe.py`, `index.py`, composed by `pipeline.py`.
- [x] **7.6 House Bee, complete.** `workers/roles/house_bee.py` now has three duties: sweep hot
  state (phase 4), ripen Nectar, and ripen aged Bee Bread into Honey. Runs in the Queen's process
  by default.
- [x] **7.7 Retrieval.** Hybrid FTS + vector search with manifest weights; result token budget
  scaled by the bound model's window and folded into `memory.assemble` as the cold tier. A
  tainted item (10.6d) is never returned, whatever its score.
- [x] **7.8 Waggle integration.** `HoneyQuery` / `HoneyResponse`; results injected as delimited,
  labelled untrusted content.
- [x] **7.9 Queen pre-check.** The planner queries Honey for the goal's targets and for the chosen
  Cell's known quirks, attaching hits to `TaskAssign`; `queen.honey_consulted`.
- [x] **7.9a Cell Wax ripens and is consulted.** Cleared and expired Cell Wax (4.2a) is ripened
  into Honey at `cell:<id>` scope with the note's provenance and severity, so a Cell's history of
  cautions compounds without living in hot state. The pre-check in 7.9 attaches that history for
  the chosen Cell alongside its live wax, and the browser (7.10) lists a Cell's live wax under its
  folder. Live wax stays a memory-table item, never a Honey row.
- [x] **7.10 Honey scoping and browser.** `honey_store/scope.py`: every Honey row carries a
  scope derived from provenance (`hive`, `cell:<id>`, `bee:<id>`, `task:<id>`) and a visibility
  rule; a bee's `honey:read:<scope>` capabilities decide what its queries can return. Query
  filtering applies both scope and clearance, with Comb Shield policy denying Royal data on
  Night Veil Cells.
  `honey_store/browse.py`: a read-only virtual folder tree over the store (`/hive/...`,
  `/cells/<cell>/...`, `/bees/<bee>/...`, `/tasks/<task>/...`, `/bee-bread/...`) with listing,
  reading and search per folder, so the Observation Hive and the CLI can walk the Hive's knowledge
  the way you walk a filesystem. Browsing never writes; every write to Honey goes through the
  Queen's process (intake, ripening, pins). The browser's "propose a note" files a PROPOSED Cell
  Wax from a Cell's folder, and from any other folder queues the note for the House Bee, which
  runs beside the Queen, to drain into HUMAN Nectar (ADR-0035).
- [x] **7.11 CLI.** `hive honey query|stats|ripen --now|reembed|ls <path>|cat <path>`.

### Exit criteria

- The phase 6 Forager goal run twice: the second `TaskAssign` carries Honey about the site and the
  Forager takes fewer steps.
- A Handoff deposited in phase 4 is retrievable through Honey a day later with full provenance.
- Ripening runs with `RIPENER` and `EMBEDDER` on local adapters in the `local_llm` job.

**Met 2026-09-24, with one stand-in** (branch `claude/vigilant-hawking-qf81zr`; the full record is
`.claude/phase-7-handoff.md`). Phase 6 was not built first (the operator asked for phase 7), so a
Drone stands in for the Forager. `tests/e2e/test_honey_compounds.py` runs one goal twice on the Hive
Stand through `build_hive` (real lease, SQLite, Queen, Warden and Drones, scripted models). At C2
the second `TaskAssign` carries the first run's ripened outcome, `queen.honey_consulted` is on the
trail, and the second Drone finishes in fewer model calls. At C1, the default, the outcome is C2 by
the Real Cell floor and reaches the second run only once an independent judge lowers it (ADR-0038;
cases (d) and (e)). A real run used three separate processes on one database, with the same Hive
Stand Cell id in each, a real subprocess and the House Bee's own loop. RIPENER ran on
qwen2.5-3B-Instruct, JUDGE on qwen2.5-7B-Instruct and EMBEDDER on nomic-embed-text v1.5, local GGUF
models behind llama-cpp-python's OpenAI-compatible server. At C1 the first Drone needed four calls,
and the second and third two each. Re-run this bullet with the Forager once phase 6 lands. The
Handoff bullet is `test_a_phase_four_handoff_is_retrievable_a_day_later_with_full_provenance`: a
Handoff written by `memory.write_checkpoint` is deposited by the Queen's housekeeping sweep a day
later, ripened, and returned with task, Worker, Cell and time. The `local_llm` bullet is
`tests/evals/honey/test_ripening_local.py`, which builds the store from `docs/manifests/local.toml`
through the production builder. It passed on that server with qwen2.5-3B-Instruct and
nomic-embed-text v1.5, and with Qwen3-Embedding 0.6B in process through sentence-transformers. Where
the steps above differ from what was built: there is no `vec0` table (ADR-0035 measured it no
faster); re-embedding is progressive and automatic rather than triggered (ADR-0036),
`hive honey reembed` drains the backlog on demand and `--prune` drops other models' vectors
(ADR-0037); a deduplicated deposit keeps its own source (ADR-0037); lowering a label (7.3) is a
proposal the House Bee files and the JUDGE slot or the operator (`hive honey review`) decides, with
Capping's discipline but outside `CappingGate`, whose actions are Cell actions (ADR-0038); step 7.10
now describes the note path as built, a queued note the House Bee drains rather than a message in
the Queen's inbox; `browse` and `house_bee` are packages, not single modules.

### ADRs to write

- `honey-store-sqlite-fts5-sqlite-vec.md` (written: `docs/adr/0035-*`).
- `embedding-provider-and-reembedding-policy.md` (written: `docs/adr/0036-*`).
- `honey-keeps-repeat-sources-lists-scopes-and-prunes-on-request.md` (written: `docs/adr/0037-*`).
- `honey-label-lowering-is-a-judge-reviewed-proposal.md` (written: `docs/adr/0038-*`).

---

## Phase 8: Local models and provider routing

**Goal.** Moving from Claude to locally hosted models is a configuration exercise: every slot can
run on a local model, the Hive can run with no external network, routing weighs Forage and where
the model runs, and there is measured evidence for which slots should stay hosted.

**Depends on.** Phases 4 and 7.

**Deliverables.** Routing policy, offline mode proven end to end, evaluation harness, per-provider
prompt overlays, `hive llm eval`.

### Steps

- [x] **8.1 Routing policy.** `llm/routing/` (a package; built 2026-10-11, ADR-0045): pure
  `choose_binding(RoutingInputs) -> RoutingDecision`, the inputs being the slot, its `Tempo`, the
  capability flags it needs (not `TaskNeeds`, so `llm` stays below `cell`), the Forage map's
  sources, the Cell's hosting plan, the `[llm.slots]` rows and `RoutingRules` (`[llm.floors]`,
  `offline`, the spend cap, a raised floor); the decision names the key to bind first, the whole
  chain reordered behind it, the effort, the reason, what was passed over and why, and a seat
  shortfall. It honours fallback chains (it reorders the slot's own chain, never invents a
  binding),
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
  so the mapping is tuned from measurements, not guesses. As built: the Warden routes in
  `wardens/spawn/route.py` before the Guard authorises the routed key, binds the sub-bee along
  the routed order with the routed effort on its first link, records `llm.fallback` when the
  slot's own key was passed over, files one `SHARED_SEATS` request on a shortfall without holding
  the spawn, and its own rebind on an escalated Alarm routes again with the floor raised by the
  attempt; the Queen's hosting plan reads the same floors. Proved on the Hive Stand by
  `tests/e2e/test_routing_on_hive_stand.py` (CRITICAL lands on the grade-4 fallback with one
  `llm.fallback`; LOW stays on the grade-2 primary). A handoff successor and a Queen-ordered
  REBIND still bind a plain key's manifest chain.
- [x] **8.2 Local server management.** `llm/providers/ollama/manage.py` (optional): list, pull,
  `keep_alive`, health. `hive llm models|pull|keep-alive` and `hive llm providers` use it today;
  `hive doctor` (step 14.2) will. Generic health probe covers vLLM and llama.cpp.
- [x] **8.3 Model server on a Cell.** `wardens/local_pool/hosting/` (a package: `host`, `launch`,
  `sources`, `report`, `config`, `door`; the manifest declares each server in `[llm.servers]`; it needs the Cell's
  `CellSession`, so it lives with the Warden that owns the Cell, not in `forage`): start and stop
  an OpenAI-compatible server on a Cell that reports `can_host_model` (through its session), register
  it as a provider with a loopback-only or Cell-local base URL, add its models to the Forage map
  as new sources with measured distance from that Cell, and report its seats to the ledger as
  part of that Cell's local pool. Weights come from the Basket (9.2a) when the Hive already holds
  them, shipped from the Hive Stand rather than downloaded again. The Cell's Warden loads and
  unloads models on it autonomously
  within its ceilings and allowlist, reporting each as `forage.source_added` / `removed`; the
  Queen may evict a model only to reclaim seats she has exported. This is what local sources in a
  hosting plan and Nucs (phase 11) build on. As built (2026-10-11, ADR-0044): distance is
  measured by the Fanner on the first call rather than at registration; the Basket does not exist
  yet, so weights are the server's own concern until 9.2a; the Hive Stand reports
  `can_host_model` exactly when the manifest declares a server; both the Warden and the Queen
  record `forage.source_added`/`removed` (a `reporter` field tells them apart). Proved on the
  Hive Stand with the scripted OpenAI-compatible server as the server process
  (`tests/e2e/test_model_server_on_hive_stand.py`): started through the lease, healthy, on the
  map with the Stand as host, every call of a haiku goal on it, gone with the lease.
- [ ] **8.4 Prompt portability pass.** Run every prompt snapshot and the phase 3 scenarios against
  at least two local models of different sizes; add overlays under `llm/prompts/overlays/<provider>/`
  where needed, each with a header saying why. **Mechanism built 2026-10-11** (ADR-0044): overlays
  are directories under `llm/prompts/overlays/<name>/` applied at the provider door by
  `OverlayingProvider` when `[llm.providers.<name>] prompt_overlay` names one, so the shared
  prompts stay portable and no call site knows; `small_local` ships. The pass itself (two real
  local models of different sizes) needs a model server this container cannot reach; it is the
  operator's to run with the `local_llm` job and `hive llm eval`, adding overlays where a model
  needs them.
- [x] **8.5 Evaluation harness.** `tests/evals/` with a fixed goal set and graders (decomposition
  validity, `TaskNeeds` correctness, tool-call validity rate, ladder rung reached, Alarm rate,
  handoff-resume success, step count, cost, wall time), run by `hive llm eval`, writing
  `docs/evals/<date>.md`. `JUDGE` may itself be local. With `--update-map` the harness writes
  measured per-slot scores back onto the Forage map as `measured_grade`, so hand-set grades are
  replaced by evidence and routing's grade floors are grounded in the Hive's own results.
  `TRANSCRIBER` is graded by word error rate and latency on a fixture clip set, so the default
  Whisper size is chosen from measurements.
- [x] **8.6 Offline mode end to end.** `docs/manifests/offline.toml`; a `local_llm` e2e job runs
  the haiku goal on the Hive Stand and in a Virtual Cell, the ripening scenario, a spoken goal
  transcribed on the in-process Whisper, and a Clustering round trip, with no network egress.
  As built (2026-10-11): `tests/e2e/offline/` runs every scenario in CI's e2e job over the
  scripted OpenAI-compatible server on loopback, each manifest derived from `offline.toml`, under
  a socket guard that fails any connect to a non-loopback address, asserting every `llm.call`
  names a provider that runs locally; the real-model halves (Ollama at two sizes, in-process
  Whisper) are `tests/evals/offline/`, run by the `local_llm` job in `integration.yml` on demand
  and nightly, never on push. The scripted runs found and fixed three defects no fake had shown:
  the Clustering pause raced the Handoff, the health probe crashed on an in-process embedder, and
  the transcriber never closed its connections.
- [x] **8.7 Local vision.** The Forager scenario with a local vision model in `WORKER`, or the
  accessibility-tree path where none is available. As built: `tests/e2e/offline/
  test_offline_vision.py` logs in with a real Chromium on a provider declared without vision,
  through `browser_snapshot` alone (no screenshot tool offered, no image part sent), and with
  vision through screenshots, so the capability is the switch; a real local vision model is the
  `local_llm` job's (`tests/evals/offline/test_offline_local.py`).
- [x] **8.8 Cost and token accounting for local models.** Zero or amortised prices in the
  manifest; usage from the response or a documented estimate; both uniform on the trail.
- [x] **8.9 Runbook.** `docs/runbooks/moving-a-slot-to-local.md`.

### Exit criteria

- The haiku goal, ripening, the browser Forager scenario and a Clustering round trip complete with
  `offline.toml`; every `llm.call` on a local provider.
- `docs/evals/` holds a table comparing Claude and two local models across every slot.
- Switching any slot between providers changes only the manifest; a checksum of `src/` is
  identical between the two configurations in the e2e job.

**Met 2026-10-11 for bullets 1 and 3, with the scripted loopback server standing in for the local
model** (branch `ccr-0235b6d7-okyhmn`; the record is `.claude/phase-8-handoff.md`). This container
reaches no model hub, so every `local_llm` case skipped here and the real-model proof is the
`local_llm` job's. Bullet 1: `tests/e2e/offline/` runs the haiku goal (Hive Stand and Virtual
Cell), ripening, the browser Forager login and a Clustering round trip with manifests derived from
`offline.toml`, under a no-egress socket guard, every `llm.call` on the loopback provider. Bullet
3: a sha256 over every `packages/*/src/**/*.py` is identical across the offline and the
fake-provider runs. **Bullet 2 is not met**: `hive llm eval` writes `docs/evals/<date>.md` and
`--update-map` writes measured grades (proved on the fake provider and on the scripted server, where
it wrote `scripted_local` grade 4), but the table comparing Claude and two local models across
every slot needs a hosted key and a model server; the `local_llm` job produces the local columns
and the runbook names the hosted run. Where the steps differ from their text: routing returns a
`RoutingDecision` over the slot's own chain rather than a `BoundModel` (ADR-0045); the Hive Stand
reports `can_host_model` exactly when `[llm.servers]` is declared; overlays are keyed by a
manifest-chosen name (`prompt_overlay`), not by provider; measured grades live in the Queen's
ledger, not in the manifest.

### ADRs to write

- `local-model-serving-via-openai-compatible-api.md` (written: `docs/adr/0044-*`).
- `provider-routing-forage-aware-fallbacks-and-offline-mode.md` (written: `docs/adr/0045-*`).

---

## Phase 9: Royal Jelly Lab + Comb Registry

**Goal.** The system extends itself safely: a Worker or Warden requests a capability, the Lab
scaffolds a tool, the Quarantine Comb proves it in a sandbox Cell, and only then is it promoted, at
hive scope by the Queen or at cell scope by the requesting Warden.

**Depends on.** Phases 5 and 7.

**Deliverables.** `hivemind/royal_jelly`, `hivemind/basket`, `hive tools` and `hive basket` CLI.

### Steps

- [x] **9.1 Tool spec format.** `ToolSpec` (name, version, model-facing description, input
  schema with `additionalProperties: false`, required capabilities, entry point, dependencies,
  supported OSes, `scope = "hive" | "cell"`). `validate.py` rejects specs requesting capabilities
  beyond the requester's own. **Built 2026-10-12** (ADR-0046): `royal_jelly/spec/` (`models.py`,
  `validate.py`, `package.py`, `report.py`). `validate_spec` is pure and also refuses a schema that
  is not a closed object, a `tool:` capability, a CELL scope without a Cell and an entry point other
  than `tool:run`; a tool package is a byte-identical zip of `tool.py`, `test_tool.py` and
  `spec.json` named by its sha256, and `QuarantineReport` is defined beside the spec because the
  registry reads it.
- [x] **9.2 Comb Registry.** SQLite table + package dir, each package a Basket blob named by hash
  (9.2a); `CombRegistry` with `list`, `get`, `promote(spec, report, scope)`, `retire`. `promote`
  refuses without a passing `QuarantineReport`;
  hive scope requires the Queen principal; cell scope requires the owning Warden. A test asserts
  there is no bypass. **Built 2026-10-12** (ADR-0046): `royal_jelly/registry/` keeps the package
  hash, never the bytes (the Basket holds them); `promote(spec, report, offer, principal, reason)`
  is the only path to a PROMOTED row and `tests/unit/royal_jelly/registry/test_no_bypass.py` proves
  every refusal (a failing or mismatched report, HIVE from a Warden, CELL from the Queen or another
  Cell's Warden, a repeated version, an unreviewed REAL_CELL path at HIVE scope) and that no other
  public method yields a current PROMOTED row. SQLite triggers make the evidence columns immutable
  and refuse DELETE.
- [x] **9.2a The Basket.** `hivemind/basket/`, a package at Layer 2 beside `honey_store` (add it
  to the codingrules layer table and the `import-linter` contract in the same PR): `models.py`
  (`Blob`: content hash, size, media kind as a descriptive tag, platform tags, `HoneyClearance`
  from provenance, origin task, bee and Cell, deposit event id; `BlobManifest`, signed by the
  Hive key at deposit), `store/` (SQLite metadata plus a blob directory on the Hive Stand, the
  Comb's own pattern; `deposit`, `get`, `retire`, dedupe by hash), `residency.py` (per Cell, the
  blobs kept resident under the `basket_disk_mb` ceiling from `Ceilings` (4.8), evicted oldest
  first, recorded with the node like resident tools on a colonized device (11.6a) and kept on
  an Overwintered Cell's volume (5.9)), and `transfer.py` (chunked under the Waggle frame cap,
  resumable by hash, always through the Hive Stand since nodes connect out to the Queen; the
  receiver verifies hash and signature before keeping a byte, and a failure is a `guard.*`
  integrity event). `basket.*` Waggle messages (`offer`, `chunk`, `ack`, `refuse`) are a waggle
  minor bump. Every deposit writes one Honey record for the blob (9.8's pattern): a title and a
  one-paragraph summary the proposing bee supplies with the `retain` proposal, plus the hash
  that locates it, kind, platform tags, clearance and origin. That record is the only way a bee
  finds a blob: retrieval (7.7) returns the few records that match under the token budget, and
  `memory.assemble` never lists the Basket, so a Cell holding a thousand blobs costs a prompt
  nothing; `hive basket list` is for the human. The rule, tested: **a blob is inert**. Holding
  one grants nothing; running a script from it is a capped command, installing a program from
  it is an outside-scratch write with its restore path (3.17), and only a Comb promotion makes
  a blob executable as a tool, by naming its hash as the package. Nothing enters by side
  effect: a bee proposes to keep an artefact (a `retain` proposal through the Capping gate,
  checked and trailed like any side effect), its Warden deposits it, `basket.deposited`
  records it.
  Text-like blobs pass the scanner (10.6b) at deposit; binaries cannot, and so never run
  except through the Comb. Clearance rules apply unchanged (a Real Cell origin is `C2`, so
  nothing from a laptop ships to Night Veil), and a blob from an isolated or quarantined
  source is tainted (10.6d). `hive basket list|show|deposit|ship|evict|retire`. Absconding
  (13.4) removes every resident copy. **Built 2026-10-12** (ADR-0048): `hivemind/basket/` at Layer 2
  (`models.py`, `manifest.py`, `clearance.py`, `deposits.py`, `residency.py`, `store/`,
  `transfer/`), with `basket.*` Waggle messages in minor 1.12 and `BasketEvent` on the trail.
  Deviations from the text: the Honey record goes through a port the composition root fills with a
  Nectar submission (Layer 2 siblings never import each other); the clearance floor is restated from
  `honey_store.clearance` for the same reason; `retain` is a capped COPY whose verified result
  reaches the Warden's sink in process, the Warden deposits (Hive Stand) or ships up signed with its
  node key and the Queen re-deposits through the signed door; `resident_basket_disk_bytes` on
  `Ceilings` is the ceiling, with the `[basket]` default where it is 0; a Warden-to-Warden shipment
  is two legs through the Hive Stand. Absconding's removal of resident copies is phase 13's.
- [x] **9.3 Scaffolder.** From a `ToolRequest` produce spec, source and tests through
  `llm/structured.py` on `ModelSlot.SCAFFOLDER`; tests first. The request carries the target
  Cell's capability report (OS, distro, arch, package manager, shell, Python availability) so the
  scaffolder writes for that platform and declares it in `supported_os`. Generated tools act only
  through the `CellSession` they are handed; a direct `subprocess` import fails the Comb. A Warden
  may run the scaffolder locally within its grant. **Built 2026-10-12**: `royal_jelly/scaffold/` on
  `ModelSlot.SCAFFOLDER` through `complete_structured` with `llm/prompts/scaffold_tool.md` (tests
  first; `async def run(session, arguments)`; standard library plus `hivemind.cell` only); the model
  never sets the tool's name, scope, ids or capabilities, and `validate_spec` proves it widened
  nothing. A Warden scaffolds locally on the first SCAFFOLDER binding its grant allows
  (`wardens/ticks/tools/cell_scope.py`), else answers `hive.tool.no_scaffolder`.
- [x] **9.3a Quarantine for non-Ubuntu targets.** The Comb's sandbox is an Ubuntu Virtual Cell,
  so a tool written for Windows, macOS or another distro cannot be fully exercised there. Until
  Virtual Cell images exist for those platforms (post-1.0), such tools pass static checks and
  schema fuzzing in the Ubuntu sandbox, then run their tests inside a lease's scratch directory on
  a Real Cell of the target platform under a dedicated `tool:quarantine_on_real` capability, with
  the flight recorder on and the tier set to `device_command`. The report says which path was
  used; hive-scope promotion of such a tool needs the Queen's awake review, not just autopilot.
  **Partly built 2026-10-12** (ADR-0047): `quarantine_comb/path.py` sends a target whose platform is
  not the sandbox image's Linux distribution down `REAL_CELL`; the `tool:quarantine_on_real` flag
  family exists and is held by no role below the two roots; the report carries the path and the
  registry refuses an unreviewed REAL_CELL path at HIVE scope until the Queen's awake review
  (`queen/ticks/tools/review.py`) accepts it. **Not built:** the Real Cell tests-gate leg itself (a
  sandbox over a lease's scratch on a Real Cell of the target platform):
  `QuarantineComb.run(path=REAL_CELL)` only stamps the report. No second-platform Real Cell exists
  in CI to run it.
- [x] **9.4 Sandbox protocol.** `Sandbox` protocol; `sandbox_cell.py` (a Virtual Cell with
  `network = none`, limits, read-only mounts; never a Real Cell); `sandbox_subprocess.py` (dev
  only). A Warden needing a sandbox files a `CellRequest`; the Queen provisions one for the Comb.
  **Built 2026-10-12** (ADR-0047): `quarantine_comb/sandbox.py`, `sandbox_cell.py` (refuses a
  borrowed Cell by `is_borrowed`, never `kind`) and `sandbox_subprocess.py` (dev only,
  `[royal_jelly] sandbox = "subprocess"`, warned at build). Deviation: `CellRequest` is not used. A
  Warden sends `tool.quarantine` to the Queen, which provisions a `network_policy = NONE` Virtual
  Cell, runs the Comb there through that Cell's Warden and relays the report back; the sandbox stays
  the Queen's and is torn down after the report.
- [x] **9.5 Quarantine Comb.** Gates in order: static checks with a banned-imports list, the
  tool's tests, schema round-trip, fuzz over the input schema, resource limits, capability audit.
  Emits a `QuarantineReport`. **Built 2026-10-12** (ADR-0047): six gates in `GATE_ORDER` (static,
  tests, schema, fuzz, resources, capabilities) under `quarantine_comb/gates/`; the in-sandbox
  `runner/` package applies rlimits, a `sys.addaudithook` that blames a spawn, a socket or a read
  outside the package only when the innermost non-stdlib frame is the tool's, runs the `unittest`
  suite with the module-level `SESSION`, the schema round trip and the fuzz inputs, and writes one
  JSON report the gates read.
- [x] **9.6 Promotion flow.** request → scaffold → sandbox request → quarantine → (promote at
  scope | reject with report), a `tool.*` event per stage, one retry with the report. **Built
  2026-10-12**: `royal_jelly/promotion/flow.py` walks `ToolState` (`QUARANTINING → SCAFFOLDED` added
  for the one retry, `REQUESTED → REJECTED` for a first scaffold that fails) with a `tool.*` event
  per stage; the Queen runs it beside her tick (`queen/ticks/tools/`), ships the package as a Basket
  blob, orders `tool.quarantine` and awaits the correlated `tool.quarantine_report`; the requester
  is answered with `tool.promoted` or `control.error`.
- [x] **9.7 Worker integration.** A Worker loads only the bundle its Warden hands it at spawn,
  attenuated from the Queen's grant, never the registry (README, core concept 10). A promotion
  updates the registry and deposits Honey (9.8); it pushes nothing into a running Worker. A
  Worker's bundle changes only when its Warden re-attenuates it, on a fulfilled `ToolRequest`
  or a smaller minimum for the next step, as an intervention. `workers/tools/invoke.py`
  re-validates inputs, capabilities and OS before every call. Tool packages arrive as Basket blobs
  (9.2a), resident on the Cell and verified by hash before load. **Built 2026-10-12**:
  `workers/tools/comb/` (`invoke.py` re-validates schema, capabilities and OS before every call and
  loads a package only through a `CombToolEntry` whose hash the resident bytes match); a Worker's
  `CombToolBundle` changes only when its Warden replaces it (`wardens/ticks/tools/install.py`, after
  the blob is resident and verified), and the bounded loop rebuilds its registry on
  `ToolsChangedError`. A Cell attached after a promotion is caught up on attach, blob first.
- [x] **9.8 Honey link.** Every promotion deposits Nectar describing the tool. **Built 2026-10-12**:
  `royal_jelly/honey_link.py`; the Queen deposits on every promotion she makes or accepts, the Hive
  Stand Warden on its own cell-scope promotions.
- [x] **9.9 Versioning and rollback.** Immutable versions; `retire`; `hive tools rollback`. **Built
  2026-10-12**: `retire` keeps the row; `rollback` re-currents an older PROMOTED row whose stored
  report still passes, recorded as `tool.promoted` with `supersedes_version` plus
  `tool.rolled_back`; `tool.retired` is a Waggle 1.12 event (spec open point 35).
- [x] **9.10 CLI.** `hive tools list|show|request|retire|rollback|quarantine <path>`. **Built
  2026-10-12**: `cli/tools/` and `cli/basket/` (`hive basket list|show|deposit|ship|evict|retire`);
  `--manifest` is a group option (`hive tools --manifest M list`); `quarantine <path>` runs the Comb
  in the subprocess sandbox on a package directory.

### Exit criteria

- A Forager needing a missing capability triggers scaffold → sandbox → Comb → promote → the same
  Forager completing with the new tool, all on the trail.
- A Warden requests a cell-scope tool for its machine, gets a sandbox from the Queen, promotes it
  after the Comb passes, and only bees on that Cell can see it.
- A malicious scaffold (socket, `subprocess`, reads outside its dir) fails the Comb with the reason.
- The scaffold scenario passes with `SCAFFOLDER` local in the `local_llm` job.
- A tool promoted at hive scope reaches a second matching Cell as a Basket blob verified by
  hash; a blob deposited from a Real Cell is refused by a Night Veil Cell; a blob with no Comb
  entry cannot be invoked as a tool, and the refusal is on the trail.

**Met 2026-10-12 with the dev subprocess sandbox standing in for Docker, and the Forager's
browser faked** (branch `ccr-4e10bf6f-y9zk5g`; the record is `.claude/phase-9-handoff.md`). No
Docker reaches this container, so every sandbox is either `[royal_jelly] sandbox = "subprocess"`
on the Hive Stand or a `ContainerSpawningFakeCellBackend` Virtual Cell whose real in-process
Warden runs the Comb over its `InCellSession`; only the container itself is faked.
`tests/e2e/test_phase9_exit_criteria.py` proves bullets 1 to 3: a real Forager on the Hive Stand
calls `request_tool` for a missing capability, the Queen scaffolds (scripted SCAFFOLDER), deposits,
quarantines in the subprocess sandbox, promotes and announces (`tool.requested, scaffolded,
quarantine_requested, quarantined, promoted, announced, loaded` in that order), and the same
Forager finishes with the tool's output; a Drone on the Hive Stand asks at cell scope, its Warden
scaffolds on the SCAFFOLDER binding in its grant, the Queen provisions a sandbox Virtual Cell for
it, and the Warden promotes with its own principal, after which `visible_tools` lists the tool for
that Cell only and a Worker on another Cell is never offered it; a scaffold importing `socket`,
one calling `subprocess.run` and one reading outside its directory each fail the static gate with
the construct named, twice, then `tool.rejected` and `control.error hive.tool.rejected`, with no
registry row. Bullet 4 is `tests/evals/scaffold/test_scaffold_local.py`: the same scenario with
only the SCAFFOLDER slot moved to `docs/manifests/local.toml`'s server, gated by the `local_llm`
marker; it passed against a scripted loopback stand-in and has not run against a real model here.
Bullet 5 is `test_phase9_exit_criteria_basket.py`: a hive-scope tool reaches a Virtual Cell that
attaches later (`basket.offered` on the Queen's trail, `basket.received` on the Cell's, the resident
bytes hashing to the registry's `package_sha256`); a C2 blob from the Hive Stand is refused by the
Night Veil rig with `basket.refused` (CLEARANCE) while a C0 blob is kept; a dataset deposited and
never promoted gives no tool, and a forged `tool.promoted` whose package is absent is refused with
`tool.load_refused`. One reading differs from the bullet's text: a call to a tool that was never
offered is refused by the tool ladder before any registry, so it leaves no `guard.denied` row; the
trail refusal is `tool.load_refused`. Where the steps differ from their text: a sandbox is asked
for with `tool.quarantine`, not `CellRequest` (ADR-0047); the Real Cell tests-gate leg of 9.3a is
not built; no Worker role holds `tool:request` in the shipped policy (the scenarios grant it per
manifest), and an in-Cell Warden reads no `[guard]` section, so a Worker on a Virtual Cell cannot
request a tool yet.

### ADRs to write

- `basket-inert-blobs-residency-and-transfer.md` (bytes travel, permission does not; Honey
  describes, the Comb permits, the Basket stores; residency under a ceiling; verified transfer
  through the Hive Stand).
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

**Deliverables.** `hivemind/guard` (capabilities, policy, the scanner), `hivemind/entrance` (two
listeners, enrolment, auth, push, exposure, the Entrance Reducer), Guard Bee role, Cell
isolation (Queen-only), `docs/entrance/`, `hive entrance` and `hive keys` CLI.

### Steps

- [x] **10.1 Capability model.** Extends the `CapabilitySet` from 3.13a with every family:
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
- [x] **10.2 Policy engine.** `[guard]` manifest section (per-role default sets, deny lists,
  escalation rules), pure `evaluate` with a reason; denials are `guard.*` events.
- [x] **10.3 Enforcement points.** Placement, lease creation, grant issue, Warden spawn, tool
  invocation, session calls outside scratch, Exoskeleton attach on a real display, Honey access,
  slot binding and rebinding, question routing to the human, Nuc promotion, device commands,
  tactic invocation, and Comb Shield egress policy activation. A test enumerates them and fails if a new state-changing action
  lacks one.
- [x] **10.3a Night Veil guardrails.** Enforce at policy level that `cell:comb_shield:night_veil`
  implies `cell:virtual` and forbids `cell:real:*`; enforce that Night Veil model slot bindings are
  local-only; enforce that a Night Veil Cell's Waggle transport may reach the Hive Stand only
  through the Tor SOCKS proxy to its hidden-service address, never the VPN interface or the default
  route; and enforce that Night Veil capability
  sets allow `honey:clearance:c0` and `honey:clearance:c1` while denying any attempt to read or
  write `c2` Honey.
  **Cut off, never rerouted (phase 11 review, 2026-10-14).** Phase 11's exit bullet, "killing the
  Tor daemon while leaving the VPN and default route up clusters the Cell instead of falling back",
  had no code behind it: a Night Veil Cell's Warden dialled through the bare transport every Cell
  has, which gives up when its connection drops, so Tor dying left a Warden with a dead link, no
  outage and no clustering. Its Warden is now built over the mending link a colonized device's has
  (`cli/in_cell/redial/`): a dialer that can dial only through the SOCKS proxy, a
  `ReconnectingQueenLink` and an `Outage`. A Cell whose Tor dies goes OFFLINE, redials the proxy
  with backoff and nothing else, clusters fully past the Hive's `[supervision] max_offline_s`
  (told to the Cell as `HIVEMIND_MAX_OFFLINE_S`, since a Cell has no manifest), and is taken back
  by the Queen as a reconnection when Tor answers again. `tests/e2e/test_night_veil_tor_loss.py`
  stops a fake Tor under a working Cell, and `tests/unit/cli/in_cell/test_main_night_veil_redial.py`
  does so at the entry point. The Queen now resumes the tasks of a
  Virtual Cell that clustered fully when it returns (a device's come back through the swarm gate's
  hold, which has no Virtual counterpart): its first Heartbeat past OFFLINE after the reconnection
  says CLUSTERED, and she resumes from the recalled Handoff (`queen/cluster/returned.py`, ADR-0053's
  2026-10-27 amendment).
- [x] **10.3b Tier inheritance enforcement.** Dispatch binds a task to the target Cell's
  `CombShieldLevel`; no runtime path may weaken controls after placement. A task moved between
  Cells is re-evaluated and re-bound to the new Cell's tier before resume.
- [x] **10.3c Night Veil initiation policy.** Enforce that Night Veil placement may only be
  initiated by explicit human request through the inbox or API, never by autonomous escalation.
- [x] **10.3d Night Veil location guardrails.** Enforce deny-by-default for location-sensitive
  capabilities on Night Veil Cells (`geo:*`, Wi-Fi scan, host metadata access), and reject task
  tool plans that request them.
- [x] **10.4 Principals, the operator and device keys.** Principals: the human operator, Queen,
  Warden, Worker, Swarm device, and **enrolled client device**. Brood 1.0 has exactly one
  operator; the password is Argon2id-hashed in the Entrance tables and set at `hive init`.
  Clients never hold a shared API key: each enrolled device holds its own keypair, a WebAuthn
  passkey with user verification in browsers (and through the Android credential manager in the
  Capacitor build) or an Ed25519 key in secure storage for programs. Every Waggle-side principal
  keeps its Ed25519 keypair as before. Secrets are hashed or in the secret store; nothing in the
  manifest.
- [x] **10.5 Hive Entrance.** `entrance/app.py` (composition root) running **two listeners**:
  loopback on `[entrance] bind`, always on, and a remote listener on `remote_bind` only when exposed
  (10.5a). `entrance/routes/` with one file per resource under `/v1/` (`goals`, `tasks`, `cells`,
  `wardens`, `forage`, `inbox`, `chat`, `episodes`, `tools`, `honey`, `trail`, `swarm`, `llm`,
  `devices`; `honey` serves 7.10's browser, and its "propose a note" queues the note the way
  `hive honey propose` does rather than writing); every route declares which listeners serve it, and
  the loopback-only set (approve, deny, unlock, widen capabilities, reopen, operator add) is
  asserted by the route test from coding rules 8.11, where a loopback-only route on the remote
  listener is a 404 and not a 403. `entrance/streams/` with one live WebSocket stream per view:
  trail events, telemetry per bee, Forage ledger deltas, task graph deltas per principal (the Queen
  and every Warden), episode records, Cell status, Entrance security events. The **human inbox and
  chat**: free-text messages from the human enter the Queen's inbox as `HumanMessage` items the
  Attendant scores; the Queen's replies, her questions, and Alarms that reached her come back on the
  same channel, so the chat is the human end of the inbox rather than a separate path into the
  system. `entrance/landing_board.py` generates the OpenAPI document from the route models, marking
  loopback-only routes; it is committed as `docs/entrance/openapi.json` and CI fails when the
  generated document differs. The Entrance also serves `packages/observation-web/`'s build as static
  files.
- [x] **10.5a Remote exposure.** `entrance/expose.py` and the `[entrance]` manifest section
  (coding rules 13): `expose = "loopback" | "vpn" | "lan" | "tunnel"`, `remote_bind`,
  `public_url`, `tls`, `mutual_tls`, `rate_limit_per_device`. `loopback` is the default and the
  loopback listener never goes away. `vpn` is the recommended remote path: the remote listener
  binds to the overlay interface (WireGuard or Tailscale, chosen in the ADR) and nothing else, so
  unauthenticated packets never reach the Entrance. `lan` and `tunnel` require TLS and mutual
  TLS with the device certificate on top of login, and refuse to start without both; `tunnel`
  runs the tunnel client as a supervised child of the Entrance. There is no `public` value.
  Per-device rate limiting, CORS only for `public_url`. A test starts the Entrance in every mode
  and asserts every refusal.
- [x] **10.5b Push channel.** `entrance/push/`: `PushChannel` protocol; `websocket.py` for live
  clients, `webhook.py` (signed with the Hive key, retried with backoff, idempotent by event id),
  `web_push.py` (VAPID; keys from `HIVEMIND_ENTRANCE_VAPID_*`), and the native channel the
  Android build registers through (12.12). Subscriptions are per device, filtered by capability,
  persisted, and re-validated on start. Pushed: a question for the human, an Alarm that reached
  the human, a reply from the Queen, completion of a goal the device submitted, every Entrance
  security event. Payloads say only that something is waiting, never the content. A question
  answered on any device is withdrawn from every other; a test asks from the CLI, answers by
  webhook, and asserts the web-push copy is withdrawn.
- [x] **10.5c Landing Board contract and client guide.** `docs/entrance/landing-board.md`: the
  three calls a client needs (submit a goal, subscribe, answer) with `curl` examples, the
  enrolment flow from the device's side, the push contract, and the versioning rule (additive
  within `/v1/`, breaking means `/v2/` with `/v1/` kept for one Brood). A conformance test drives
  the Entrance through the committed OpenAPI document with a generic client and nothing else, so
  a third-party program written from the document alone is known to work. A Python client
  package is post-1.0.
- [x] **10.5d Device enrolment, approved at the Hive Stand.** `entrance/enrol/`: `hive entrance
  invite --device "phone"` on loopback mints a short-lived, single-use invite shown as a code and
  a QR; the device opens the Entrance, presents the invite, generates its keypair and sends the
  public key with a self-description; the request lands in a pending table and is pushed to every
  enrolled device as "a device is asking to join"; `hive entrance approve <id> --capabilities ...
  --spend-cap ... --expires ...`, or the Observation Hive on loopback, approves it; `deny` refuses
  it; pending requests expire. `revoke` lists the device's open goals and, with `--cancel-goals`,
  cancels them in the same step; the Observation Hive's revoke screen (12.8a) offers the same
  toggle, and the revocation event names any goals left running. Approval, denial, unlock,
  capability widening and revocation are loopback-only routes. `[entrance] steward_devices` (off
  by default) lets a device flagged
  `entrance:steward` approve after full step-up, and nothing else can. The device state machine
  (`INVITED → PENDING → APPROVED`, `PENDING → DENIED | EXPIRED`, `APPROVED ↔ LOCKED`,
  `→ REVOKED`) lives in `entrance/enrol/state.py` (Appendix C); every edge is a
  `guard.entrance_*` event (a trail kind has exactly one dot).
  *The Observation Hive's approve and revoke screens are 12.8a's; the loopback routes they call
  are these, and `hive entrance` drives the same routes today.*
- [x] **10.5e Login, sessions, step-up and the Entrance Reducer.** `entrance/auth/`: login is
  the device key (a passkey assertion with user verification, or a signed challenge) plus the
  operator password; sessions carry `session_ttl_hours` and `idle_timeout_minutes` and are bound
  to the device key (a token presented without a matching device signature is refused); step-up
  re-runs both factors, is valid for `step_up_window_minutes`, and is required for spend above
  `step_up_spend`, key and capability changes, Supersedure, Sting Cut, Absconding and reopening;
  break-glass actions add the typed confirmation phrase on every path. Lockout after
  `lockout_attempts` failures, or a burst of capability denials from one device past
  `lockout_denials` (a submit-only program suddenly calling observe routes), moves the device to
  `LOCKED` until a loopback unlock; rate limits per device and per address.
  `entrance/reducer.py`: `hive entrance reduce` drops the Entrance to
  loopback only and revokes every remote session; a Guard Bee autopilot rule does the same on
  failure bursts, cross-device lockouts, or an unknown client hammering the invite route;
  `hive entrance open` on loopback with step-up reopens. `travel_lock` (off by default) forces
  step-up and a notification when a known device appears from a new network and never approves
  anything. Tests: a stolen session token without the device key is refused; an approve request
  on the remote listener is a 404; the Reducer closes a live WebSocket subscription within a
  second.
  *The Guard Bee's door rules (failure bursts, cross-device lockouts, invite abuse, request
  forgery) reduce a real Entrance under `hive serve` since 10.6. Supersedure, Sting Cut and
  Absconding are 13.2a's, 13.4's and 13.4a's to build; their step-up and typed-phrase checks are
  here for them. No route changes a device's key yet; the step that adds one requires step-up.*
- [x] **10.5f Voice in at the Landing Board.** `entrance/voice/`: `POST /v1/chat/audio` for
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
- [x] **10.6 Guard Bee role.** `workers/roles/guard_bee.py`, run in the Queen's process on the
  Hive Stand like the House Bee (7.6), so no Cell action can take it down and it always reads the
  central trail. Deterministic rules first; its awake episodes run on `ModelSlot.JUDGE`, so the
  manifest can pin them to a different provider than `WORKER` and blind spots do not correlate.
  Watches the trail for denial rates, out-of-scratch touches, over-grant spend, unexpected
  network attempts, Capping rejection and rollback rates, failed sampled audits,
  injection-suspected events from the scanner (10.6b) and their correlation with a denial in the
  same episode, node integrity failures (a signature or replay failure, a capability report that
  changed without re-enrolment, a refused segment merge, 11.9), and Entrance events (failure
  bursts, lockouts, invite-route abuse, travel-lock triggers). It acts on its own only where the
  action narrows access hive-wide, by autopilot rule: raise a tier's audit rate, and trigger the
  Entrance Reducer (10.5e). Anything aimed at one Cell or one bee it can only **request**:
  isolate a Cell (10.6a), quarantine a bee, or Sting Cut a Cell (13.4a). Every request carries a
  `GuardReport`: the rule that fired, the trail event ids, the affected Cell, bees and grants,
  the recommended action and a confidence tier. Requests enter the Queen's inbox as
  `InboxKind.GUARD_REQUEST`, which `WeightTable.queen_default()` scores above `ALARM` and every
  human message, with the Guard principal's multiplier on top, so only age separates two of
  them; the Warden table never sees one. The Guard Bee never touches a Cell, never calls a model
  on the hot path, and can never widen anything. Every report is a `guard.alert` on the trail
  and is deposited as `C2` Nectar so it appears in the Honey browser; a report the Queen acts on,
  or one at `CRITICAL`, also reaches the human on the existing path, an Alarm that reached the
  human in the inbox, pushed to every enrolled device (10.5b), with the report linked. `AlarmKind`
  gains `SECURITY` and the wire's `InterventionAction` gains `QUARANTINE` (10.6c), a waggle minor
  bump since both are wire enums; the Hive-side `PolicyAction` gains `ISOLATE` and `QUARANTINE`,
  which no wire enum mirrors (ADR-0043).
  *Landed as `hivemind.workers.roles.guard_bee`, composed into every Hive `hive run` and `hive
  serve` build (`hivemind.cli.compose.guard`) and filing through the Queen's own door; C2
  deposits are a named seam (`GuardReportSink`, in memory until then) that phase 7's Nectar
  intake fills. Node integrity counts what the Hive records today (a forged Entrance request, a
  forged frame or an unmerged segment at the Cell gate, a connection closed for flooding and a
  replayed envelope id at the Hive Stand's listeners, the last two per node as
  `frame_ceiling_repeats` and `replay_refusal_burst`); key-verified merges (11.9) are
  `segment_forgery`'s `bad_signature`, and capability reports (13.4a) become one rule when their
  step records its kind.*
- [x] **10.6a Cell isolation, Queen-only.** `queen/isolation.py`: the Queen's action on one Cell,
  never a Guard Bee's or a Warden's: revoke the Warden's grant, checkpoint and pause every bee on
  the Cell, write a `BLOCK` Cell Wax so nothing is placed there, set a Virtual Cell's network
  policy to `none`, and keep the lease and its scratch intact for forensics. Recorded as
  `cell.isolated` with reason, the `GuardReport` id and the trail ids that justified it, and
  pushed to the human. The Queen decides by autopilot rule for the dire patterns in `[guard]`
  (13.4a) and by awake episode with the report attached otherwise; when her awake mode is
  unavailable the autopilot fallback on a `GUARD_REQUEST` is to isolate, since isolation only
  removes access. Any Handoff, checkpoint or Nectar from an isolated Cell is labelled **tainted**
  and `memory.assemble` refuses it until a judge clears it, so an injection cannot survive
  through memory. Lifting isolation needs the human with step-up; the Queen never lifts it on
  her own, and the Hive Stand's own lease is isolated only by the human: on a dire pattern there
  her rule falls back to quarantining the implicated bee (10.6c), holding new placements for that
  goal on the Hive Stand, and raising a `CRITICAL` Alarm to the human with the report. Tests: a
  Guard request never isolates without a Queen decision on the trail; a tainted Handoff is
  refused; the Hive Stand path is human-only and the fallback fires instead.
  *Landed as `hivemind.queen.isolation` (the one path, checked at `EnforcementPoint.ISOLATION`;
  the human's lift; `IsolationDoor`) and `hivemind.queen.guard_requests` (the durable request
  table, its `GUARD_REQUEST` items, the decision by rule, episode or fallback, and
  `GuardRequestDoor.report_to_human` for a CRITICAL report, shown once per report id), with the
  human's `POST /v1/cells/{cell_id}/isolate` and `/lift` (interactive, step-up,
  `entrance:steward`). The Hive Stand's fallback holds goals through `PlacementHold` rows that
  placement reads; a quarantine checkpoint a judge clears resumes its task on the Queen's tick.
  The taint reaches the Hive's own memory tables and, by Waggle 1.10's `cell.taint_order`
  (resent while the isolation stands), the store a Virtual Cell's Warden keeps inside the Cell,
  whose resume gate then refuses a tainted Handoff (`hivemind.wardens.isolation`). Docker cuts a
  running Cell's egress by dual-homing it on a per-Hive internal control network
  (`[virtual_cells] control_subnet`, the listener on its gateway) beside its own egress network,
  detached by the cut and reattached by the lift; proved on a real daemon by
  `tests/integration/test_docker_egress.py`. QEMU declares why not, and what a cut needs.*
- [x] **10.6b Untrusted-content scanner.** `guard/scanner.py`, deterministic, no model: runs at
  every point where outside text enters a prompt, tool results in the Worker runtime (3.16),
  session output, Honey hits at assembly (7.7), Nectar intake (7.4) and Landing Board messages
  (10.5). It emits `guard.injection_suspected` carrying the source, the consuming bee and a
  content hash, never the text, and `memory.assemble` applies the `[guard] untrusted_content`
  policy: label harder or drop. The trail carries no prompt text, so this is the only producer
  of the injection signal the Guard Bee watches. The invariant, tested with seeded payloads
  through Honey and a tool result: an injected instruction can at most make a bee ask; it never
  widens a grant, never reaches an outside-scratch write uncapped, and always leaves a `guard.*`
  event. The patterns are data, not code: `guard/defaults/untrusted-content.toml` (shipped,
  read through `importlib.resources`, explained in `docs/guard/`), one family per
  table (imperatives addressed to the model, role and identity overrides, secrets paths beside
  exfiltration verbs, encoded blobs over a size, tool-call-shaped text, hosts outside the task's
  targets), each with a weight, and the `[guard] untrusted_content` thresholds for label and
  drop per Comb Shield tier. A flag alone never stops a bee; only the correlation rule in 10.6
  escalates. The chaos seeds in 13.6 are drawn from the same file, so a pattern change is a
  visible diff and the invariant stays testable.
  *Landed for tool results, session output and Landing Board messages; Honey hits at assembly
  and Nectar intake are named seams (`ScanSource.HONEY_HIT`, `NECTAR_INTAKE`,
  `AssembleRequest.retrieved`) that phase 7 fills.*
- [x] **10.6c Quarantine, one intervention.** `Quarantine` joins the `Intervention` union (3.13)
  and `InterventionAction` on the wire (minor bump), carrying the episode id from which the
  bee's memory is suspect. One code path in `wardens/`, nothing composed by hand anywhere else:
  checkpoint, cancel, kill the tracked process, revoke the bee's slice of the grant, label every
  checkpoint, Handoff and Nectar from that episode on as tainted (10.6a's rule), move the task
  to `PAUSED`, record `warden.intervened` and `memory.tainted`. Ordered by the Queen on a Guard
  request, by rule for the dire patterns or by awake decision otherwise, or by a Warden's own
  policy row for its own sub-bee, since a Warden may already cancel it; the Queen is told
  either way. The only way out is a respawn from a Handoff the judge has cleared. A test
  asserts no other path marks memory tainted.
  *Landed with Waggle 1.9 as `hivemind.wardens.quarantine` (the path, and the gate that is the
  only way out), ordered through `Queen.intervene`, `hivemind.queen.quarantine.order_quarantine`
  or a `QUARANTINE` policy row at either level; the Queen holds the task on the Warden's PAUSED
  report and hears a SECURITY Alarm. The Guard request that pulls the lever lands with 10.6.*
- [x] **10.6d Taint, one label.** `tainted` is one marker on checkpoints, Handoffs, episode
  records, Nectar and Honey items, with the reason and the event that set it. Set by isolation
  (10.6a), by quarantine (10.6c), or by the Queen on a Guard report about a Honey item, and by
  nothing else. `memory.assemble` and retrieval (7.7) refuse a tainted item outright; the
  Handoff loader refuses a tainted Handoff. Only a judge verdict clears it, on the taint rubric
  with no shared context. A House Bee duty re-ripens tainted Nectar with the flagged span
  stripped into a new Honey item that starts tainted and goes to the judge; the old item is
  retired, never edited. `memory.tainted` and `memory.taint_cleared` on the trail. A test seeds
  a tainted Handoff, a tainted Honey hit and a tainted Nectar deposit and asserts none reaches a
  prompt until cleared.
  *Landed on checkpoints, Handoffs, episode records and Bee Bread entries, with the one setter
  (`memory.taint.taint_memory`) and the one clearer (a judge verdict through `TAINT_CLEAR`); the
  Honey and Nectar halves and the House Bee's re-ripening are declared seams (`TaintLedger`,
  `TaintedNectarRipener`) that phase 7's stores implement.*
- [x] **10.7 Access levels.** `guard/access.py` (3.13a) grows the full permission data for
  `AccessLevel` (2.3a) on every Real Cell (`read_only`, `scratch`, `full`), stored with the node
  and the lease and shown in the UI. Virtual Cells are
  always `full`. The Pollen Packet requests `full` at enrolment by default; the operator may grant
  less, and the level caps every capability set issued for that Cell. `watch:<node>` capabilities
  bound what watch mode (11.10) may observe: `read_only` allows process list, resource use, logs
  in allowed roots and file-change events in allowed roots; screen or input capture is never part
  of watch mode and needs an explicit, separately granted capability.
  *Landed for the one Real Cell phase 10 has: the level is stored with the Hive Stand's node
  (`[hive_stand] access_level`, and no lease above it) and with every lease (`cell.leased`
  carries it), shown by `GET /v1/cells`, the Cell stream and `hive cells list`, and it caps the
  Warden's set (`guard.policy.roles.warden_set`), every Worker's (a subset of its Warden's) and
  every check (`evaluate`). Swarm nodes store theirs with the registry (11.2) and take the
  operator's grant through `hive swarm access` (11.12); the Pollen Packet's `requested_access`
  is on the wire (`waggle.messages.swarm.enrolment`) for 11.1 and 11.3 to send and honour; the
  web screens are 12.4 and 12.6.*
- [x] **10.8 CLI.** `hive entrance invite|pending|approve|deny|devices|revoke|steward|
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
- `guard-bee-requests-queen-only-isolation-and-tainted-memory.md` (the Guard never acts on a
  Cell; every Cell-level lever is a request with a `GuardReport` the Queen decides on, scored
  above every human message; dire patterns as manifest data; tainted memory after isolation).

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

- [x] **11.1 Enrolment protocol.** `docs/waggle/enrolment.md`: one-time token from `hive swarm
  invite`; the packet presents it with its public key, its `CellCapabilities` and its
  `ForageCapacity`; the Queen replies with the Hive's key and the node's capabilities. Tokens
  expire and are single use.
  **Built 2026-10-14.** `docs/waggle/enrolment.md` (Waggle 1.13). The invite `hive swarm invite`
  prints is a `pollen1.` string of eight keys (`hive_id`, the token, the listener's URL, the Hive
  Stand's node id and key, its TLS certificate's SHA-256 pin, the expiry and a label), so the
  packet's first frame is addressed to the Hive and its first connection is pinned. The packet
  presents the token with its public key, its `CellCapabilities`, its `ForageCapacity` and a
  `PlatformReport`; the enrolment door (`swarm/enrolment/`) replies with the Hive's key and the
  node's capabilities, access level and Comb Shield tier. Tokens are single use and expire; a
  refused enrolment says why. `pollen/enrol/` is the packet's half.
- [x] **11.2 Swarm registry and source.** `SwarmNode`, `NodeStatus`, SQLite registry, enrolment,
  heartbeat, and `SwarmSource` (a `RealCellSource` whose `lease()` and `release()` round-trip with
  the device and wait for restore confirmation).
  **Built 2026-10-14.** `swarm/models.py` (`SwarmNode`, `NodeStatus`, `RuntimeLevel`, capacity and
  capabilities as reported), `swarm/registry/` (SQLite and in-memory behind one protocol), the
  listener's heartbeat watch (`swarm/gateway/`: a device that misses its beats is UNREACHABLE,
  `swarm.online` and `swarm.unreachable` on the trail), and `SwarmSource` (`swarm/source.py`), a
  `RealCellSource` whose `lease()` opens the lease on the device and whose `SwarmLeaseReleaser`
  waits for the device's restore report before the release counts.
- [x] **11.3 Pollen gateway.** `pollen/agent/packet.py`: connect out, heartbeat, verify signatures,
  check every request against the node's capabilities locally, hand `session.*` and `lease` traffic
  to the executors, keep an outbox. It persists the Queen's address from enrolment and replaces it
  only on a `QueenMoved` signed by the Hive key (13.2a). No supervision logic; no model client.
  **Built 2026-10-14.** `pollen/agent/`: the link (dial out over `wss://` with the pinned
  certificate, heartbeat, reconnect with backoff), signature verification on every frame, a
  dispatcher that checks every request against the node's capabilities locally before any executor
  sees it, the outbox (replayed in order on reconnection), and `control.py` (`control.ping`,
  `swarm.node_update`, `control.queen_moved`). The Queen's address comes from enrolment and is
  replaced only by a `control.queen_moved` signed by the Hive key (`pollen/agent/moved.py`). No
  supervision logic and no model client in the packet. Left to 13.2a: the relocation notice carries
  no TLS pin for the new address (closed by 13.2a: `new_tls_pin_sha256`, Waggle 1.16).
- [x] **11.3a Per-node frame ceiling.** The server half of `waggle/transport/websocket.py`
  gains `max_frames_per_minute` per connection, lax on purpose (default 6000, a hundred a
  second, well above any Warden's heartbeat and result traffic) with a burst allowance; over
  it the server closes the connection with a reason code and the node reconnects with its
  usual backoff. Below it nothing changes. `waggle` writes no trail, so the Hive Stand's
  listener wrapper records `guard.frame_ceiling` with the node id on each close. A narrowing
  action the transport takes alone; the Guard Bee only counts the events, and repeated
  ceilings from one node in a window are a dire pattern (13.4a), since isolation keeps the
  socket open and only a Sting Cut invalidates the node's keys.
  **Built 2026-10-13.** `waggle/transport/ceiling.py`: 6,000 frames a minute per connection with a
  burst of 600; over it the server closes with code 4029 and the node reconnects with its own
  backoff. The Hive Stand's swarm listener and Virtual Cell listener record `guard.frame_ceiling`
  with the node id on each close (`waggle` writes no trail); the Guard Bee counts the events per
  node (`frame_ceiling_repeats`: three closes of one node in ten minutes, a request to isolate the
  Cell the node speaks for, or an observation when none is bound), which 13.4a lists as a dire
  pattern.
- [x] **11.3b Replay window.** ADR-0005 leaves deduplication to receivers that care; every
  receiver now must. The server half keeps the envelope ids seen per node over a bounded window
  (`replay_window_minutes`, default 10, and a cap on ids kept) and drops a duplicate without
  handling it, so a lost ack followed by an outbox replay stays idempotent. A duplicate inside
  the reconnection grace after `warden.reconnected` is expected and silent; any other is
  `guard.replay_refused` with the node id, and a burst is the signature-or-replay dire pattern
  (13.4a). `docs/waggle/spec.md` states the obligation, and the gateway contract (11.7a) carries
  it, so a compiled gateway is held to it too.
  **Built 2026-10-13.** `waggle/transport/replay.py`: the envelope ids seen per node over
  `REPLAY_WINDOW_S` (600 s, a constant of the transport rather than the step's
  `replay_window_minutes` knob), capped at 50,000 ids and shared by every connection of one server,
  so a duplicate is dropped unhandled whichever connection carries it. Inside the reconnection grace
  (`RECONNECT_GRACE_S`, 120 s, counted from the link's attach, before its first frame is read) a
  duplicate is silent; any other is `guard.replay_refused` with the node id, which the Guard Bee
  counts per node (`replay_refusal_burst`: five in five minutes). `docs/waggle/spec.md` states the
  obligation and the gateway contract (11.7a) carries it. Proved across a container boundary: a
  replayed `session.exec` runs once and a frame with a broken signature closes the link with 1008
  (`tests/e2e/test_phase11_exit_replay.py`).
- [x] **11.4 Device-side lease, executors and dead-man switch.** `pollen/lease/` (scratch dir
  under the quota the node's `Ceilings` carry, enforced the way 3.11 does on the Hive Stand;
  started pids, restore; **dead-man**: when the link is lost longer than the enrolled limit and no
  Warden runs on the device, kill what the lease started and release), `pollen/executors/session.py`
  (persistent shell with streaming, argument lists only), `files.py`, `info.py`.
  **Built 2026-10-13.** `pollen/lease/`: scratch under the quota the node's `Ceilings` carry (a
  watchdog as in 3.11), every started process recorded with its creation time (a release never
  signals a reused pid), restore records, and the dead-man switch: past the enrolled limit with no
  Warden on the device it kills what the lease started, releases with cause `DEAD_MAN` and reports
  the release when the link returns. `pollen/executors/`: `session.py` and `run/` (each
  `session.exec` is its own process group with stdout and stderr streamed, argument lists only; not
  a persistent shell, which the `CellSession` contract does not ask for), `files.py`, `info.py`,
  `process.py` (background processes) and `observe/` (watch mode, 11.10). Proved in a container
  (`tests/e2e/test_phase11_exit_deadman.py`). **Quota wired end to end, 2026-10-14.** The packet
  honoured a quota on `cell.request` from the start, but nothing on the Hive Stand sent one, so the
  packet's own 512 MiB applied to every device. A device's Warden now puts the Queen's
  `Ceilings.scratch_disk_bytes_per_lease` on every lease request
  (`LeaseRequest.scratch_quota_bytes`, read afresh each lease; 0 means she set none),
  `SwarmSource` sends it as the existing
  `cell.request.scratch_quota_bytes` (no new wire field), and where she set none it sends the
  Hive's `[swarm] scratch_quota_mb` (default 512, the packet's own figure). A command that fills
  scratch past the figure is killed by the packet's watchdog and the Capping gate rolls it back.
  Proved in a container, a manifest default and then a Queen's `forage.ceilings_set`
  (`tests/e2e/test_phase11_exit_quota.py`).
- [x] **11.5 PollenSession.** `swarm/session.py`: the Hive Stand side `CellSession` over the
  `session.*` messages. Passes the `CellSession` contract suite against a packet in a container.
  **Built 2026-10-13.** `swarm/session/` (`PollenSession`, the `CellSession` over `session.*`). It
  passes the `CellSession` contract suite against a real packet run in process over a real
  WebSocket, and `tests/integration/test_pollen_in_container.py` runs the packet in a container.
- [x] **11.6 Level 0: a Warden per device, on the Hive Stand.** Enrolment starts a Warden on
  the Hive Stand bound to the device through `PollenSession`, with a shared grant and a local
  pool consisting of the device's host compute for sub-bee slots (their brains run beside the
  Warden on the Hive Stand; only their hands are on the device). Placement sees the device; the
  Undertaker releases its leases. Documented limit: remote exec latency; no autonomy when the
  link drops.
  **Built 2026-10-14.** The swarm gate (`queen/swarm_gate/`) gives every online Level 0 device a
  Warden on the Hive Stand (`cli/compose/swarm/device_warden.py`) bound to the device through
  `PollenSession`, with a shared grant and a local pool sized from the device's own cores and
  memory; every command a sub-bee sends passes a per-lease command guard. Placement sees the device
  as a `REAL` candidate (pin a goal with `cell:real:<cell_id>`); every Real Cell release path, the
  Undertaker's included, reaches the device through `SwarmLeaseReleaser`. A device whose lease the
  device refuses (an unverified Propolis tunnel) keeps its task parked and retried, not dropped. The
  documented limits stand: remote exec latency, and no autonomy when the link drops. Proved in a
  container (`tests/e2e/test_phase11_exit_level0.py`).
- [x] **11.6a Level 1: the Warden moves onto the device (colonization).** `swarm/level.py` +
  `pollen/bootstrap/`:
  when the Queen decides the device should carry its own Warden (its Forage suffices and the Hive
  Stand needs relief, or the task needs to survive short outages), the packet installs the
  `hivemind` runtime, the Hive Stand Warden checkpoints and hands off, a Warden starts on the
  device and resumes from the Handoff, its sub-bees are respawned beside it, and the Queen sets
  its `Ceilings`. Models still come from the hosting plan's shared sources. `warden.migrated`.
  The Warden is always the first bee to move; a device never runs sub-bees without their Warden.
  **Built 2026-10-14.** `swarm/level.py` (`decide_colonize`: the device's Python and free memory
  against the role footprint, and a reason: the operator's `desired_level`, the Hive Stand's relief
  pressure, or a task that must survive a lost link), `queen/swarm_gate/colonize/` (Guard-checked at
  `NUC_PROMOTION`: the packet PREPAREs a venv runtime from its wheelhouse and STARTs the Warden in
  `hive in-cell` borrowed mode under the same id, `pollen/bootstrap/` and `cli/in_cell/borrowed/`;
  each running task is checkpointed into a Handoff, a move waiting up to 30 s for a bee inside a
  command, and resumes on the device from it, carried inline on `TaskAssign.handoff`; any failure
  rolls back to a Warden on the Hive Stand; `swarm.promoted`, `warden.migrated`; the way down only
  while idle). The Queen moves a device on her own (`queen/swarm_gate/rungs/`: relief and seat
  pressure defined in `pressure.py`, `TaskNeeds.survives_link_loss`, a device lowered only after its
  reason has gone and while idle, a failed attempt backed off), and the operator's `desired_level`
  always wins (`demote` at GATEWAY pins a device). A terms change on a colonized device restarts its
  Warden through the packet with its tasks checkpointed (ADR-0053). Proved on a real container
  (`tests/e2e/test_phase11_exit_nuc.py`).
- [x] **11.7 Platform shims and packaging.** `pollen/platform/{linux,windows,macos}.py` (any
  Linux distro through generic systemd and XDG paths, with Arch and Ubuntu tested; Windows; macOS);
  `pollen install|uninstall`; single-file builds in CI for Linux x86-64 and ARM64 (Raspberry Pi),
  Windows x86-64, macOS ARM64 and x86-64. The packet reports OS, distro, arch, package manager,
  shell and Python availability at enrolment, and that report is what every later decision about
  the device reads: placement, tool eligibility, scaffolding targets, Nuc eligibility.
  **Built 2026-10-14.** `pollen/platform/{linux,windows,macos}.py` (a systemd user unit and XDG
  paths on any Linux, a scheduled task on Windows, a launchd agent on macOS), `pollen
  install|uninstall|status|version`; `scripts/build_pollen.py` and `.github/workflows/pollen.yml`
  build the single-file packet for Linux x86-64 and ARM64 (inside Debian 12, so its glibc floor is
  Raspberry Pi OS's), Windows x86-64 and macOS ARM64 and x86-64; the CI Arch job runs the packet's
  tests on Arch. The enrolment `PlatformReport` carries OS, distribution, architecture, package
  manager, shell and Python, and the decisions about the device read it (placement by `os`,
  scaffolding, colonization by `python_version`, tool eligibility by `os`; the Nuc decision does
  not yet).
  A frozen packet hands the programs it starts the device's own loader path, not PyInstaller's.
  Proved on a clean Debian 12 container standing in for Raspberry Pi OS
  (`tests/e2e/test_phase11_exit_binary.py`); ARM64 itself only on the CI matrix.
- [x] **11.7a Gateway contract for other runtimes.** `docs/waggle/gateway-contract.md`: the
  minimal message set a Pollen-equivalent must implement (enrol, heartbeat, capability report,
  lease open and release, the `session.*` family, outbox replay, signature verification), with
  the conformance suite from 1.9 runnable against any implementation over WebSocket. This is the
  path for devices the Python packet cannot reach, such as phones and constrained IoT boards: a
  small compiled gateway speaking the same contract, written after Brood 1.0. The protocol is the
  product; the Python packet is its first implementation.
  **Built 2026-10-13.** `docs/waggle/gateway-contract.md` and the conformance suite
  `tests/contracts/test_gateway_contract*.py`, which runs against any gateway named by
  `HIVEMIND_GATEWAY_COMMAND` (the Python packet by default) over a real WebSocket.
- [x] **11.8 Level 2: Nuc promotion.** `swarm/nuc.py`: on a Level 1 device the Queen decides
  from the device's free VRAM, the Hive Stand's seat pressure and the tasks' autonomy needs; she
  writes a hosting plan with local sources first, sets the model ceilings, and sends `NucPromote`.
  The device's Warden starts a model server through 8.3, loads allowlisted models within its
  ceilings, and reports the new sources; from then on its local pool includes seats. Demotion is
  the reverse. Guard-gated. A device cannot go straight from Level 0 to Level 2; the Warden moves
  first.
  **Built 2026-10-14.** `swarm/nuc.py` (`decide_nuc`: colonized, a model server declared in the
  Warden's capacity report, allowlisted models that fit the free VRAM (a CPU model needs none), and
  a reason: the operator's `desired_level`, the shared pool's seat pressure past `[swarm.nuc]
  seat_pressure`, or a task that must survive a lost link), the Queen's flows in
  `queen/swarm_gate/colonize/nuc/` (Guard-checked at `NUC_PROMOTION`: ceilings, `swarm.nuc_promote`,
  `swarm.nuc_promoted`, `swarm.promoted`, then a hosting plan with the device's local sources first
  and the shared ones behind them; demotion the reverse; never GATEWAY straight to NUC), and the
  device Warden's `ticks/nuc/` (starts and stops its server through 8.3's hosting and reports
  `forage.source_added`). A later load of an allowlisted model within the ceiling is the Warden's
  own and leaves only a new source; one outside the allowlist is `guard.denied` at SLOT_BINDING. A
  Nuc whose Warden comes back without its server is promoted again or lowered cleanly. Config: the
  Hive manifest lists the server id with `load_on_start=false`, and the device manifest carries the
  `[forage.map]` rows its servers load. Proved on a real container with a scripted model server
  inside it (`tests/e2e/test_phase11_exit_nuc.py`).
- [x] **11.8a Real Cell Comb Shield is operator-set.** `swarm/enrolment.py` records the tier the
  operator chose at enrolment, Meadow or Propolis, changed only by `hive swarm shield <node>`;
  placement never changes a Real Cell's tier. For Propolis the Warden verifies the device's own
  VPN before each lease (default route via the tunnel, DNS through it, direct egress denied) and
  refuses to place otherwise; HiveMind never installs, starts or reroutes a VPN on a Real Cell.
  Night Veil requests are denied on Real Cells and redirected to Virtual placement.
  **Built 2026-10-14.** The tier rides on the invite and is changed only by `hive swarm shield`;
  placement never raises a Cell's tier, so a PROPOLIS task never lands on a MEADOW Cell, Real or
  Virtual (on the Virtual side a spec must provision the tier the task needs). For Propolis the
  device's Warden verifies the device's own tunnel before each lease through fixed read-only
  commands and pure judges (`cell/tunnel/`, moved down from `swarm/shield/`; ADR-0057): default
  route via the tunnel, DNS through it, no direct IPv6 route, direct egress refused;
  `swarm.shield_verified` or `swarm.shield_refused` with per-check results, the scratch released and
  the task held, never failed, and nothing on the device changed. A NIGHT_VEIL goal is denied at
  placement on a Real Cell and goes Virtual. A PROPOLIS Virtual Cell is attested from its image with
  the same checks plus `tor_disabled` before it is ready, and again when resumed (ADR-0057). Proved
  in containers (`tests/e2e/test_phase11_exit_propolis.py`,
  `tests/integration/test_docker_propolis.py`).
- [x] **11.9 Offline Wardens.** `wardens/offline/`: on link loss a device Warden keeps working
  with what it owns. A Nuc keeps its sub-bees running on its local pool, and shared grants are
  frozen; a Level 1 Warden clusters its sub-bees at once, since it has no models to think with, and
  keeps them warm. Both queue results, Alarms and any `ForageRequest` in the outbox, write a local
  trail segment, retry the link with backoff, refuse anything that needs the Queen (new Cells,
  shared Forage, human-bound questions), and run full Clustering past the offline limit. On
  reconnection: outbox replay, `TrailSegmentSync`, grant and ceiling reconciliation, local pool
  report, `warden.reconnected`. A merged segment is verified against the node's key over its
  events before insertion, and one that fails is refused and recorded as a `guard.*` event,
  since ADR-0007 covers order and dedupe on merge, not authenticity. The Queen's side: mark
  unreachable, hold the node's tasks for a grace period, spawn the diagnostic Drone below, then
  decide.
  **Built 2026-10-14.** `wardens/offline/`: a Warden that dials the Queen keeps working on link loss
  with what it owns. A Nuc keeps its sub-bees on its local pool with shared grants frozen; a Level 1
  Warden clusters its sub-bees at once and keeps them warm; both queue results, Alarms, Nectar and
  `ForageRequest`s in the outbox, write a local trail segment (its final chunk signed with the node
  key), retry with backoff, refuse a new Cell, keep a request for shared Forage queued and a
  question for a human held until she is back, and cluster fully past `max_offline_s` (waking at
  that deadline, not at the next beat). The frames that must arrive and were sent in the stretch
  before a dead link is noticed (up to `UNNOTICED`) are kept in a bounded ring
  (`waggle.transport.recent`) and sent again under their own ids on reconnection, before the outbox;
  the receiver's replay window drops what it already had, and the packet does the same for
  `session.exit` and `cell.lease_released`. On reconnection: that ring, the outbox, the segment
  rewound by `UNNOTICED` and synced, grants, ceilings and plan re-sent by the Queen's one attach
  path (`queen/attach/`), the local pool reported, `warden.reconnected`; tasks the outage paused
  resume after `RESUME_SETTLE_S`, the Queen's word first. The Queen verifies a merged segment's
  signature over its events before insertion and refuses one that fails (`guard.*`). A Level 0
  device's Warden, which lives on the Hive Stand, holds a task whose bee died with the Cell's link
  in its outage with the Handoff it had, instead of counting a crash. The Queen's side
  (`queen/swarm_gate/unreachable/`): the lost device's Cell is held for `[swarm]
  unreachable_grace_s`, the Hive Stand's diagnosis (11.9a) is asked for when the hold holds tasks
  and the loss was seen, then she decides by rung (Level 0 and 1 tasks taken back and placed
  afresh from the newest Handoff she can hold: a Level 0 device's is in her store, a colonized
  device's is the HANDOFF Nectar its bee deposited, recalled by `swarm_gate/recall.py` unless it is
  tainted, written under a standing isolation, above the task's clearance or a Night Veil Cell's;
  a Nuc's tasks kept waiting up to `max_offline_s`). Proved on real containers
  (`test_phase11_exit_nuc.py`, `test_phase11_exit_unnoticed.py`, `test_phase11_exit_deadman.py`,
  `test_phase11_exit_diagnose.py`). Left open: after a Queen restart her replay window is empty, so
  an Alarm or a question sent again from an unnoticed stretch can repeat (results and Nectar stay
  idempotent), and after an outage longer than about nine minutes nothing from that stretch is sent
  again.
- [x] **11.9a Active reconnection: a diagnostic Drone on both ends.** Retry-with-backoff (11.9) is
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
  **Built 2026-10-14.** `wardens/offline/diagnose/`: a Warden that goes OFFLINE while it holds a
  local model binding (a Nuc) spawns one short-lived Drone from its own local pool, on a task it
  mints itself (`RequestOrigin.WARDEN`, never sent up as a task) that takes no grant slot, bounded
  by `max_sub_bees` and `[supervision.reconnect_budget]` (attempts and wall clock, carved out of
  `max_offline_s`; a device manifest sets both), and held to the Cell's access level and the Capping
  gate like any Drone. The Warden wakes at the budget's and the offline limit's deadlines, judges
  FIXED, DIAGNOSED or NO_CAUSE from the Drone's closing `CAUSE:` line, records `warden.diagnosed`,
  and queues the findings as Nectar until it reconnects; 11.9's timeline then runs untouched. A
  Warden with no local model spawns none. On the Hive Stand side, a lost device whose hold holds
  tasks, and whose loss was seen, gets an ordinary Queen task pinned to the Hive Stand
  (`queen/swarm_gate/unreachable/hive_stand.py`, "check reachability of device <cell> from the Hive
  Stand"), run by W0's Drone and deposited as Nectar before the hold decides. Neither touches the
  Royal Jelly Lab or the Quarantine Comb. Proved on real containers
  (`tests/e2e/test_phase11_exit_diagnose.py`: a planted resolver fault mended in budget with the
  task completing untouched; the network gone, the Drone giving up in budget and the Warden
  clustering at its limit; an unplugged colonized device's W0 finding deposited before the hold
  decides) and in process (`tests/e2e/test_hive_stand_diagnosis.py`).
- [x] **11.10 Watch mode and the hourly Patrol.** Real Cells stay enrolled between sessions, so
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
  **Built 2026-10-14.** `wardens/watch/`: a Warden with no bee working enters `WATCH` and observes
  through a probe, the `SessionWatchProbe` on the Hive Stand (two fixed read-only scripts through
  the Warden's own lease) or the `PollenWatchProbe` on a device (the packet's `observe` executors);
  observations go to Bee Bread with a retention window. On `[supervision.watch]`'s Patrol interval
  one awake episode over the observations since the last Patrol raises a `PATROL_ANOMALY` Alarm,
  deposits a Nectar summary, proposes Cell Wax, or records `warden.patrol` with "nothing notable"; a
  Patrol's delivery has its own timeout. The Cell page shows the mode and the last Patrol; the Hive
  Stand is watched too. Proved in a container with intervals of seconds: no writes outside the
  packet's directory, Patrols on schedule, a planted CPU hog an Alarm at the next Patrol
  (`tests/e2e/test_phase11_exit_watch.py`). **Roots on a device, 2026-10-14.** Logs and file
  changes were watched on the Hive Stand only: a device's Warden was built with no roots, so a log
  line or a changed file on a device never reached an observation. The operator now names them per
  device (`hive swarm watch`, ADR-0054's amendment), none until they do. The node record carries
  them (`SwarmNode.watch_roots`), `PollenWatchProbe` asks the packet for exactly those its granted
  capabilities let it read, and a change rebuilds the Level 0 Warden. The packet answers only
  inside its `fs:read` grant and never follows a symbolic link out of a root. Proved in a
  container: a new log line and a changed file under the named directory reach an observation, a
  path outside it (or through a link inside it) never does, and `--clear` stops both
  (`tests/e2e/test_phase11_exit_watch_roots.py`). **On a colonized device, 2026-10-14.** The Warden
  `cli/in_cell/borrowed` builds was given no watch settings, so a device stopped being watched
  when its Warden moved onto it. It now watches like every Real Cell's: a `SessionWatchProbe` over
  a lease-free read-only `ObserverSession` (it gives its lease back when idle), the Patrol's
  numbers from the device manifest's `[supervision.watch]` and the operator's directories carried
  as the other terms are (`NodeUpdate.watch_roots`, the packet's state, `HIVEMIND_WATCH_ROOTS` at
  START, `CellReady.watch_roots` back), so naming one restarts the Warden under it by the terms
  renewal, held to the access level at both ends (ADR-0054's second amendment). Proved in a
  container: the Warden in WATCH, Patrols on its beat and quiet, a planted hog an Alarm at the next
  Patrol, no file read until a directory is named and then its log lines and no other's, and no
  write outside the packet's directory (`tests/e2e/test_phase11_exit_watch_colonized.py`).
- [x] **11.11 Exoskeleton on devices.** Through `PollenSession` the X11 backends from phase 6 work
  on a Linux device unchanged; on a Nuc they run locally under the device's Warden.
  **Built 2026-10-14.** `PollenSession`'s `start`, `stop` and `is_running` carry the phase 6 X11
  backends to a Linux device unchanged (`tests/integration/test_exoskeleton_on_device.py` over
  device image variants; openbox no longer swallows the first click). On a colonized device or a Nuc
  they run under the device's own Warden through its local session: `device_exoskeleton`
  (`cli/compose/exoskeleton.py`) gives that Warden the wiring a Virtual Cell's has (flight recorder,
  browser launcher where the extra is installed, ears), with Chromium's sandbox kept and the
  recordings in the Warden's memory for a day after their last action. Proved on a container whose
  image carries an X server (`tests/e2e/test_phase11_exit_exoskeleton.py`): the Warden on the
  device starts the display through its own session, a Drone's click lands on it, the recorder
  opens one recording, the task succeeds, and the device is left as found. That scenario found
  that the first cut wired the Warden with nothing, so the display worked and none of its use was
  recorded (ADR-0052, amended).
- [x] **11.12 CLI.** `hive swarm invite|list|revoke|promote <node>|demote <node>|access <node>
  <level>|shield <node> meadow|propolis|run <node> "<cmd>"`; `hive cells list` shows devices as `REAL` with Warden location
  (`hive_stand` or `nuc`), access level and mode (`ACTIVE` or `WATCH`).
  **Built 2026-10-14.** `cli/swarm/`: `hive swarm
  invite|list|revoke|access|shield|promote|demote|run`, over the operator desk `swarm/operator/`
  (revoke, narrow and move a device); `run <node> "<goal>"` runs a goal pinned to the device (its
  Drone runs the command), not a raw command. `hive cells list` shows a device as `REAL` with source
  `swarm`, its access level, tier and mode (`ACTIVE` or `WATCH`), and where its Warden runs:
  `hive_stand` or `on_cell` (a column of its own gives the rung, GATEWAY, COLONIZED or NUC, so a Nuc
  reads `on_cell`/`NUC` rather than the `nuc` this step named). Proved through Typer's runner in
  `tests/e2e/test_swarm_cli.py` and on a container in `tests/e2e/test_phase11_exit_level0.py`.
  **`watch`, 2026-10-14.** `hive swarm watch <node> [<directory>...] [--clear]` names the
  directories whose logs and file changes the device's idle Warden reads (11.10), shows them when
  none is given, and records `swarm.watch_changed`.

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
- An amendment to ADR-0005: receivers must deduplicate by envelope id within a bounded window
  (11.3b), and the server may close a connection over its frame ceiling (11.3a).
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

- [x] **12.1 Read API and metrics.** `observation/api.py`: aggregated views for every screen
  below, paginated trail and episode queries, all behind `observe` with `observe:thoughts` and
  `observe:honey:<scope>` for the sensitive ones. `observation/metrics.py`: Cells by kind and
  status, Wardens by location, grants and headroom, Alarms by kind and level, awake episodes per
  hour per principal, tasks by status, tool promotions, denials, tokens and cost per task per slot
  per provider; Prometheus endpoint. Add first-class counters for total active bees, active versus
  inactive Cells, Real versus Virtual Cell totals, and current LLM versus autopilot activity.
  **Built 2026-10-12.** `observation/api/` and `observation/metrics/` are packages (the views
  directory was at its ten-module limit): one module per screen holding its read model and a pure
  builder; `entrance/reads/screens/` gathers from `HiveReads` and `entrance/routes/screens/`
  serves `GET /v1/summary`, `/v1/fleet`, `/v1/cells/{id}/page`, `/v1/attendant/{principal}`,
  `/v1/thoughts` (`observe:thoughts`, C2, cursor-paged over the newest 2,000 records),
  `/v1/capping`, `/v1/cost` and `/v1/metrics` (hand-written Prometheus text 0.0.4, no new
  dependency; `RouteSpec.response_class` declares its media type). Deferred, each documented on
  its field: `mask_state` (6.13), `last_patrol_at` and a Swarm Cell's `warden_runs` (phase 11),
  Capping recording links (no recording store is reachable), and the live Attendant inbox (the
  tick queues are memory-only, so it is reconstructed from open Alarms, blocked questions and
  unhandled chat and marked `inbox_is_reconstructed`). Autopilot decisions come from
  `queen.decided` and `alarm.handled` trail events, since autopilot leaves no episode record.
  Cost is per goal and lands under a null goal until the Fanner lanes carry `goal_id` (12.10
  wires that). The task-status metric reads up to 10,000 rows per status per scrape; a store
  `count` is a cheap follow-up if scrapes are frequent.
- [x] **12.2 Shell and live plumbing.** `packages/observation-web/` (scaffolded in 0.1):
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
  **Built 2026-10-12** (ADR-0049, `docs/observation/design.md`). `pnpm generate` writes
  `src/landing_board/types.ts` with `openapi-typescript` and `pnpm generate:check` runs in CI;
  `src/landing_board/client/` is the only module touching `fetch` or `WebSocket` (non-extractable
  P-256 binding key in IndexedDB, `hive-request-v1` and `hive-ws-v1` signing, the first-frame
  hello, the reconnect policy by close code, Web Push, and a write guard that refuses every
  mutating route but the Queen's inbox and the Entrance's own, asserted against the document);
  `src/store/` folds the streams into bounded slices read with `useSyncExternalStore`; hash
  routes with the loopback-only screens hidden off loopback; the shell, strip, tokens, badges and
  a placeholder per view; a shell-only service worker that never caches `/v1/`. The supervision
  tree, Cell and Forage diagrams land with their views (12.4, 12.5). Not yet: a push handler in
  the service worker (12.12), and the Entrance serving `.webmanifest` with its media type
  (checked in the phase's exit run). **Design round 2026-10-27:** the hive palette with the
  propolis chrome, the comb motif in its five places, the theme toggle in the top bar, the notice
  bar under the strip for `PushNotice`, a `ViewBoundary` around every view, relative time stamps,
  and tables that never split a word (design.md sections 2, 3.4, 4.2, 4.3, 6 and 7). **Round 2
  (2026-10-27):** one relative time per cell, phone captions on one line, two-decimal money, the
  shared "Sort by" control on every sortable table's cards, the Cell page's panels in columns,
  the chat opening on its newest line, the comb in the rail and the top bar with hex-cut top
  corners on panels, a dark chrome one step above the page, and the PWA icon redrawn as the
  Mark (design.md sections 2, 3.1, 3.4, 4.3, 5.6 and 5.8). **Round 3 (2026-10-27):** ids cut
  to their kind and ULID tail (`cell_…2C3D4`) so real ids tell apart; the rail, the Cell header
  and the Honey pane sized from the head's measured height (`--head-h`), so a waiting question
  is never under the rail; every panel, card and form a comb cell (`.comb-cell`) with the four
  corner 10 px cut and a stroked ring (`--hex-ring`) in place of a border cut off at the
  chamfers; the comb at 12 percent in the chrome and behind the login card, the strip's groups
  one comb row; 20 px numbered comb cells for the sub-bees' thoughts; the Devices list a table
  from 1280 px with its controls a wrapping row; the Forage's Wardens, requests and map tables
  back from 720 px with only the Grants table waiting for 1080, and the host cards named from a
  fleet read the view makes itself; `shell.css` split into `head.css` and the login view's own
  stylesheet (design.md sections 2, 3.4, 4.3, 5.3 and 5.10). **Round 4 (2026-10-27):** the
  focus ring on the chrome in `--chrome-accent` (the page's `--accent` was about 2:1 on the
  propolis), inset on the nav items so the rail never clips it, with both ring pairings in the
  contrast test; the Cell page leading with the Cell's name; the strip's four groups sharing the
  band up to the content's cap with the stale note at its right edge; one heading rhythm
  (`.section-heading`, the panel hairline) on every top-level section heading; the Fleet's
  table box and `EmptyState` comb cells, `EmptyState` wherever a view or section has nothing to
  list; the Devices list's platform on one line with its door controls the column that wraps
  (design.md sections 2, 3.4, 4.3, 5.2, 5.6, 6, 7 and 8). **Round 5 (2026-10-27), the phone
  round:** every diagram sized from its measured pane (`usePaneWidth`), so the Cell diagram's
  and the Attendant graph's narrow canvases are the pane's width and their 12 unit text is 12 px
  on any phone, and the Forage flow narrows (every label left of its box, cut to what the width
  leaves) instead of scrolling sideways; the Attendant graph's lines routed through the grid's
  gaps so one never runs through a node; the strip's phone form two lines per cell within its
  116 px budget; the seven per-view tags folded into one `.tag` and `.tag--status`; the Honey
  item in the hash (`#/honey/<path>?item=<id>`) with the opener and Close real links; a word
  beside every grant gauge on the Cell page; the Forage's legend drawn once and its pool card
  untitled under its heading; the Capping filters a select per group on a phone; the system
  font stack the stated design (design.md sections 2, 4.1, 4.7, 4.8, 5.1, 5.7, 5.9 and 6).
  **Round 6 (2026-10-27), the phone diagrams:** the Forage flow's pool amounts on a line under
  their words on a phone and "soon" under a near-expiry grant's name on either canvas (a box
  never shorter than its label's lines; Warden and grant labels by kind and ULID tail, or the
  tail alone); every line into one Attendant task with a bus, an entry point and, in the gaps, a
  lane of its own, the gap a lane wider per line a task can receive, with a test that two lines
  into one task share no segment; the wide diagrams drawn at exactly 1x and centred (the `<svg>`
  carries its canvas's width), so their type stays 12 px at 1280 and up; a 44 px touch area for
  the links in rows, cards, link lines and the Honey crumbs on a phone, with the flex rows that
  mix one with plain text aligned by baseline; the stale note inside the strip's 116 px phone
  budget (the cells' label line as tight as their number's, 2 px of padding given up while
  stale); the Cell page's gauge words level with their bars (`--gauge-h`); the Capping select
  collapsing a chip set to its first member (design.md sections 2, 4.2, 4.7, 4.8, 5.9 and 6).
- [x] **12.3 Thoughts view.** The Queen's thinking as it happens: the Attendant's current ordering
  of her inbox with scores, each autopilot decision and the rule that fired, and each awake
  episode's trigger, assembled context (by section, expandable), reasoning summary where the
  provider exposes one, decision and action. The same view opens for **any bee** from the fleet
  view, with full read access to its episode records and telemetry, and a "follow" mode that
  streams new episodes as they happen. Backed by `memory/episodes.py` (3.14) and the episode stream
  (10.5). Secrets and screenshot bytes are redacted at the source, not in the UI.
  **Built 2026-10-12.** `src/views/thoughts/`: seeded from `GET /v1/thoughts` (and
  `GET /v1/attendant/queen` for the Queen's ordered inbox with scores), follow mode over
  `/v1/episodes/stream` through the store's `useViewStream`, a "N new" queue when follow is off,
  cursor paging bounded at 500 records, autopilot decisions interleaved by time, and the taint
  label (state and source, added to `EpisodeView` for this step) beside the clearance. The store
  became generic here (`createHiveStore(slices)`, `useSlice`, `useViewStream`; a view registers
  its slice by declaration merging into `SliceRegistry` and one entry in `APP_SLICES`). Deferred:
  the assembled context by section, because an episode record holds only `prompt_ref`, which
  nothing sets yet (memory 3.14 chose never to store the prompt text; exposing sections needs a
  memory-side decision on what to persist); and a cursor on the episode stream, so a 4409
  reconnect re-subscribes without the gap until the view is re-seeded.
- [x] **12.4 Cell pages and diagrams.** One page per Cell with a live diagram: its Warden and
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
  **Built 2026-10-12.** `src/views/cell/`: seeded from `GET /v1/cells/{id}/page`, folded live
  from `/v1/cells/stream`, `/v1/telemetry/stream` and `/v1/trail/stream` (a trail event naming
  the Cell or its Warden re-reads the page once, a second after the first of a burst); the header
  carries the tier pill and the mask tag at every width (`OFF` with a "not tracked yet" note while
  `mask_state` is null); the diagram is SVG from a pure layout module, the Warden drawn inside
  the frame when it runs on the Cell, outside on the Hive Stand, dashed when untracked, each
  sub-bee with a role glyph, status colour, task and context gauge, plus a hidden table for
  readers; panels for tasks and goals, the Warden's Forage, the Honey entry point
  (`#/honey/cells/<id>`), mode and Patrol, Cell Wax, open Alarms and Capping activity. Not in
  the schema yet, shown as "not tracked yet" or omitted: the session's kind and state, the lease
  expiry, the Patrol's report (only its time), a recording link for Exoskeleton Capping items,
  the bee an Alarm belongs to (the bee whose task it names is marked), and the Cell's `status`
  on `CellFrame` (the header's status updates on re-read).
- [x] **12.5 Forage view.** A live diagram of total capacity and how it is divided: per host
  (Hive Stand, each Nuc, each Virtual Cell backend) the cores, memory, GPU and model seats in use
  versus available, the Royal Reserve held back, and a flow from the Queen's shared pool to each
  Warden's grant to each sub-bee's spend, plus hosted spend against cost caps. Each Nuc and Level 1
  device shown with its local pool beside the shared flow: what its Warden owns, its ceilings, its
  local reserve, seats it has exported, and spills from local to shared over time. The Forage map as
  a table: every source with grade, measured grade, distance from each Cell, abundance and cost.
  The Fanner's queues per binding with waiting requests and their tempo. Pending `ForageRequest`s
  with their reasons and the Queen's decision when it lands, and grants nearing expiry.
  **Built 2026-10-12** for what today's reads carry. `src/views/forage/`: seeded from
  `GET /v1/forage`, `/v1/wardens` and `/v1/llm` together, folded live from `/v1/forage/stream`
  (a `forage.capacity_reported` frame re-reads the ledger once, since the frame carries no
  figures); the capacity diagram (pure model and layout, SVG) per host for cores, memory, GPUs,
  local seats and sub-bee slots with the shared pool and the Royal Reserve first and
  over-commitment marked; the flow diagram pool to Wardens to grants in seats, sub-bees, spend
  or tokens; grants within ten minutes of expiry first and marked; requests from the stream's
  `forage.requested`, `granted` and `denied` events with the Queen's decision; Wardens with
  state and Heartbeat; a sortable map table; a spend gauge per Warden against its grants'
  budgets. Schema gaps, each needing a read-model field before the view can show it: the map's
  grade, measured grade, abundance, cost and per-Cell distance (`LlmView` has none); the
  Fanner's queues per binding; Nuc and Level 1 local pools and ceilings (phase 11);
  `CapacityView`'s host kind, per-host reserve, granted-but-idle, GPU and seat use; pool and
  reserve for spend and tokens; Hive-wide cost caps; pending requests in the seed; per-sub-bee
  spend in the Forage reads; and capacity figures on `ForageFrame`.
- [x] **12.6 Fleet list.** Every Cell in one table with a **Real / Virtual / All** filter: kind,
  source, status and mode, `CombShieldLevel`, Pheromone Mask state, access level, two badges (where the Warden runs: Hive Stand or on the
  Cell; where its models come from: local, Hive Stand, hosted, or hybrid per its hosting plan),
  current task and role, open Alarms, a Cell Wax indicator showing the highest live severity, lease
  age, and links into its page, its Warden's Attendant view and its bees' thoughts. `CombShieldLevel` appears as a persistent, high-contrast badge in
  every row, and the view includes a fixed legend for Meadow, Propolis, and Night Veil.
  **Built 2026-10-12.** `src/views/fleet/`: seeded from `GET /v1/fleet`, each `CellFrame` from
  `/v1/cells/stream` folded into its row at once and one throttled re-read (a second after the
  first frame of a burst, never without a frame) for the aggregate-only columns; the Real /
  Virtual / All chips remembered per viewer; stable sorting by meaning with `aria-sort`; the tier
  pill and mask tag first in every row and in the stacked cards below 1280 px; the legend fixed
  above the table. The task's bee link comes from the row's `thought_principals`, since
  `FleetRow` does not name the bee working the task, and a Queen-forced mask's expiry is not
  published, so the tag is drawn without it. The design's tablet expander is not built. The
  header is sticky inside the table's own scroll box and new rows wait behind a "N new rows,
  re-sort" button while the pointer or focus is in the table (2026-10-27); on a phone one "Sort
  by" select stands in for the header row. Round 2 (2026-10-27): the kind, source and lease age
  fold under the Cell's name, the mode under the status, the role under the task, the wax under
  the Alarm count and the thoughts (each sub-bee a numbered comb cell) under the Attendant link,
  so a row is two short lines (40 px) and every column fits the pane from 1280 px; between 720
  and 1279 px the rows stay cards, two across, and the page never scrolls sideways.
- [x] **12.7 Attendant views.** For the Queen: the task graph she is currently concerned with,
  what recently finished, what is coming next, and the ordered inbox behind it, with each task's
  placement and Alarms. The same view for **every Warden** over its sub-bees' tasks and its own
  inbox, reachable from the fleet list and the Cell diagram. Both read the per-principal task graph
  stream from 10.5.
  **Built 2026-10-12.** `src/views/attendant/`: seeded from `GET /v1/attendant/{principal}`,
  each `TaskGraphFrame` for the principal's tasks folded at once (status, placement) with one
  throttled re-read per burst for the ordering, the inbox and the Alarm counts; the graph is SVG
  from a pure layout (longest-path layers, cycles broken at the smallest id, left to right from
  720 px and top to bottom below) with a hidden table; beside it the ordered inbox with scores and
  reasons (and the "reconstructed" note), recently finished and up next. Read-model fields that
  would improve it: a Warden's `cell_id` on `AttendantView`, a subject on `InboxEntryView`, a
  `title` and `worker_id` on `TaskNode`, a `role` on `TaskView` (so a frame can add a node) and an
  Alarm count on `TaskGraphFrame`.
- [x] **12.8 Chat.** A chatbox for the human: request tasks, ask questions, answer the Queen's
  questions, resolve Alarms that reached the human, typed or spoken (a push-to-talk button using
  the browser's recorder, sent through 10.5f, with the transcript shown for confirmation when the
  message is a goal). Messages go into the Queen's inbox through the chat route (10.5) and the
  Queen's side of the conversation is her replies, questions and Alarms.
  This is the only write path in the UI. Each message links to the tasks and episodes it produced.
  Any break-glass Absconding path in UI requires password re-auth, a typed confirmation phrase,
  and a final scope review; no one-click trigger is permitted.
  **Built 2026-10-12.** `src/views/chat/`: seeded from `GET /v1/chat` and `GET /v1/inbox`,
  followed over `/v1/chat/stream` resuming from the last `seq`, the inbox re-read on a question
  or Alarm line; the conversation with the human's lines pending until they land, answer forms on
  waiting questions and acknowledge on Alarms (posting to the two inbox routes), the composer
  posting to `POST /v1/chat` with the step-up prompt and one retry on `step_up_required`;
  push-to-talk through a `MediaRecorder` behind the `Browser` seam, capped at 60 s with a
  countdown, posted as the clip's own bytes and media type by the client's new `postAudio`
  (signed over the bytes sent), the transcript shown and a held spoken goal confirmed or
  declined from its card. A test enumerates every request the view can issue and asserts the
  write set is the chat, the audio route, the two inbox routes and the goal confirm and decline.
  No break-glass control exists. Gaps: `ChatLine` and `QuestionView` carry only `task_id` (no
  episode or goal id, so links go to the Queen's Attendant and thoughts), `max_clip_seconds` is
  not published, the stream cannot start from the seed's
  `newest_seq` (a line in the gap before the first frame shows only through the inbox), and
  history beyond the newest 100 lines is not paged.
- [x] **12.8a Enrolment and step-up screens.** On loopback only: the invite screen (code and QR),
  the pending list, approve with name, capabilities, spend cap and expiry, deny, unlock, revoke,
  and the Entrance Reducer with its reopen flow. On every device: the join screen a new device
  lands on when it opens the Entrance with an invite, the passkey login, the step-up prompt, and
  the device list showing where each enrolled device last connected from. The remote build never
  renders the loopback-only screens, and the route test proves it could not use them anyway.
  **Built 2026-10-12.** `src/views/enrol/` (loopback only: mint an invite with its link and the
  Entrance's `qr_svg`, withdraw, the pending list live over `/v1/entrance/stream`, approve with
  name, capabilities, spend cap and expiry, deny) and `src/views/devices/` (every device: the
  list with the calling device marked, lock, unlock, revoke behind a typed name, capabilities,
  the Entrance's mode, reduce, reopen after step-up, held confirmations; door controls only on
  loopback and never on the device in hand); the join screen under `enrol/join/` redeems an
  invite with a new passkey and waits for approval, re-checking only on tap; the login screen
  shows it when the address carries an invite; one shared step-up prompt under
  `devices/step_up/`; the write guard gains `/v1/enrol/passkey*` only. The Entrance now serves
  the page at `/enrol` so a minted link opens the app. A loopback-gate test enumerates the
  document's loopback-only operations against what the UI can reach off loopback, and the Python
  route test proves the remote listener never mounts them. Gaps: `DeviceView` has no "last
  connected from" address (only `last_seen_at`, platform and user agent), and a pending device
  can only learn its approval by trying `POST /v1/auth/challenge`.
- [x] **12.9 Honey browser.** The Hive's knowledge as a folder tree from `honey_store/browse.py`
  (7.10): the main store under `/hive`, then what each Cell and each bee can see under `/cells`
  and `/bees` (each Cell's folder includes its live Cell Wax), task folders, and Bee Bread.
  Read-only navigation, reading and search per folder, provenance and `HoneyClearance` on every
  item, and a "propose a note" action that queues the note for the House Bee rather than writing
  (a Honey note, or Cell Wax when made from a Cell's folder; 7.10). Scope and clearance filtering
  match the viewer's capabilities.
  **Built 2026-10-12.** Python: `GET /v1/honey/tree`, `/v1/honey/items/{id}` and
  `/v1/honey/search` over the browse facade (`entrance/routes/hive/honey.py`,
  `entrance/reads/honey.py`, `observation/views/honey/`), each folder's scope checked as
  `observe:honey:<scope>` through the Guard, the viewer's clearance ceiling cutting the listing,
  a hidden item a 403 naming only the family, cursor paging; the 501 placeholder `GET /v1/honey`
  is gone. Front end: `src/views/honey/`, breadcrumbs, folders and items with the clearance tag
  always visible, wax severity, proposer and expiry, the reading pane, search where
  `can_search`, a Cell folder re-read once per burst on a `honey.*` or `memory.wax_*` event
  naming the Cell and other folders on `honey.*`; a two-viewer test proves the trees differ as
  the scopes say and every request is a GET. **Propose a note is not built:** nothing on the
  Landing Board carries a proposal to the House Bee (open decision 16), so the browser says so
  in each folder and draws no control.
- [x] **12.10 Cost view.** Per-goal spend by slot and provider, and what moving a slot local would
  save, from normalised `Usage` on the trail.
  **Built 2026-10-12.** `src/views/cost/`: seeded from `GET /v1/cost`, each `llm.call` frame
  with usage on `/v1/trail/stream` folded into its goal, slot and provider at once (a call with
  no `goal_id` in its payload lands in the unattributed row, pinned last and explained) with one
  throttled re-read per burst that replaces the figures; a totals strip, then sortable goal, slot
  and provider tables as stacked cards at phone width; the slot gauge shows hosted calls out of
  all calls, since `hosted_usd` equals `cost_usd` in today's model. Spend reaches a goal only once
  the Fanner lanes carry `goal_id` (12.1's note); a per-slot hosted and local call count would
  make the hosted share exact.
- [x] **12.11 Capping view.** Hive-wide queue of proposals by tier and state, judge verdicts with
  reasons, rollbacks, sampled-audit findings and rates per tier, and a link from every item to the
  bee's thoughts and, for Exoskeleton actions, the recording.
  **Built 2026-10-12.** `src/views/capping/`: seeded from `GET /v1/capping`, each `capping.*`
  frame on `/v1/trail/stream` folded at once (state, failing check, verdict, rollback, audit,
  with the per-tier counts and rates moving by the server's formulas) and one throttled re-read
  per burst; a rates strip per tier, the queue with tier and state chips, sorting with
  `aria-sort`, bee and Cell links, the verdict rendered from its check codes, and the Exoskeleton
  rows marked "recording not available" because `ProposalRow` carries no recording reference
  yet; audits in their own table; stacked cards below 1080 px. Reviewer prose is deliberately not
  on the trail, so a `reason` on `VerdictRow` and `AuditRow` is a read-model decision to make
  before the view can show one.
- [x] **12.12 Android client.** `packages/observation-web/android/`: a Capacitor project wrapping
  the same React app as an APK. Native push through the channel chosen in the ADR (FCM or
  UnifiedPush) registered with 10.5b, passkeys through the Android credential manager, the
  share sheet as an intake so any app on the phone (a glasses companion app included) can share
  text, an image or an audio clip into the Hive as a `HumanMessage` or Nectar labelled `C2`, a
  push-to-talk button that streams to 10.5f, and a foreground notification while a question is
  waiting. The phone is an enrolled device like any other; the
  APK is built in CI and is a release artifact from phase 14.
  **Built 2026-10-13** (ADR-0050). `packages/observation-web/android/` is a Capacitor project over
  the same build with a pinned WebView origin; Kotlin plugins for passkeys through the Credential
  Manager, UnifiedPush delivery over the Entrance's existing Web Push channel (no FCM) with a
  local notice while a question waits, and the share sheet as an intake; the TypeScript platform
  seam for Android is chosen at startup, a share card confirms before sending text through the
  chat, audio through `/v1/chat/audio` and images through the new `POST /v1/nectar/images`
  (`entrance:submit`; C2 Nectar with a per-device byte budget and `[entrance] max_image_bytes`,
  `image_bytes_per_minute`, `image_keep_hours`); `[entrance.android]` (`package_name`,
  `cert_fingerprints`, `webview_origin`) serves `/.well-known/assetlinks.json`, adds each
  fingerprint's `android:apk-key-hash` origin to the remote relying party and the WebView origin
  to the CORS and socket checks; `android.yml` builds and signs the APK on tags only.
  **Unverified here:** the native side has not been compiled (no JDK, SDK or Gradle in the build
  container), the library calls (`androidx.credentials`, the UnifiedPush connector 3.x) follow
  their documentation, and the real-device run (passkeys against a VPN-only Entrance, a push in
  the background, a share of each kind, App Link verification) is the operator's to do with the
  first tagged build.

### Exit criteria

**Exit run, 2026-10-13.** `packages/hivemind/tests/e2e/test_phase12_observation_hive.py` (marker
`e2e`; needs the web build, Node and Playwright's Chromium) serves `hive serve`'s composition with
a scripted provider and drives the built app in a real browser: a device joins by invite link with
a passkey, is approved at the Hive Stand, logs in, and in one page load with no reload the fleet
shows the Hive Stand's Cell with its tier and mask badges, the strip's counters move and equal
`/v1/summary`, a goal requested from the chatbox runs, its question comes back in the chat, the
answer resumes it and the reply arrives, the Queen's thoughts and the Attendant graph move live,
the Honey browser shows `/hive` and refuses a Cell folder the device lacks, every write the page
made is the chat's, the goal's or the Entrance's own, there are no page errors, and the strip, the
fleet and the chat render at 360 px. Not provable in the build container, left to the operator:
Cells appearing and grants flowing under the phase 6 or 9 scenario (only the Hive Stand's Cell
exists here), the phone over the VPN, the PWA install, the APK's enrolment, passkey, background
push and shared text, and the remote build's absence of the loopback-only screens (the Python
route test proves the routes are not mounted; the UI gate is tested in vitest).

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

- [x] **13.1 Requeening.** `queen/requeening/recover.py`: rebuild from the Brood Chamber and trail,
  reconcile with every backend and `RealCellSource`, reattach to live Wardens, replay their
  outboxes, reconcile grants against the ledger, release leases whose Warden is gone, reassign
  orphaned tasks, resume `PAUSED` tasks from Handoffs, `queen.requeened`. e2e kills the Queen with
  tasks on the Hive Stand, in a container and on a Nuc; all finish.
  **Built 2026-10-27** (ADR-0062). Every start is a Requeening: `run_hive` re-arms the readiness
  gate from each live Virtual Cell's `hivemind.cell_key` label (`queen/cell_gate/rearm.py`; every
  backend now stamps the Cell's public key) and runs `begin_requeening` before the Hive Stand's
  Warden leases; her tick runs `settle_requeening` until no Warden she waits for still holds a task
  unattached, or `[queen] requeening_grace_s` (90 s) is over. Orphans (the dead Hive Stand Warden's,
  a gone Cell's) are placed afresh with their newest Handoff as the resume hint, a waiting question
  withdrawn, a Night Veil one cancelled; a Swarm device's are left to the swarm gate; Clustering
  pauses (in memory only) resume from their Handoff through a Warden that came back; ended grants
  are released and gone holders' revoked `HOLDER_OFFLINE`; the dead process's Hive Stand lease is
  given back (`cell/local/orphans.py`, its id parsed before anything is removed). Every Virtual
  Cell's Warden now redials (`cli/in_cell/redial`: direct, or through Tor for Night Veil) and the
  Cell listener binds the OS-chosen port it had (`queen/cell_gate/port.py`). The Queen mode starts
  `REQUEENING` (`REQUEENING -> CLUSTERED` added for an outage meanwhile). Phase 11's open item is
  closed: what she handled survives a restart (`queen/requeening/handled.py`, noted when handled,
  never on receipt), so an Alarm or question resent after it is dropped and a question's answer is
  still forwarded, however long after the restart the human answers (its wiring is kept until the
  question closes). One Queen per Brood Chamber: `run_hive` holds the Hive's Queen lock
  (`<db>.queen.lock`), so a `hive run` beside a running `hive serve` is refused at start instead of
  requeening the serve's live work. `queen.requeened` carries counts only (the Night Veil boundary)
  and is classified a lifecycle notice. Proved in-process by `tests/e2e/test_requeening.py`: the
  Queen killed (her sockets closed, her tasks cancelled, no teardown) with a task BLOCKED on the
  Hive Stand and one held mid-call on a Virtual Cell; under a second Queen over the same file both
  finish, the lease is given back and `queen.requeened` reads one given back, one Warden back. The
  container and Nuc legs need Docker and a Nuc and are the operator's run
  (`docs/runbooks/requeening.md`).
- [x] **13.2 Queen state snapshots.** The Queen's own periodic Handoff, so recovery is bounded;
  `docs/runbooks/requeening.md`.
  **Built 2026-10-27.** `queen/requeening/snapshot.py`: every `[queen] snapshot_interval_s` (60 s)
  her tick writes a `QueenSnapshot` (the trail cursor, trailing it by 60 s, the Hive Stand leases
  open there, the Wardens attached) into `queen_snapshot` beside the handled-envelope table and the
  listener's port (`queen/requeening/store`, its own migration), and prunes plain handled rows past
  the replay window and the wiring of closed questions; recovery folds the trail only from the
  newest cursor. The first snapshot of a Queen's life follows `queen.requeened`.
  `docs/runbooks/requeening.md` says what an operator sees and checks.
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
  **Built in-process 2026-10-27** (ADR-0065, Waggle 1.16); not ticked, since the transport to a
  real candidate machine is left open (below). `queen/supersedure/`: `preflight.py` (the candidate
  test, pure, every check reported `PASSED`, `FAILED` or `UNVERIFIED`; reachability cannot be
  proved from the Hive Stand, so it is `UNVERIFIED` unless a probe says), `copy.py` (`hive backup`
  through `common.backup`, the archive put on the candidate, `hive restore --sha256` there, then
  the candidate's Queen started), `handover.py` and `rollback.py` (the two endings), `run.py`
  (`begin`: freeze, then `queen.supersedure_started` before a byte moves; `finish`: copy, poll the
  candidate, hand over or roll back), `record.py` (the four trail kinds), `gate.py` (the start
  rules and the candidate's hold), `ports.py` and `door.py` (the Queen's `supersede` and
  `supersedure_preflight`). The handover order differs from the text above, on purpose: the old
  Queen stands down (`SUPERSEDED`, `queen.superseded`) and acknowledges the candidate before the
  `QueenMoved` goes out, so no device is pointed at an unacknowledged Queen and the candidate's
  session stays usable to the end. The freeze clusters every bound provider plus a freeze key;
  `SUPERSEDING` and `SUPERSEDED` place, plan and resume nothing, and a `SUPERSEDED` Queen's loop
  ends. Each start reads the node's own trail under the Queen lock
  (`cli/compose/hive/supersedure.py`): an old Queen whose Supersedure has no outcome, a superseded
  one and a rolled-back candidate never start; an unfinished candidate is held in `REQUEENING`
  (Requeening's new `hold`) until acknowledged or the window is over with the old Queen
  unreachable (a TCP probe of `old_address`), and the Wardens' redial grace starts when the hold
  lifts. After a crash the human settles it (`hive supersede --settle superseded|aborted`, the
  password and the Queen lock). `QueenMoved.new_tls_pin_sha256` closes 11.3's gap, and the packet
  dials each address with its own pin. The `supersedure` point is wired and human-only.
  `hive supersede NODE` and `hive doctor --supersedure NODE` act as the console over loopback-only
  routes (`/v1/hive/supersede`, step-up and the typed phrase). Proved in-process by
  `tests/e2e/test_supersedure.py`: two Queens on temp stores, one handover with a sampler that
  fails if both are ever active (the goal's task placed afresh and finished on the new Queen, one
  `queen.superseded` on each trail), and one candidate killed before it is ready (rolled back
  `NOT_READY`, a rollback notice, the goal finished on the old Queen). Left open: the
  session-backed `CandidateLink` and probe for a real machine (stream the archive over the
  candidate's `PollenSession`, run `hive restore` and `hive serve` there, read its secret names,
  key and clock), so a real Hive has no `SupersedureWiring` and both commands refuse; Wardens and
  Virtual Cells honouring `QueenMoved` (only the Pollen Packet does); pushing the new address to
  enrolled phones; a `SUPERSEDED` Queen's process closing its Swarm gate and listener on its own
  (today her loop ends and the operator stops `hive serve`; until then `old_address` answers, so a
  candidate whose acknowledgement was lost stays held); enrolling the old machine as a Swarm node in
  `WATCH` is the operator's (`hive swarm invite`). The exit run onto a Nuc is the operator's once those land.
- [x] **13.3 Swarming policy.** `queen/scheduler/swarming.py`: pure policy for provisioning more
  Virtual Cells from queue depth and Forage headroom, per-backend limits, cost caps.
  **Built 2026-10-27** (ADR-0063). `plan_swarm` (no `hivemind` import; the autopilot import
  contract now forbids `hivemind.llm` to `queen.scheduler` too) gives each backend the smallest set
  bound on fresh Cells: its own `max_cells`, a Hive-wide `max_parallel` over Cells held (dormant
  included) and being made, the Hive Stand's live free memory and idle cores against one
  reservation, less the Cells still being made there, for a backend that `draws_on_hive_stand`,
  and `max_cost_per_hour_usd` over each backend's stated price. Queue depth is no bound of its
  own: the dispatcher asks once per ready task with no Cell on the way, so it scales the fleet
  only through how many placements a pass makes. The dispatcher's snapshot asks it before
  every placement, the retry's included, and marks a backend it allows nothing held back with the
  bound's name, so the task waits PENDING with one `queen.decided`. Scale-down is the existing
  release; a Virtual Cell shared by several tasks is now released only after its last one (it used
  to go under the others). `[virtual_cells.swarming]` (and `.backends.<name>`), every field unset
  by default; `manifest/schema/placement` became a package to hold it. Proved in-process by
  `tests/e2e/test_swarming.py`, the exit criterion's first half: twenty independent tasks under
  `max_parallel = 8` reach eight Cells, never nine, all succeed, and the fleet is back to zero
  before the Hive stops.
- [x] **13.4 Absconding.** `hive abscond`: Virtual Cells, dormant Cells, every lease on the Hive
  Stand and on devices, every grant, Worker processes, temp dirs, resident Basket blobs on every
  Cell (9.2a); from labels and the trail alone. Leavings (5.0a) are listed in the scope review
  and removed only when the human ticks them: they were left on purpose, often at the human's
  own word.
  Absconding is human-only: callable only by a human principal, never by Queen, Warden, Worker,
  autopilot rule, or tool. Execution requires password re-auth, an explicit typed confirmation
  phrase, and a final scope review prompt. Records actor, reason, and confirmation evidence in the
  trail. Persistent stores are not wiped by default.
  **Built 2026-10-27.** `hive abscond --reason TEXT` (`cli/abscond/`; `hive cells abscond --yes`
  is gone). The operator password must re-open the Hive Stand console's key before any store is
  opened. The `absconding` enforcement point then passes as the operator: `abscond` is a flag only
  `[roles.operator]` holds, and a new human-only floor (`guard.state_floor.human_only`) refuses
  every other principal at that point whatever a role override grants (policy tests cover each
  principal kind). `scope.gather_scope` reads, from backend labels, the trail and the small
  tables only: Virtual Cells (dormant counted), open leases split by the node that recorded them
  (Hive Stand or device), live grants, Workers spawned and never ended, temp dirs (scratch named
  for a lease that is not open; none under `keep_scratch` or while a Queen runs) and resident
  Basket rows on every Cell the labels or the trail name. Leavings are numbered in the review and removed only when ticked
  (`remove_leaving`, shared with `hive cells leavings remove`). After a final yes to the review
  and the typed `abscond my hive`, 5.13's `run_abscond` runs (a device's lease is now always a
  RELEASE order for its own Warden, never left untouched), then the temp dirs, residency rows and
  ticked Leavings go. `guard.absconded` records the human as actor, the reason, the confirmation
  evidence (`reauth`, `phrase`, `scope_review`, `leavings_ticked`, never a secret), the scope's
  counts and each result. `docs/runbooks/absconding.md`.
- [x] **13.4a Sting Cut (per-Cell emergency disconnect).** `hive cells sting-cut <lease|cell>`:
  immediate lease revocation for one Cell, session key/token invalidation, termination of
  Hive-started processes for that lease, idempotent scratch cleanup, and deletion of lease-local
  volatile traces (ephemeral caches, temp artifacts, transient credentials) per policy. Records
  `cell.sting_cut` with reason and actor in the trail and leaves central audit state intact for
  Meadow and Propolis. For Night Veil, retained logs and persisted records are destroyed at
  teardown. A Guard Bee may request a Sting Cut with a `GuardReport` (10.6), and the Queen may
  perform one under `[guard] sting_cut`: by autopilot rule only for the **dire patterns** listed
  there as data (a signature or replay failure from the Cell's session or envelope; repeated
  egress attempts outside a bee's network scopes; an outside-scratch touch after a Capping
  rejection; an injection-suspected event followed by a denial in the same episode; a node whose
  capability report changed without re-enrolment; repeated frame-ceiling closes from one node,
  11.3a), by awake decision with the report attached
  otherwise, and never for the Hive Stand's lease, which stays human-only. Every Queen-performed
  cut follows an isolation (10.6a) rather than replacing one, is capped per hour by
  `max_autonomous_sting_cuts_per_hour`, preserves evidence before scrubbing (a snapshot on a
  Virtual Cell, scratch copied as tainted `C2` Nectar on a Real one), and is pushed to the human.
  `hive cells sting-cut` with step-up remains the human's path for any Cell.
  **Built 2026-10-27.** `queen/sting_cut/`: one path, `sting_cut_cell`, for the human and the
  Queen. It checks `EnforcementPoint.STING_CUT` (`sting_cut` held; the Queen is refused the Hive
  Stand's lease and past `[guard.sting_cut] max_autonomous_sting_cuts_per_hour`, each a
  `guard.denied`), follows an isolation (the Queen's needs one standing; the human's isolates first
  through 10.6a's path), keeps a snapshot of a Virtual Cell as evidence (a month's retention), then
  revokes the Cell's grants, has its Warden release the lease through the Overwintering scrub's
  confirmed `Intervene(RELEASE_LEASE)` (processes killed, scratch removed, session closed; a Real
  Cell's Warden gets the order unconfirmed), forgets a Virtual Cell's key at the readiness gate and
  closes its link, and records `cell.sting_cut` (reason, `cut_by`, report, evidence, each step's
  result). A Night Veil Cell keeps nothing and is torn down after the record, which purges its
  segment. A CRITICAL SECURITY Alarm goes to the human. A restarted Queen never re-arms a cut Cell
  (`rearm_gate` skips it). A Guard request whose rule `[guard.sting_cut] dire_patterns` lists
  (shipped: `envelope_forgery`, `segment_forgery`, `replay_refusal_burst`,
  `frame_ceiling_repeats`), or an awake `STING_CUT`, is `QueenAction.STING_CUT`: isolate, then
  cut. The human's lever is `POST /v1/cells/{target}/sting-cut` (step-up, the typed phrase,
  `entrance:steward`; a lease id names its Cell) and `hive cells sting-cut <lease|cell>`. Left
  open: the Queen never cuts a Real Cell or a Cell whose backend cannot snapshot on her own, since
  copying a Real Cell's scratch in as tainted C2 Nectar is not built; such a Cell stays isolated
  for the human. `docs/guard/sting-cut.md`.
- [ ] **13.5 Overwintering at scale.** QEMU snapshots and cloud stop; disk accounting; eviction.
  **Disk accounting and eviction built 2026-10-27** (ADR-0066); not ticked, since QEMU snapshots
  and a cloud backend's stop need those backends on real infrastructure. A backend that declares
  `can_measure_disk` implements `DiskMeter` (the fake does; Docker and QEMU do not yet), and
  `CellLifecycle.release` weighs a Cell at what it really holds, which the pool then accounts it at.
  A full pool evicts its oldest dormant Cells to keep a freshly released one
  (`hive/overwinter/room.py`, pure: only as many as the bounds need, only the same image while the
  per-image bound blocks, none if nothing would help), each with `cell.evicted` (`pool full`), and
  only once the policy has decided to keep the newcomer. Expired dormant Cells are evicted while the
  Hive runs: on every release and from the dormant list the Queen reads on each placement pass and
  liveness tick, so placement is never offered one. The placement views moved to `hive/inventory.py`.
- [x] **13.6 Chaos tests.** Kill Cells, drop links, drop a Nuc mid-task, corrupt replies, return
  refusals, malformed JSON and rate limits from the fake, take a provider down mid-run and assert
  Clustering then resume, flood the inbox, exhaust a grant, kill the old Queen mid-Supersedure,
  kill the candidate before it is ready, hammer the Entrance's invite route from an unknown
  client and assert the Reducer fires, seed injection payloads through Honey and a tool result
  and assert no grant widens, the Queen isolates on the Guard's report and the tainted Handoff is
  never assembled, forge a Swarm node's trail segment and assert the merge is refused. Every goal
  finishes or fails explicitly; nothing hangs; no lease or grant is left open.
  **Built 2026-10-28.** `tests/e2e/chaos/` (marker `chaos`, with `e2e`; a nightly `chaos` job in
  `integration.yml`, left out of the push CI's e2e job). `rig.py` bounds every scenario (90 s,
  nothing hangs) and judges, once `run_hive` has returned, that the trail shows no lease or grant
  left open, the Forage ledger holds no live grant and no Virtual Cell is left on a backend. The
  scenarios: refusals, rate limits, malformed tool JSON, a malformed plan and an exhausted plan
  ladder from the fake provider; the provider down mid-reply, clustered and resumed by the health
  poller with no order; a Virtual Cell's container killed mid-task; every Cell link dropped and
  redialled; thousands of Alarms from a rogue link mid-goal; a goal billed past its spend cap
  (clustered, paused at its Handoff); a candidate dead before it is ready, one stalled mid-copy,
  and an old Queen whose process ends mid-copy (refused a start until settled, then finishes);
  an unknown client hammering the invite route until the Guard Bee's `invite_abuse` reduces the
  Entrance; seed payloads through a Honey hit (flagged, withheld, steered write capped, host
  refused, no grant widened) and through a tool result in a Virtual Cell (isolated by rule,
  nothing granted after the flag, the tainted Handoff not resumed after a lift); a Swarm node's
  segments signed with the wrong key and claiming another node (both refused, nothing merged, the
  link kept). `docs/runbooks/chaos.md`. Three defects it found are fixed: a Virtual Cell killed
  mid-task left its task RUNNING until the Hive stopped (`queen/requeening/lost.py` takes a lost
  Warden's tasks back after `max_offline_s` plus the redial grace and tears its Cell down); a
  stopping Hive left a paused task's grant live (the stop now revokes its Wardens' grants); and a
  Supersedure copy had no bound, so a candidate that stopped answering mid-copy held the old Queen
  SUPERSEDING for ever (the copy now runs under the grace window, rolled back `NOT_READY`). The
  Nuc leg rides `test_phase11_exit_nuc.py` (marked `chaos` and `integration`): it needs Docker and
  has not yet run on this branch. "Old Queen killed" ends her process by cancelling `run_hive`;
  a hard kill of the process is the operator's run with the real Supersedure transport (13.2a).
- [x] **13.7 Backups.** `hive backup` / `hive restore`; `docs/runbooks/backup.md`.
  **Built 2026-10-27** (ADR-0064). `hivemind.common.backup` (Layer 0, so Supersedure's `copy.py`
  can call it from the Queen): one gzip'd tar whose first member, `backup.json`, lists every other
  with its size and SHA-256, the Hive id, the public half of `hive.ed25519` and the secret store's
  names. `[hive] db` (every store, the Entrance tables and enrolled devices) is snapshotted through
  SQLite's online backup API, never copied by hand, and the Basket's blob directory rides along.
  The reader trusts nothing: index first, every member a regular file it lists once at its size
  and digest, written to a path built from the validated name, then `PRAGMA integrity_check`.
  `hive backup OUT` never overwrites and prints the archive's SHA-256. `hive restore ARCHIVE
  [--sha256] [--replace]` holds the serve and Queen locks, refuses another Hive's backup, a
  secret store missing any secret the backed-up Hive held or holding a different `hive.ed25519`,
  then writes the database back through the same API, copies blobs in (never deleting) and records
  `queen.restored` as the human. No secret is ever in a backup: the keypair moves only through the
  secret store. Tests run on temp-dir databases only (`tests/unit/common/backup/`,
  `tests/unit/cli/backup/`).

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
  **Text written 2026-10-28**; not ticked, since the clean-machine runs (Windows and Ubuntu, once
  per provider path) are the operator's. The README's Getting Started now goes: install `uv`,
  `uv sync`, `hive init --provider anthropic|ollama|openai-compatible`, `hive doctor`, the haiku
  goal through `hive run` on the Hive Stand, then Docker Desktop (`--extra docker`, the
  `base-ubuntu` image, `[virtual_cells]` with `listen_host = "0.0.0.0"` and `[placement] prefer =
  "virtual"`), then `hive entrance expose`, `hive serve`, an invite and an approval for a phone,
  with `docs/runbooks/remote-access.md` holding the long form.
- [x] **14.2 `hive init` and `hive doctor`.** Manifest generator with `--provider`; checks for
  scratch root, Docker if enabled, every provider reachable, every slot's model present, Forage
  sanity, sqlite-vec loads, escalation policy file valid.
  **Built 2026-10-28.** `manifest/starter/` renders the starter from a shipped template per path
  (`anthropic`, `ollama`, `openai-compatible`; model ids and base URLs only in the templates and
  that package): fresh ids, every slot bound, `[hive_stand] enabled = true`, `offline = true` for
  a loopback server, validated before it is returned. `hive init --provider P [DIR]` (`cli/init/`)
  writes `DIR/hive.toml`, refuses an existing one without `--force`, then sets the operator
  password through `bootstrap_operator` (10.4's "set at `hive init`"; `--no-password` skips it, a
  password already set is left alone). `hive doctor` (`cli/doctor/`) runs the seven checks in the
  order a newcomer fixes them, each PASSED, WARNED, FAILED or SKIPPED, exit 1 only on FAILED:
  provider health through `hive llm providers`' own rows (only configured providers; offline's
  refusal before any probe), each `[llm.slots]` row's model against its server's `/models` (a
  hosted provider that lists none is SKIPPED), Forage as a `[forage.map]` row for every chat
  binding (WARNED otherwise), sqlite-vec loaded on an in-memory database (WARNED: the Python
  fallback), the policy loaded as a Warden loads it, Docker through an injected probe only for a
  Docker backend; 13.2a's `--supersedure NODE` is folded in. Proved by
  `tests/e2e/test_init_doctor.py` (init, then doctor over real sockets against the scripted
  OpenAI-compatible server: served, missing model, nothing listening).
- [x] **14.3 Docs pass.** Subsystem READMEs, ADRs, protocol spec, runbooks including
  `moving-a-slot-to-local.md`, `real-cells.md`, `wardens-and-alarms.md`, `clustering.md`, `nucs.md`,
  `supersedure.md`, `remote-access.md` (the VPN, enrolling a phone or a program, a lost device, the
  Entrance Reducer).
  **Built 2026-10-28.** The five missing runbooks are written (`real-cells.md`,
  `wardens-and-alarms.md`, `clustering.md`, `nucs.md`, `remote-access.md`; `supersedure.md` and
  `backup.md` landed with 13.2a and 13.7) and `docs/runbooks/README.md` lists every runbook. Every
  subsystem has its README (`pollen` and `waggle.outbox` were missing theirs). The ADR index
  lists every ADR (0061 to 0066 are still Proposed, for the owner to accept), and every one of the
  catalogue's 89 message kinds and classes appears in `docs/waggle/spec.md` at protocol 1.16.
- [ ] **14.4 Security review.** Coding rules section 15 across the tree; `pip-audit`;
  `docs/security.md` covering the Swarm, leases, grants and attenuation, Nucs offline, the Lab,
  the Exoskeleton, and prompt injection through Honey, hot state and tool results.
  **Review and `docs/security.md` done 2026-10-28**; not ticked, since the local `pip-audit` run
  waits on the operator's go-ahead for its network lookup (CI's `audit` job runs it, and `pnpm
  audit`, on every push). The section 15 walk is the table at the end of `docs/security.md`. It
  fixed two things: the subprocess sandbox was only warned about outside `env = "dev"` and is now
  refused by the manifest and by `hive tools quarantine`, and three QEMU runner subprocess calls
  gained their `# SAFETY:` comments. What is still open is listed there too.
- [ ] **14.5 License.** Choose and add `LICENSE`; update README.
- [ ] **14.6 Release.** Tag `brood-1.0`, wheels, `pollen` binaries, the Observation Hive build
  and the Android APK, changelog from conventional commits.

### Exit criteria

- A newcomer completes the haiku goal on their own machine as the Hive Stand in under 15 minutes,
  and in a Virtual Cell in under 30, using only the README, on either provider path.

---

## Phase 15: Inspection (the operator's hands on a Cell: terminal and remote desktop)

**Goal.** The operator can go into any connected Cell by hand and work it, the way a beekeeper
opens a hive to inspect a frame: a live terminal on the Cell, and its screen with mouse and
keyboard. Both work from the `hive` CLI and from the Observation Hive, on Real Cells enrolled
through Pollen (plain, colonized and Nucs) and on Virtual Cells; the terminal also works on the
Hive Stand, whose own screen is out of scope for this phase. The two tracks
are equal goals: **Track A, the terminal**, and **Track B, remote desktop**. Track A lands first
because Track B reuses its lease, authorisation, audit and kill paths.

**Vocabulary** (proposed; added to the README glossary in 15.1). An **Inspection** is one human
session on one Cell, held as an **operator lease**. **Smoke** pauses the Cell's bees for the length
of an Inspection, named for the smoke a beekeeper uses to calm a colony before opening it.

**Depends on.** Phase 6 (the Exoskeleton's Xvfb display and 6.6's flight recordings), phase 10
(capabilities, step-up, the Entrance), phase 11 (Pollen and leases on devices), phase 12 (the
Observation Hive) and phase 13 (Sting Cut, 13.4a, and Absconding, which must end an Inspection).
Phase 15 is post-1.0: it starts after phase 14 ships (CLAUDE.md: no phase starts until the
previous one's exit criteria hold), and phase 14 depends on phases 0 to 13 only. The `hive swarm packet` work (Pollen Packet over Tailscale, ADR-0067) has landed on main and is
the transport Track A and Track B ride on to reach a device off the LAN; neither track changes
it.

### What already exists (assessed 2026-10-28)

| Piece | Evidence | Verdict |
|---|---|---|
| Pollen exec session | `pollen/executors/session.py:1-28` (open, exec, stdin, one session per lease); `pollen/executors/run/runner.py:186-193` spawns with `PIPE` for stdin, stdout and stderr; `waggle/messages/session/commands.py:233-286` (`SessionExec`: argv, `has_stdin`, `timeout_s`, no tty, no window size) | **Extend.** Byte streaming, stdin, process groups (`platform/windows.py:1-27` Job Objects) and kill-on-release are reusable. There is no PTY on any platform and no ConPTY on Windows; `timeout_s` and the 16 MiB `max_output_bytes` cap (`gateway-contract.md` section 11) are wrong for a session a human keeps open. |
| Gateway contract | `docs/waggle/gateway-contract.md:629` "No inbound socket, ever"; `:632` "no remote shell outside a lease"; `:633-634` never "runs a command through a shell it chose"; `:641` never "capture the screen or the input"; `:644` never queue `session.output` | **Amend.** A shell inside an operator lease fits the letter of `:632`. `:633-634` must allow the device's own default shell inside an operator lease only (never a shell named by request text), amended by the operator-lease ADR; `:641` must gain a narrow exception for an operator lease, and `:629` must say how a loopback-only screen server is allowed, both amended by the transport ADR. |
| Warden levers | `waggle/messages/supervision/oversight.py:95-110` (`TAKEOVER`: "the supervisor resumes the task itself from the bee's Handoff"); `queen/autopilot/table.py:113-123` and `wardens/autopilot/table.py:135-145` map a policy `TAKEOVER` to escalation; `wardens/warden.py:295-305` `intervene` | **Reuse the pause, not the name.** Takeover today is a *bee* taking over a bee's task; it has nothing to do with a human at a keyboard. The `HANDOFF` lever plus `TaskPause` that Clustering pulls (`queen/cluster/protocol.py:1-15`) is exactly Smoke, but Clustering selects by provider; it needs a by-Cell selector. |
| Entrance sockets | `entrance/streams/socket.py:1-27` (signed first frame within 5 s, capability policed, registry closes on logout, revocation or reduction, idle recheck); `:86-92` `Duplex` (client frames read by the view, used for push-to-talk); `entrance/app.py:168-193` mounts views per listener | **Reuse.** A terminal is a `Duplex` view. Video needs a binary-frame variant and a larger frame budget. |
| Step-up and listeners | `entrance/auth/step_up/rules.py:50-63` (`ActionKind`, with `STING_CUT` and `ISOLATE_CELL`); `entrance/gate/spec.py:49-50` (`LOOPBACK_ONLY`, `BOTH_LISTENERS`); `entrance/routes/isolation/cells.py:49` (`entrance:steward`, "no bee ever holds it"); `entrance/routes/isolation/sting_cut.py:1-20,140` (break-glass, remote allowed, through the Queen's door) | **Extend.** Add an `INSPECT` action kind and an `entrance:inspect` capability family modelled on Sting Cut: human-only, interactive device, fresh step-up, through `QueenDoor`. |
| Observation Hive read-only rule | `.claude/codingrules.md:1004-1012` (8.11 "One write path"); `tests/unit/entrance/test_app.py:44,107` (`UI_CAPABILITIES`, the route test) | **Conflict, resolvable.** Same resolution Sting Cut already uses: the UI's default credentials never hold `entrance:inspect`, the route test keeps passing for them, and a device the operator widened on loopback gets the pane. Needs an ADR amending 8.11. |
| Access level and tiers | `cell/tiers.py:49-60` (`READ_ONLY` < `SCRATCH` < `FULL`), `:96-105` (`CombShieldLevel`); `supervision/capping/tiers.py:93-101` (`RiskTier` up to `DEVICE_COMMAND`, `IRREVERSIBLE`); ADR-0060 (commands only where contained or fully granted); ADR-0031:87-92 (`exoskeleton:real_display` "never part of any ceiling", issued only when the Cell reports the operator allows it) | **Reuse the pattern.** A shell is uncontainable, so it needs `FULL`. The screen follows `real_display`: a device-side opt-in the Hive cannot grant itself. Capping does not gate a human's keystrokes, since the human is the top of the chain; the operator-lease ADR records this exemption from codingrules 15's "Nothing lands uncapped". |
| Watch mode and screen rule | `.claude/codingrules.md:807-814` and `:1524-1527` ("Screen or input capture on a Real Cell is a separate capability that is never issued implicitly"); ADR-0054 | **Consistent.** Inspection is that separate, explicit capability. Watch mode itself is untouched; the Warden marks the Cell `INSPECTED` while it lasts. |
| Pheromone Trail | `.claude/codingrules.md:1296-1333` (every state change trailed, never secrets or screenshots, the Night Veil skeleton); `exoskeleton/recorder/redact.py` (flight recorder redaction) | **Reuse.** New `cell.inspection_*` events with ids, durations and counts only. Recording is optional and lands as `C2`, like a flight recording. |
| Sting Cut and leases | `queen/sting_cut/__init__.py:1-8` (revokes the lease and grants, kills the lease's processes, forgets the key); `pollen/lease/deadman.py:1-12` (the packet releases every lease when the Queen goes silent) | **Reuse as is.** An operator lease is a lease, so a Sting Cut, the dead-man switch, the Entrance Reducer and Absconding already end it. |
| Virtual Cells and the Hive Stand | `cell/in_cell.py:1-17` (the Virtual Cell's Warden runs inside it); `hive/backends/qemu/process_runner.py:349-354` (`-serial file:...`, `-display none`); ADR-0031 (Xvfb, openbox, xdotool in `desktop-ubuntu`) | **Extend.** Docker can attach a tty exec from the Hive Stand; QEMU has no console attached today; the headless display already exists for the Exoskeleton. |
| Browser and CLI clients | `cli/remote/__init__.py:1-20` (`hive remote`, an enrolled device); `cli/landing/stream.py` (the CLI already opens Entrance sockets); `packages/observation-web/package.json` (no xterm.js, no noVNC) | **Extend.** Both clients exist and can sign a socket; the terminal and screen widgets are new. |

### The shape

- **One path for both tracks.** `QueenDoor.open_inspection(cell, mode)` is the only way in, the
  control plane through the Queen like Sting Cut. She checks the capability, the access level, the
  tier and the device's opt-in, Smokes the Cell when asked, opens an **operator lease** on it and
  returns an inspection id. The byte stream then flows device → Pollen's outbound link → Hive Stand
  relay → Entrance socket → CLI or browser, and never through a bee: the human acts directly and no
  model sees the bytes.
- **The device side.** On a Real Cell, Pollen runs a PTY executor (POSIX `pty`, Windows ConPTY
  through `pywinpty`) and, for Track B, a screen executor. Neither starts without the device's own
  local opt-in (`pollen allow inspect --shell|--screen`, typed on the device, stored in its state),
  so a compromised Hive can never turn either on remotely. On a Virtual Cell the Hive Stand attaches
  from outside (Docker exec with a tty; QEMU's console), because the Hive owns the machine. On the
  Hive Stand the shell is a local PTY.
- **Coexisting with bees.** Two layers. **Layer 1, during every Inspection** (the Cell is
  `INSPECTED`, derived from the Cell's Inspection rows not yet `ENDED` and re-sent to a Warden
  whenever it attaches, 15.4b): every Warden of the Cell, in the Hive Stand's process, inside a Virtual Cell or
  on a colonized device or Nuc, suspends Watch observations, the Patrol and any awake episode
  of its own, and writes nothing to Bee Bread, so the operator's command lines, logs and touched
  files never reach Bee Bread or a model through the Warden. **Layer 2, Smoke** (the default), one
  mechanism on every Cell: each task checkpoints to its Handoff (a bee that does not in time is
  retired), then the processes of every non-operator lease are killed while the lease itself stays open, its scratch, restore records
  and grants untouched, as an isolated Cell keeps its lease (codingrules.md:1849, Appendix C).
  While any Inspection row on the Cell is not `ENDED`, a second Inspection of it is refused; while
  a row that Smoked is not `ENDED`, new dispatch and new non-operator leases on the Cell are
  refused too; on close, once
  the operator lease is confirmed ended, each task this Inspection Smoked resumes on the same
  lease, unless the Cell is isolated or the Handoff is tainted (15.4b). `--alongside` gets layer 1 only: the bees keep running
  and writing their own task memory, so they can see the operator's processes and files like any
  other process on the Cell and read what the operator types, passwords included; the operator
  is told exactly that before such an Inspection opens. `--alongside` is for a terminal only and
  is refused on a Cell whose capabilities say `hive_owned`, where Smoke costs only time. Screen
  control always Smokes, because a human mouse and an Exoskeleton mouse on the same display
  corrupt each other.
- **Authorisation.** A new human-only family `entrance:inspect` with scopes `shell`, `screen_view`
  and `screen_control`, never in a bee's set and never in the UI's default credentials; widened only
  on loopback. Opening needs an interactive device and a fresh step-up (`ActionKind.INSPECT`), every
  time. Remote is allowed (it is the point of the feature) over the VPN overlay, except that a shell
  on the Hive Stand itself, or on any Cell the Queen derives as `shares_hive_stand_host` (15.5b), is
  loopback-only, and its screen is refused at the door in this phase. Whether a request is
  remote is read from the `StepUpProof` (15.4a), never from anything the client says, and the
  proof says `LOOPBACK` only when the step-up arrived on the loopback listener and carried
  ADR-0068's stand key: arriving on the loopback listener is not enough, because a guest reaches
  it too. `[inspection] hive_stand_remote` defaults to
  false and is **Justin's decision** (open decision 18): turning it on means a remote session can
  reach the Entrance's loopback listener from the Hive Stand's own shell, so device approval,
  capability widening and reopening stop being loopback-only in practice, and a stolen phone plus
  the password and a step-up could enrol a new device. If the option is kept at all, the
  operator-lease ADR states that consequence.
  **A guest gains nothing on the loopback-only rows.** QEMU's SLIRP network maps a guest's
  `10.0.2.2` to the host's `127.0.0.1` (`hive/backends/qemu/network.py:47-50`), Docker Desktop's
  `host.docker.internal` and `host-gateway` do the same, and `entrance/expose/loopback.py:58-80`
  checked only the `Host` and proxy headers. ADR-0068 (branch `fix/guest-loopback`, merged before
  this phase starts) keeps the network as it is and protects the loopback-only rows with a
  per-boot stand key that never enters a Cell, so a loopback-only row requested from inside a
  guest, with a forged `Host` and no stand key, gets 403; restricting SLIRP, removing the alias
  and moving the listener to a socket were all rejected there. The stand key guards only the
  loopback-only rows; every other row, step-up included (`entrance/routes/auth.py:170-201`, both
  listeners), still answers a guest that tunnels to the loopback listener, and records it as
  arriving on loopback. So loopback is never trusted by listener alone: a step-up counts as
  `LOOPBACK` only with the stand key (15.4a), and opening a shell on the Hive Stand or a Cell
  sharing its host needs the stand key on the open itself, an HTTP `POST` the door checks per
  Cell (15.6), since a browser WebSocket cannot carry the key. With those two, a guest, or an attacker in
  a remote shell on a Virtual Cell, can do no more there than any remote client, and remote
  shell Inspection of a Virtual Cell needs no refusal per backend. Unreachability is still needed for two arbitrary ports the stand key does not
  cover, 15.13b's VNC servers and 15.17's loopback TCP bridge, and no current backend gives it:
  every QEMU and Docker Desktop guest reaches the host's `127.0.0.1`. So on the Hive Stand, or on
  any machine running Virtual Cell backends, neither ever listens on host loopback TCP: the only
  paths are a Unix socket, a named pipe or the browser pane.
- **Limits.** `READ_ONLY` Cells: screen view only, if opted in. `SCRATCH`: screen view only; no
  shell, since a PTY cannot be held to scratch. `FULL`: everything opted in. Meadow and Propolis
  alike. **Night Veil: terminal only, never the screen, never recorded.** A Night Veil Cell is
  Virtual, so its terminal is attached from the host side (Docker exec or the QEMU console, 15.5a),
  which never crosses a network and so never puts the Hive Stand's address on the wire; a Night
  Veil Cell on a remote backend (a cloud one, later) would be reached only through its existing
  Tor Waggle link, never a clearnet route. `cell.inspection_opened` and `cell.inspection_closed`
  (ids, mode, duration, counts) join the lifecycle skeleton of codingrules section 12 and survive
  teardown; everything else about the Inspection is purged with the Cell.
- **Audit.** `cell.inspection_opened`, `cell.inspection_smoked`, `cell.inspection_closed` with the
  inspection id, device id, mode, duration, bytes and key events counted, and the close reason;
  `cell.inspection_smoked` also lists the ids of the tasks it Smoked (15.4b).
  Never content. Optional recording (a terminal transcript, or the screen) is off by default,
  kept for a retention window, announced in the client, and stored only in the operator-only area
  (15.9) under a state path the guard floor denies to every bee, never in the Basket, Nectar or
  Honey: those are bee-readable, and `C2` is a label bees hold, not a wall.
- **Ending it.** Idle timeout (`[inspection] idle_minutes`, default 15) and a hard cap (default 4
  hours); the Entrance session expiring, a logout, a revocation or the Entrance Reducer closes the
  socket and the Inspection with it; a dropped Pollen link ends it after a short grace. A Sting Cut
  or Absconding ends it with everything else. On the device, every Inspection raises a system
  notification and can be ended locally with `pollen inspect end`; a screen Inspection also keeps
  an always-on-top notice with an "end now" button up for its whole length.
- **Video.** RFB (the VNC protocol) bridged end to end: a VNC server on the device attached over
  stdio, `-inetd`, a socketpair or an owner-only Unix socket, with a one-time password, Pollen piping its bytes over an outbound data socket, the Entrance piping
  them to noVNC in the browser or to any VNC viewer through `hive inspect --screen`. Typical office
  work at 1080p costs 1 to 5 Mbit/s with Tight/ZRLE encodings and 80 to 200 ms of added latency
  through the Hive Stand hop; video playback is poor. WebRTC (H.264 or VP8, 1 to 3 Mbit/s, 50 to
  150 ms, better under loss) is the later upgrade if RFB proves too slow, at the cost of a TURN relay
  on the Hive Stand (Pollen never listens, so relay-only ICE) and an SRTP stack.

### Steps

Track A (terminal). Until the opt-in (15.3a), the notice and local end (15.3c), the door's
authority (15.4a) and Smoke (15.4b) have all landed, the PTY executor runs only on the operator's
lab devices, never on an enrolled device in daily use.

- [ ] **15.1 ADRs, vocabulary and manifest.** Write the four ADRs below before any code (the
  transport ADR as a draft, accepted at 15.10's exit, before 15.11a); add
  Inspection, operator lease and Smoke to the README glossary; amend codingrules 8.11, 12 (the two
  `cell.inspection_*` skeleton events), 15 and Appendix C (rows for the Inspection lifecycle, open,
  Smoked, `CLOSING` (close requested, the operator lease not yet confirmed ended),
  `ENDED_UNCONFIRMED` (ended by a grace, the dead-man switch or the fallback, awaiting the
  device's word or a human revocation) and `ENDED`, in
  the Brood Chamber's `inspections` table; the Warden's
  `INSPECTED` mode, derived from those rows and re-sent on attach; the operator lease kind; and the
  Inspection row and the `smoked_by` mark as state that survives a Queen crash), and gateway-contract sections 6 (the `inspect.*`
  open kinds, shell and screen, join rule 3's Queen-only list, so a Warden can never open one),
  9.1 (an operator lease opens only through `inspect.open`, never a `cell.request`; and the new
  `cell.lease_stop`, which kills a lease's processes and keeps the lease; and the device's
  reported machine id, 15.2), 9.2 (every `session.*`
  request on an operator lease is refused), 12 (the operator lease is exempt from
  left-as-found restore) and 16, as the ADRs decide.
  The section 15 changes: "Real Cells are borrowed" gains the operator lease as the one lease
  exempt from restore; "Nothing lands uncapped" exempts a human's own keystrokes and pointer;
  "Watching is bounded" names `entrance:inspect:screen_*` plus the device's opt-in as the separate
  capability for screen and input capture; the Entrance bullet adds `ActionKind.INSPECT` to what
  needs step-up. CLAUDE.md's "Real Cells are borrowed and left exactly as found" line needs the
  same exception, made when the operator-lease ADR lands. Add the `[inspection]` section
  (`idle_minutes`, `max_hours`, `hive_stand_remote`, `recording`, `link_grace_s`,
  `handoff_patience_s`, `hive_stand_shell`, `close_confirm_s`) to
  `manifest/schema.py` and `docs/manifests/full.toml`, loaded in a test. *Exit:* ADRs accepted by
  the operator; the manifest section loads with its defaults. *Size:* small, 1.5 agent-days.
- [ ] **15.2 Waggle operator channel.** An `inspect.*` message family (open, opened, resize, input,
  output, close, closed) on a new protocol minor; byte frames carry a per-Inspection sequence number
  inside a channel keyed at the signed open, so a keystroke is not a fresh signed envelope and
  never enters the replay window; no output frame is ever queued in the outbox. `inspect.open`
  carries the Inspection's `link_grace_s`, and `inspect.close` is idempotent: the device answers
  `inspect.closed` for an id it does not know or has already ended. `spec.md` and the
  gateway contract gain the section, and the open kinds are Queen-only (gateway-contract section 6,
  rule 3). The Queen learns a device's opt-in from a new `inspect_opt_in` field (shell, screen,
  remembered screen approval) on the packet's heartbeat in the same minor; it is Inspection
  availability, not a capability report, so a change never counts toward 13.4a's dire pattern.
  The same minor adds the device's machine id (`/etc/machine-id`, the Windows `MachineGuid`, the
  macOS platform UUID; under WSL, detected from `/proc/version` or `WSL_DISTRO_NAME`, also the
  Windows host's `MachineGuid`, read through interop with `reg.exe query`), a `virtualized` flag
  (`systemd-detect-virt` on Linux, WSL detection, a container check through `/.dockerenv`,
  `/run/.containerenv` or the cgroup, and the Windows and macOS equivalents where they exist) and
  `bee_account_privileged` (15.5a) to the packet's heartbeat, from which the Queen derives
  `shares_hive_stand_host` (15.5b), and one device action for Smoke, `cell.lease_stop` ("kill this lease's
  processes, keep the lease"), from the Queen or the Cell's Warden, answered with the count it
  killed: gateway-contract section 9 gains it and the conformance suite a case for it; Pollen
  implements it (creating a cgroup v2 scope per lease at lease open on Linux where the account has
  delegation, so the kill takes the scope rather than one group), the colonized or Nuc Warden sends it for its own sub-bees' leases, and the
  in-Cell Warden carries it out through its session's process tracking.
  *Exit:* contract suite covers open, resize, input, close and a replayed or out-of-order frame
  refused; an `inspect.open` signed by the Cell's Warden (on a colonized device or a Nuc) is
  dropped; flipping the opt-in in a test fixture of the opt-in store (the real `pollen allow
  inspect` is 15.3a's) shows in the next heartbeat and raises no Guard request or Sting Cut, and
  so does flipping `bee_account_privileged`; `cell.lease_stop` leaves no process of the lease running while the lease, its scratch
  and its restore records remain, and a later release restores it as found. *Size:* medium, 4
  agent-days.
- [ ] **15.3a Pollen PTY on POSIX and the operator lease.** A PTY executor on Linux and macOS
  (`pty`); the shell is the device's own default, never text from a request, started with no rc
  or profile files, a clean, minimal environment and history off (`bash --noprofile --norc` with
  `HISTFILE=/dev/null` and `set +o history`, `zsh -f` with no `HISTFILE`, and on Windows in 15.3b
  PowerShell `-NoProfile` with PSReadLine's `-HistorySaveStyle SaveNothing` or without PSReadLine,
  and `cmd /d`, which keeps no history file); a default shell other than these four falls back to
  `/bin/sh` with history off (fish, for one, would need `--private`); window resize; the
  process group recorded in the lease table before the first byte; the operator lease kind, exempt
  from left-as-found restore and named so on the trail; idle and hard timeouts; kill on release,
  Sting Cut and dead-man. The device-side opt-in lands here, with the executor, so no build of the
  packet ever carries an ungated shell: `pollen allow inspect --shell|--screen` and `--revoke`,
  and the executor refusing with `pollen.capability_denied` without it. The opt-in is human-on-
  device only, because a bee with exec on a `FULL` Cell can run any command the packet's account
  can, and a colonized Warden can write the packet's state: `pollen allow inspect` refuses
  without an interactive console or when it descends from the packet, then asks for the OS's own
  elevation (UAC, `sudo` or polkit, macOS authorization), and writes the opt-in to a store the
  packet's account can read but not write (root- or Administrators-owned); a device where that
  account elevates without a prompt (passwordless `sudo`, an elevated packet) cannot opt in at
  all. On Windows an admin account at UAC's default level can be elevated silently by a known
  bypass, so the opt-in works only when the packet runs under a standard (non-admin) account or
  UAC is set to "Always notify"; otherwise `pollen allow inspect` refuses and says which of the
  two to change. The operator lease opens only through the Queen-only `inspect.open`: the packet refuses a
  `cell.request` of the operator kind, from the Warden or anyone, and every `session.*` request
  (exec, files, process) on an operator lease. As a second wall (gateway-contract section 10), it
  refuses a PTY on any lease whose granted access is below `FULL`. *Exit:* through a Waggle-level
  test harness standing in for the operator's terminal (the clients arrive in 15.7 and 15.8), on
  Ubuntu and Arch, a device that has not opted in refuses; one that has renders and resizes `vim`
  and `htop`; killing the link ends the shell after the grace; a real `pollen allow inspect` on the device shows in the
  next heartbeat; `pollen allow inspect` run through
  `session.exec`, and a direct write to the opt-in store by the colonized Warden's account, are
  both refused; a Warden `cell.request` for an operator lease and a `session.exec` on an open
  operator lease are refused; a PTY on a `SCRATCH` lease is refused. *Size:* medium to large, 3.5
  agent-days.
- [ ] **15.3b Windows ConPTY.** The same executor on Windows through `pywinpty` (MIT), added to the
  packet build on every platform's CI; the Job Object recorded before the first byte. *Exit:* the
  15.3a run on Windows 11 in PowerShell and `cmd`, from the single-file packet; under an admin
  account at UAC's default level, `pollen allow inspect` refuses and explains, and it succeeds
  under a standard account and under "Always notify". *Size:* medium, 2.5 agent-days.
- [ ] **15.3c Device notice and local end.** A system notification when an
  Inspection opens and closes (a Windows toast, `notify-send`, `osascript`; a headless device
  prints to the packet's console and log instead); `pollen inspect end` ends every open Inspection
  from the device; `pollen status` lists them. *Exit:* through the Waggle-level harness, every open
  and close raises the notification; `pollen inspect end` closes the harness's terminal within
  five seconds with reason `DEVICE_ENDED`. *Size:* small, 1.5 agent-days.
- [ ] **15.4a Inspection authority.** Human authority is a `StepUpProof` that only the Entrance's
  step-up verifier (`entrance/auth/step_up/`) can construct, naming the device, the session, the
  moment of the step-up and whether it was local (`LOOPBACK` or `REMOTE`). It is `LOOPBACK` only
  when the step-up both arrived on the loopback listener and carried a valid ADR-0068 stand key,
  compared in constant time, and `REMOTE` otherwise: the arrival the Entrance records
  (`entrance/auth/step_up/ceremony.py:104,181`) says loopback for a guest that tunnelled through
  `10.0.2.2` or `host.docker.internal` too, so the door never trusts the listener alone. `QueenDoor.open_inspection` requires one and records the Inspection
  in the door's live table with an unguessable nonce that only the door holds; an
  `InspectionWarrant` is the Inspection id plus that nonce, and is valid only while it matches a
  live row. An import-linter contract keeps `StepUpProof`, the warrant's constructor and every
  host-side attach entry point (below) out of every `queen/` module except the door, above all
  `queen/awake` (which holds the Queen) and `queen/guard_requests` (whose `decision/act.py:47,135`
  already calls Sting Cut from model-driven decisions), and out of every Warden and autopilot.
  The host-side attach paths never pass `FrameSender.send` and share the process with the
  Level-0 Wardens, so they get the same wall at their own entry: the Docker tty exec and the
  QEMU console socket (15.5a), the Hive Stand PTY in `cell/local/pty/` (15.5a), and 15.13b's
  `docker exec` of x11vnc and QEMU `-vnc` each take a live `InspectionWarrant` and check it
  against the door's table before attaching, and their output goes only to the door's relay,
  never to a hub listener. Every `CellSession` kind (`LocalProcessSession`, `InCellSession` and
  the Pollen session) refuses an operator-kind lease unless it comes from the door with a live
  warrant, and refuses all `session.*` work on an operator lease. **Closing never needs a
  proof:** most closes have no human behind them (the idle and hard timeouts, the link-drop
  grace, the Entrance Reducer, a revocation, a session expiry, a Sting Cut, model-driven ones
  included, and Absconding), and a close only narrows, so `inspect.close` is the system-close
  path: anything may send it and the guard always lets it through. The guard sits at the one
  path every Hive-to-device frame takes, `FrameSender.send` (`swarm/gateway/frames.py:149`,
  reached through `DeviceLink.send`, `request` and `stream`, `link.py:79,184`), where the caller
  picks the sender string, so a Level-0 Warden in the same process, signing as `hive_id`, passes
  it too. It drops every `inspect.*` frame except `inspect.close` (open, input, resize) whose
  warrant does not match the door's live table. On the way in, every device-to-Hive `inspect.*`
  frame (output, closed, and later 15.18's `get_file` responses) goes only to the door's relay.
  An `inspect.closed` is matched against the `inspections` table's rows not yet `ENDED` as well
  as the live table, so a confirmation that arrives after a Queen restart still counts; any other
  late or unmatched frame is dropped and never reaches `on_frame` or the hub's
  listeners (`hub.py:1-12`), the Queen's swarm gate included. *Exit:* a step-up on the loopback
  listener with the stand key gives a `LOOPBACK` proof; one on the loopback listener without it,
  and one on the remote listener with a valid key, give `REMOTE`; a Queen awake episode cannot
  open an Inspection; neither a Level-0 Warden nor Queen autopilot can send
  `inspect.open`; an `inspect.input` or `inspect.resize` sent by a Warden for an *open*
  Inspection, with the right id, is dropped at `FrameSender.send`; an `inspect.input` without a
  warrant is dropped; an idle-timeout close reaches the device through the guard; an
  `inspect.output` for a closed Inspection reaches no hub listener; the lint contract fails on any
  module outside the door that imports `StepUpProof` or builds a warrant. *Size:* medium, 2.5
  agent-days.
- [ ] **15.4b Smoke, the trail and the timeouts.** The two layers of the shape. Layer 1, on every
  Inspection, `--alongside` included: the `INSPECTED` mode on every Warden, the Hive Stand's, an
  in-Cell one and an on-device one on a colonized device or Nuc alike: no Watch observations, no
  Patrol, no awake episode of the Warden's own, nothing written to Bee Bread by it until the
  Inspection closes. `INSPECTED` is derived from the Cell's Inspection rows not yet `ENDED`,
  never held only in a Warden's memory: the Queen re-sends it whenever a Cell's Warden attaches or reconnects
  (`OFFLINE` to `ACTIVE`, `CLUSTERED` to `ACTIVE`, a restart), as she re-sends a `CellTaintOrder`
  (codingrules.md:1849), and lifts it only when the Inspection's row reaches `ENDED` (below). Layer 2,
  Smoke, is one mechanism on every backend, with no branch on the
  Cell's kind. Every bee on the Cell is asked for a Handoff (Clustering's lever, selected by
  Cell) and given `[inspection] handoff_patience_s` (default 30 s), bounded the way
  `pause_for_move` bounds its wait. Bees are in-process Worker runtimes, so killing their
  processes alone would leave them alive: at `handoff_patience_s` every sub-bee that has not
  handed off is retired through the Warden's one ending path (`retire_sub_bee`,
  `wardens/ticks/alarms`), and its task later resumes from its last checkpoint, so work since
  that checkpoint may be redone. Then the processes of every non-operator lease are killed with
  15.2's `cell.lease_stop`, and the lease stays open: its scratch, restore records and grants
  are untouched, as an isolated Cell keeps its lease (codingrules.md:1849). The kill reaches what
  the lease contains, never the account at large, since on a real device that would kill the
  owner's own apps: on Windows the lease's Job Object, which never allows breakaway; on Linux a
  cgroup v2 scope per lease, created at lease open (a `systemd --user` scope) where the account
  has delegation, else the process-group kill, so a process that left its group (`setsid`, a
  double fork, a tmux or screen server) may survive; on macOS the process-group kill, with the
  same residual; inside a Virtual Cell, the session's tracked processes. The Hive Stand's own
  leases get the same containment, which they lack today (`cell/local/releaser.py:202-245` and
  `cell/local/process.py:262` kill the group the root process leads): `LocalProcessSession` gains a Job Object
  per lease on Windows, without breakaway and tracked apart from the root process so a
  descendant that outlives its root is still in it, and a cgroup v2 scope per lease on Linux
  where delegation exists, else the group kill. It lives in a new sub-package
  `cell/local/containment/`, since `cell/local/` already holds ten modules, and like Pollen's
  `platform/job_objects.py` a `LocalProcessSession` process starts suspended and resumes only once
  it is in its Job Object or scope, so nothing escapes before containment. Containment stops
  ordinary and accidental
  descendants; it does not stop a deliberate, live escape by the same account, which survives
  Smoke on every platform: on Linux a bee moving itself within its delegated subtree or calling
  `systemd-run --user`, on Windows `Win32_Process.Create` through WMI, a COM server or a run-now
  Scheduled Task, on macOS `launchctl submit`. That is accepted, and tied to the operator
  guidance: on a device or Cell that has hosted `FULL` leases, treat the session as observable
  and never type an elevation secret there. The gates key on two scopes. Any row on the Cell not
  `ENDED` (open, `CLOSING` or `ENDED_UNCONFIRMED`), `--alongside` ones included, keeps the Cell `INSPECTED`, refuses a
  second Inspection of it and fails the Supersedure preflight. Only a row that Smoked and is not
  `ENDED` makes the Queen and the Warden refuse new dispatch to the Cell and new non-operator
  leases on it, and makes every `CellSession` kind (`LocalProcessSession`,
  `InCellSession` and the Pollen session) refuse exec, process `START` and file operations on
  a non-operator lease, as the packet refuses every `session.*` request (absolute: no
  Inspection feature uses `session.*`). On a Pollen device or a Nuc the operator's shell can
  outlive a close (the link can come back within `link_grace_s`, and no dead-man switch fires
  while a Warden is on the device), so resuming and lifting `INSPECTED` share one durable gate. A
  close first moves the Inspection's row to `CLOSING` and sends `inspect.close` (or releases the
  operator lease); the row reaches `ENDED` once the operator lease is confirmed ended by the
  device, an `inspect.closed` or the operator lease's release report. `inspect.close` is
  idempotent: the device answers `inspect.closed` for an id it does not know or has already
  ended. `CLOSING` is bounded: if no confirmation arrives within `[inspection] close_confirm_s`
  (default 60 s), the Queen sends a forced end of the operator lease only (the operator lease's
  release), not a Sting Cut, so the Smoked leases and the Cell's isolation
  are untouched, or drops the device's link. An end reached without the device's word (the link
  grace, the dead-man switch or this fallback, whose grace allows for the device noticing the lost
  link late: the heartbeat timeout plus `link_grace_s`) is marked `ENDED_UNCONFIRMED`. The device
  takes its `link_grace_s` from the signed open, so both sides agree on it, and its grace and
  dead-man timers count suspended time (`CLOCK_BOOTTIME` on Linux, the wall clock or
  `mach_continuous_time` on macOS, `GetTickCount64` or the interrupt time with its bias on
  Windows), so a laptop that sleeps through the grace wakes with its shell already ended. This
  changes the existing dead-man clock (`pollen/lease/deadman.py`) for every lease, not only the
  operator's. After Requeening the timer starts when the new Queen starts. On every
  reconnect of a device with a row that is not `ENDED` (`ENDED_UNCONFIRMED` included), the Queen
  re-sends `inspect.close` and the forced end before anything else, so a fast redial cannot keep
  the timer running forever, and where that row Smoked, nothing is dispatched to or resumed on
  the Cell until the device confirms. `ENDED_UNCONFIRMED` counts as not `ENDED` for every gate,
  the second-Inspection refusal and the Supersedure preflight included. A device that never
  comes back is resolved by the human with `hive swarm revoke` through the operator desk, a
  loopback-only action that needs the stand key: it moves the device's rows to `ENDED`, clears
  their `smoked_by` marks and lets the tasks be placed elsewhere. Revocation is final: a revoked
  device accepts no change and can never relink (`swarm/operator/desk.py:36`,
  `NodeRevokedError`), so no bee is ever resumed beside a shell that might come back. A row ended
  this way lifts `INSPECTED` but resumes nothing, since its marks are already cleared. Only at a confirmed `ENDED` is `INSPECTED` lifted and do the tasks still marked `smoked_by` this Inspection
  resume, each from its Handoff on the same, still-open lease, and each mark is cleared
  in the same transaction as its resume; none resumes on a Cell that is `ISOLATED` or from a
  tainted Handoff, since isolation and its human-only lift take precedence
  (codingrules.md:1849), and the close clears those marks too, so a later lift leaves the task
  paused. Because the lease never closes, every
  path that ends a lease (the dead-man switch, Sting Cut, Absconding, Requeening's recovery, a
  task cancel or re-placement) restores it exactly as it does today. A Queen that starts by
  Requeening (13.1) system-closes every Inspection row that is not `ENDED`, open or `CLOSING`,
  with or without a lease: it moves the row to `CLOSING`, sends `inspect.close` to a Pollen Cell
  (or releases its operator lease) and waits for the confirmation that moves it to `ENDED`,
  writes exactly one close with its reason, lifts `INSPECTED` on the Cell's Wardens and, from
  15.5a, tears down its host-side attach state (the QEMU console socket
  and its login session, any `docker exec` attached for it), and only then resumes the tasks that
  Inspection Smoked. Every Inspection has that row: the door writes it at open, before the
  operator lease opens, for every Inspection, `--alongside` and idle-Cell ones included, in a new
  Brood Chamber `inspections` table in the Hive's SQLite; the `cell.inspection_*` trail events
  mirror it with ids and counts only. Smoke only adds to it, before the first Handoff request: the
  Smoked task ids, also on `cell.inspection_smoked`, and a `smoked_by` mark on each task's Brood
  Chamber row. Requeening reads the table, never memory, since the door's table that held the
  warrant died with the old Queen: once the row is `ENDED`, it resumes every task still marked
  `smoked_by` that Inspection, clearing each mark in the same transaction, and
  clears without resuming those on an `ISOLATED` Cell or with a tainted Handoff; the same rule
  covers a crash between a normal close and its resumes. A Supersedure (13.2a) never meets one:
  its preflight refuses while any Inspection row is not `ENDED`, so the operator closes them first
  and their ends are confirmed, and the door refuses to open an Inspection while a Supersedure is
  in progress. If killing a Nuc Warden's
  lease processes stops its model servers (`wardens/ticks/control.py` `_stop_what_the_lease_runs`),
  Cells routed to them fall back along their hosting plans for the length of the Inspection.
  Killing processes is chosen over freezing them, or over running the operator shell as another
  account on a borrowed device: a killed process cannot read, attach to or log the operator's
  PTY, which shares the account. The on-device Warden itself stays up: it is Hive code that, in
  `INSPECTED`, runs only its autopilot and never reads the operator lease's processes. The access
  level, tier and Hive Stand checks; the `cell.inspection_*` events and their Night Veil boundary
  rows; a `guard.entrance_inspection_opened` push event sent to every other enrolled device
  (codingrules 8.15 and 15), saying only that an Inspection opened and on which Cell; the idle and
  hard timeouts, and Absconding, ending every open Inspection. *Exit:* on a Pollen
  device and a Nuc, opening on a Cell with a running task kills its lease's processes, a child
  started with `setsid` included where the lease has a cgroup scope or a Job Object, before the
  shell starts, while the lease stays open with its scratch and a touched outside path, and
  closing resumes the task on the same lease with no work redone; on a Windows and a Linux Hive
  Stand, as a lease-level test with no shell, `cell.lease_stop` on a lease whose root process
  has exited kills a descendant that outlived it (a `setsid` child on Linux with delegation) and
  leaves the lease open; a bee that ignores the Handoff request is retired after
  `handoff_patience_s`, starts no process for the rest of the Inspection (on a Pollen device here,
  on the Hive Stand and Docker in 15.5a), and its task resumes from its last checkpoint; a Sting
  Cut during an Inspection ends it and restores the Smoked lease as found; `--alongside` leaves
  the task and its processes running; a `SCRATCH` Cell refuses a shell; every other enrolled
  device receives the push; an Inspection idle past `idle_minutes`, and one open during `hive
  abscond`, each end within five seconds with one `cell.inspection_closed` carrying the right
  reason; while an Inspection is open, a task dispatch and a new non-operator lease on that Cell
  are refused; after an Inspection on a Nuc that ran `mysql -psecret` and edited a file, no
  history file under the account changed, nothing from it (command line, process, log line or
  file change) is in Bee Bread and no Warden episode
  or Patrol ran meanwhile, and the same holds for an `--alongside` Inspection on the Nuc; a Cell
  routed to the Nuc's model server falls back during the Inspection and returns after it; killing the Queen mid-Inspection on a Pollen device
  ends the Inspection under the restarted Queen, with exactly one `cell.inspection_closed`, the
  Smoked task resumes only after that close, dispatch to the Cell is accepted again, and a
  Supersedure preflight then passes; killing the Queen inside the `handoff_patience_s` window,
  before any process was killed, also leaves every Smoked task resumed after the restart;
  killing the Queen during an `--alongside` Inspection, and during one on an idle Cell, also
  writes exactly one `cell.inspection_closed`, and the Cell's Warden runs Watch and the Patrol
  again afterwards; a task paused for another reason before the
  Inspection is still paused after the restart; a Cell isolated during an Inspection resumes nothing when
  it closes, a task paused before the Inspection for another reason stays paused, and a tainted
  Handoff is never resumed; a stale `smoked_by` mark left from an earlier Inspection is not
  resumed by a later one; lifting the isolation of a Cell that was isolated during an Inspection
  leaves its task paused; a crash between a close and its resumes still resumes the marked tasks
  after the restart; killing the Queen and restarting it within `link_grace_s`, on a Pollen device
  and on a Nuc, leaves the operator's shell gone before the Smoked task resumes; a timeout close
  whose `inspect.closed` is delayed keeps the row `CLOSING`, the Cell `INSPECTED` and the task
  paused until the device confirms, and meanwhile dispatch, new leases and a second Inspection on
  the Cell are refused and a reattaching Warden comes back `INSPECTED`; an `inspect.closed` that
  arrives after a Queen restart moves the durable row to `ENDED`; a repeated `inspect.close`, and
  one for an id the device never knew, are answered with `inspect.closed`; the operator lease's
  release report alone moves the row to `ENDED`; a device that never answers reaches
  `ENDED_UNCONFIRMED` through the `close_confirm_s` fallback, before and after a Queen restart,
  its Smoked tasks stay paused, and on its next reconnect the Queen re-sends the close and the
  forced end, gets the confirmation and only then resumes them; `hive swarm revoke` of that
  device on loopback with the stand key instead moves its row to `ENDED`, clears the marks,
  lifts `INSPECTED`, resumes nothing on it and re-places the tasks, the revoked device's redial is
  refused, and the same revocation from a remote device or without the key is refused; the fallback leaves the Smoked
  leases open and the Cell's isolation unchanged; a device that redials faster than
  `close_confirm_s` still has its operator lease ended; a laptop suspended across the grace
  wakes with no operator shell running beside the resumed bees; an `--alongside` Inspection in
  `CLOSING` keeps the Cell `INSPECTED` but does not refuse dispatch; a Warden that restarts mid-Inspection comes back `INSPECTED`, with Watch and
  the Patrol off until the close; a Supersedure started with an Inspection open is refused by
  its preflight, and an Inspection open attempted while a Supersedure is in progress is refused.
  *Size:* large, 6 agent-days.
- [ ] **15.5a Host-side attach and capability fields.** Needs ADR-0068's guest-loopback fix
  landed first. Docker: an exec with a tty attached from the Hive Stand. QEMU: a virtio console
  on a Unix socket, `AF_UNIX` on Windows too (QEMU supports it there; a `-chardev pipe` would be
  created with default security), created for each Inspection and existing only while it is
  open, inside an owner-only directory (an explicit DACL on Windows) under a Hive state path the
  guard floor denies to every bee; the guest's console runs a login program from the image that
  accepts only a one-time code derived from a per-Cell secret placed by cloud-init at
  provisioning, never an autologin, and takes one client. The Hive Stand: a local PTY in a new
  sub-package `hivemind/cell/local/pty/`, since `cell/` is where subprocess is allowed and
  `cell/local/` already holds the ten modules codingrules 5.6 allows. Smoke is 15.4b's,
  unchanged; the in-Cell Warden stays up under layer 1 only. What differs per backend is read
  from named fields, never from the Cell's kind. Four live Hive-side, on the Queen's record of
  the Cell (`CellCapabilities`, `cell/models.py:105`), derived from the backend or the opt-in
  source and never reported by Pollen, so they never touch the wire report 13.4a watches:
  `hive_owned` (no device opt-in needed, `--alongside` refused), `operator_account_isolation`
  (the operator's shell runs as its own OS account that the bee account cannot read),
  `host_side_attach` (the terminal and screen are attached from the Hive Stand, not through
  Pollen) and `opt_in_source`, which tells the door where a Cell's opt-in comes from: `PACKET`
  (the Pollen heartbeat's `inspect_opt_in`, 15.2), `HIVE_STAND_MANIFEST` (`[inspection]
  hive_stand_shell = true`, a key only the operator at the Hive Stand can set: it is read from
  the manifest on loopback and is never writable by the Queen, a bee or any Entrance route) or
  `HIVE_OWNED` (none needed). The fifth, `bee_account_privileged` (the bee account has root or
  `sudo`), can change legitimately, so for a `PACKET` Cell it rides the heartbeat beside
  `inspect_opt_in` (15.2) as Inspection availability, never a 13.4a capability report; a flip to
  true applies at once, a flip to false counts only from a fresh heartbeat, and the door
  re-checks it at every open. Virtual Cell backends set it from the image, and the Hive Stand
  from its own account. `--alongside` on the Hive Stand is permitted, under the general
  `--alongside` disclosure. Virtual Cells get `hive_owned`, `host_side_attach` and
  `opt_in_source = HIVE_OWNED`; Pollen Cells and the Hive Stand get `hive_owned`,
  `operator_account_isolation` and `host_side_attach` false and `opt_in_source` `PACKET` or
  `HIVE_STAND_MANIFEST`. On QEMU, cloud-init creates the operator account, its home and the PTY's
  owner, and mounts `/proc` with `hidepid=2`, so `operator_account_isolation` is true. On Docker
  the runtime mounts `/proc` and `CAP_SYS_ADMIN` is dropped (`hive/backends/docker/backend.py:400`),
  so `hidepid` is not available and the field is false; Smoke retires every sub-bee and kills the
  session's tracked processes, so what can still see the operator's command lines is the in-Cell
  Warden, Hive code running no episode, and any process that escaped the session's tracking;
  both residuals are disclosed. For a screen Inspection, Smoke kills every lease process except
  the Exoskeleton's Xvfb, which the screen needs; the in-Cell Warden could still talk X to that
  display, also disclosed. Where `bee_account_privileged` is true the separation does not hold,
  so an Inspection there is refused while any task runs on the Cell and says why. A Night Veil
  Cell uses the same host-side attach, which crosses no network. *Exit:* through the same
  harness, the shell works on a Docker Cell, a QEMU Cell, a Night Veil Docker Cell and the Hive
  Stand; from an Inspection shell on a QEMU Cell and on a Docker Desktop Cell, a request to the
  Entrance's loopback port through `10.0.2.2` or `host.docker.internal`, with a forged `Host:
  localhost` and no stand key, to a loopback-only row gets 403 (ADR-0068); on a QEMU Cell, a
  second local client on the console and one without the one-time code are refused, and the
  socket is gone after close; on Docker and QEMU, a running task's processes are gone during the
  Inspection, the in-Cell Warden stays up, and the task resumes on the same lease after close; on
  Docker a bee that ignores the Handoff request is retired and starts no process while the
  Inspection is open; on QEMU the bee account can neither read the operator's PTY nor list the
  operator's processes; on Docker a screen Inspection leaves only the Xvfb of the lease's
  processes running; after an Inspection on a Docker Cell that ran `mysql -psecret`, nothing from
  it is in Bee Bread and no history file under the account changed; a Cell reporting `bee_account_privileged` refuses an Inspection while a task
  runs; `--alongside` on a `hive_owned` Cell is refused; the Hive Stand refuses a shell until
  `hive_stand_shell = true` is set and accepts one after, and neither a bee nor an Entrance route
  can set it; on the Hive Stand, opening on it with a running task kills that lease's processes
  before the shell starts and closing resumes the task on the same lease, a bee that ignores the
  Handoff request is retired and starts no process while the Inspection is open, and after an
  Inspection there that ran `mysql -psecret` and edited a file, nothing from it is in Bee Bread,
  no history file under the account changed, and no Warden episode or Patrol ran meanwhile; a Warden or autopilot call to the Docker tty
  exec, the QEMU console or the Hive Stand PTY without a live warrant is refused, and so is an
  operator-kind lease asked of any `CellSession` kind without one; output from those paths
  reaches no hub listener; killing the Queen mid-Inspection on a Docker and a QEMU Cell ends the
  Inspection, leaves no console socket, login session or attached `docker exec` behind, and the
  Smoked tasks resume after. *Size:* medium, 3 agent-days.
- [ ] **15.5b Co-location with the Hive Stand.** The Hive Stand rule is enforced at the door,
  from the proof's stand-key remoteness (15.4a): with `hive_stand_remote = false` the door
  refuses a remote shell on the Hive Stand, and on any Cell sharing its host, before any lease
  opens; it also requires the stand key on the open itself (15.6's `POST`), checked per Cell by
  the door, not by ADR-0068's path gate. `shares_hive_stand_host` is not a reported capability,
  since a reported field flipping when the Hive Stand moves would trip 13.4a's dire pattern: for
  a Cell whose `opt_in_source` is `PACKET`, the Queen derives it at open time by comparing the
  machine id the device reports (15.2) with the Hive Stand's own, and recomputes it whenever the
  Hive Stand moves. For those Cells it fails closed: a missing, empty or unreadable machine id
  counts as sharing the host, so the remote shell and the screen are refused, and so does any
  `PACKET` Cell whose heartbeat says `virtualized`, since a VM or container may run on the Hive
  Stand whatever its own id says. A WSL distribution on a Windows Hive Stand can read its files
  through `/mnt/c` while having its own machine id, so a packet under WSL also reports its Windows
  host's `MachineGuid` (15.2), which the Queen uses as the Cell's machine id; if WSL is detected
  but the host id cannot be read, the Cell fails closed. The operator can lift the `virtualized`
  fail-closed for one device with `hive swarm mark-separate <device>` ("this VM is on a different
  machine"), a loopback-only row that needs the stand key, listed in the committed OpenAPI
  document with its route-test allow-list row; no bee and no remote session can call it. A mark
  lifts only that fail-closed: the machine-id match, WSL included, still applies. It records the
  Hive Stand id it was made against and is void once that id changes (a Supersedure) or once the
  device's reported machine id changes. The Hive Stand derives its own id by the same rule, so a
  Hive Stand running under WSL uses its Windows host's `MachineGuid`, and it checks its own
  `virtualized` state: while the Hive Stand is itself virtualized, every `PACKET` Cell counts as
  sharing its host until the operator marks it separate, bound the same way. The Hive Stand's own
  detection (`systemd-detect-virt`, the container checks, `reg.exe` through WSL interop) runs in
  a new module `cell/local/identity/`, since `cell/` is where subprocess is allowed. A Cell with
  `host_side_attach` derives false, since ADR-0068's stand key and the absence of any mount of the
  Hive's state cover it, and a Cell whose `opt_in_source` is `HIVE_STAND_MANIFEST` (the Hive Stand
  itself) derives true. The rule reads those fields only, never the Cell's kind. It covers a
  Pollen packet installed on the Hive Stand, and after a Supersedure the *new* Hive Stand, the
  former candidate, whose packet and Warden keep running there (13.2a); the old, superseded
  machine no longer shares the host. ADR-0068's per-boot stand key limits what a local process
  can do on loopback-only rows, but a shell running as the Hive's own account can still read the
  serve record, so the rule is still needed. *Exit:* the Queen derives `shares_hive_stand_host`
  true for a `PACKET` Cell with the Hive Stand's machine id or with none and for the
  `HIVE_STAND_MANIFEST` Hive Stand, false for a `host_side_attach` Cell, and recomputes it after a
  Supersedure, when it holds for the new Hive Stand's packet and not for the old machine's; a
  packet under WSL on the Windows Hive Stand derives true from its host's `MachineGuid`, and one
  under WSL whose host id cannot be read derives true by failing closed; a virtualized packet
  derives true until `hive swarm mark-separate` is run for it, which works only on loopback with
  the stand key and is refused remotely, without the key and to any bee; a Hive Stand running
  under WSL derives its id from its Windows host's `MachineGuid`; a marked VM packet derives true
  again after a Supersedure onto its host, and after its reported machine id changes; a marked VM
  packet whose machine id matches the Hive Stand's still derives true; on a virtualized Hive
  Stand, an unmarked bare-metal `PACKET` Cell derives true and a marked one false; the
  `mark-separate` row is in the OpenAPI document and the route test (the remote refusals
  themselves are 15.6's exits, once the open route exists). *Size:* medium, 2.5 agent-days.
- [ ] **15.6 Entrance terminal route.** Open and attach are split, because a browser WebSocket
  cannot set the `X-Hive-Stand` header. `POST /v1/cells/{cell}/inspect/terminal` behind
  `entrance:inspect:shell` and `ActionKind.INSPECT` opens the Inspection through the door and
  returns its id and a one-time attach token; the stand key, when present, rides this `POST`,
  and the door checks it per Cell (15.5b), not ADR-0068's path gate. A `Duplex` WebSocket then
  attaches by that id and token, which it spends. OpenAPI regenerated; the route test gains its
  allow-list rows. *Exit:* a device without the scope gets 403, one without fresh step-up gets
  `step_up_required`, a reduction closes an open terminal; an attach with a wrong, reused or
  other Inspection's token is refused; with `hive_stand_remote = false`, a remote open of a Hive
  Stand shell is refused before any lease opens while a loopback one with the stand key
  succeeds; a remote shell on a Pollen Cell co-located with the Hive Stand, or reporting no, an
  empty or an unreadable machine id, or reporting `virtualized` without a `mark-separate`, is
  refused; a step-up followed by a Hive Stand shell open,
  both made from inside a QEMU guest and a Docker Desktop guest through the loopback listener, is
  refused. *Size:* medium, 2.5 agent-days.
- [ ] **15.7 `hive inspect` in the terminal.** `hive inspect <cell> [--alongside]` locally and with
  `--remote`; raw mode on Windows and POSIX, resize on `SIGWINCH` or console events, `~.` to leave.
  *Exit:* the 15.3a run from a laptop over the VPN overlay. *Size:* medium, 2 agent-days.
- [ ] **15.8 Terminal pane in the Observation Hive.** xterm.js (MIT) on the Cell page, shown only
  when the device holds the scope; the `INSPECTED` badge on the Cell header and Fleet row, beside
  the tier and mask badges and never colour-only. *Exit:* the same run from the Android app and a
  desktop browser. *Size:* medium, 2 to 3 agent-days.
- [ ] **15.9 Terminal recording (optional).** A transcript in asciicast form with a retention
  window, announced in the client, refused on Night Veil, and stored in the **operator-only
  area** this step creates: a directory under a Hive state path the guard floor denies to every
  bee, outside the Basket, Nectar and Honey (all bee-readable, `C2` being a label bees hold).
  15.18's downloads and 15.19's screen recordings use the same area. It is read only through
  routes that need the matching `entrance:inspect` scope (`shell` for transcripts and terminal
  downloads, `screen_view` for screen recordings and screen downloads), a fresh
  `ActionKind.INSPECT` step-up, and, for an Inspection opened under the Hive Stand rule (the
  door records `shares_hive_stand_host` at open), the stand key and a `LOOPBACK` proof; the
  UI's default credentials never reach them, and each route gets its route-test row, added here
  for transcripts and in 15.18 and 15.19 for theirs. *Exit:* a recorded session replays; an
  unrecorded one leaves no bytes anywhere; no bee can read a recording, and none appears in the
  Basket, Nectar or Honey; the UI's default credentials get 403 on the read route, and a device
  with the scope but no fresh step-up gets `step_up_required`; a transcript of a Hive Stand
  Inspection is refused to a remote device, and to a loopback one without the stand key.
  *Size:* small, 2 agent-days.

Track A total: about 36 agent-days.

Track B (remote desktop). The on-device notice (15.12) lands before any step that drives a Real
Cell's screen, and every Real Cell exit below is run with it up; until it lands, 15.11a and 15.11b
run only on the operator's lab devices and Virtual Cells. The Hive Stand's own screen is
out of scope for this phase: an operator at loopback is already at that machine, and a remote
one would reach the loopback listener (see `hive_stand_remote`). Until the pane and `hive inspect
--screen` land in 15.17, exits run through a test VNC client (TigerVNC's viewer on the Hive Stand)
attached to the Entrance relay by a test harness.

- [ ] **15.10 Spike: RFB bridge against own capture.** Measure a bundled VNC server on stdio bridged
  through Pollen against capture-and-encode in the packet (`mss` plus JPEG tiles, input through
  `SendInput` and `xdotool`), on Windows and X11, for latency, bandwidth, CPU and binary size. It
  runs only on a lab machine of the operator's or on a Virtual Cell, never on an enrolled Real
  Cell, because the notice (15.12) does not exist yet.
  *Exit:* numbers recorded in the transport ADR, the operator's choice made and the ADR accepted,
  before 15.11a starts. *Size:* medium, 2
  agent-days.
- [ ] **15.11a Screen data socket, device side.** A second outbound WebSocket from Pollen, opened
  only inside an operator lease, carrying RFB bytes from a VNC server started inside the lease.
  The server is attached over stdio, `-inetd`, a socketpair or an owner-only Unix socket wherever
  it supports one, so no other process on the device can connect to it. Where none is possible
  (some Windows servers), it listens on loopback TCP for a single connection only, and its
  one-time password reaches it through stdin or an owner-only file, never argv or the
  environment, so the process list never shows it. It dials the Queen's persisted swarm-listener URL, the only address a gateway may dial
  (gateway-contract sections 5 and 16), at its own path (`/inspect/<inspection id>`), never the
  Entrance. Its first frame is a hello signed with the device key carrying the one-time token from
  the signed open; the listener checks both and binds the socket to that Inspection. It is a
  separate byte channel, not Waggle frames, so the link's frame ceiling (6,000 a minute,
  gateway-contract section 7) does not apply to it; the Inspection's byte budget and idle timeout
  bound it instead.
  As in 15.3a, no build ever carries an ungated screen: the screen executor ships with its gate,
  refusing with `pollen.capability_denied` unless the device opted in with `--screen`, whatever
  the Hive asks. *Exit:* bytes round-trip against a test server with the Inspection's limits; a
  lease release closes the socket and stops the server; a device that opted in with `--shell` only
  refuses; a screen open signed by the Cell's Warden is dropped; with a loopback-TCP server, a
  second local connection is refused and a password scraped from the process list does not work.
  *Size:* medium, 3 agent-days.
- [ ] **15.11b Screen door and relay at the Entrance.** The swarm listener's `/inspect/` path, which
  hands the socket in process to a binary `Duplex` view, opened and attached the 15.6 way: `POST
  /v1/cells/{cell}/inspect/screen` behind `screen_view` or `screen_control` opens it and returns
  an id and one-time attach token, and the WebSocket attaches by them, landing together with its gates so no build ever serves an ungated screen:
  `QueenDoor.open_inspection` learns the screen modes and checks each before any lease opens
  (`screen_view` at every access level, `screen_control` only on `FULL`; never on Night Veil; never
  on a Cell whose derived `shares_hive_stand_host` is true, the Hive Stand itself included through
  `HIVE_STAND_MANIFEST` (15.5b); only with the device's `--screen` opt-in, unless the Cell's
  capabilities say `hive_owned` (15.5a), since the Hive owns that machine; and every screen mode always Smokes, so `--alongside`
  is refused with any screen mode); the route takes the same `ActionKind.INSPECT` fresh step-up
  as 15.6 and gains its own route-test allow-list row; a view-only session drops input frames at
  the relay. *Exit:* a Sting Cut or a reduction closes both ends; a view-only device's input never
  reaches the device; `screen_view` on a `FULL` Cell succeeds; `screen_control` is refused on a
  `READ_ONLY` and on a `SCRATCH` Cell; a shell-only opt-in refuses the screen at the door; a
  Night Veil Cell refuses the screen; a device without a fresh step-up gets `step_up_required` on
  `/inspect/screen`, and one without the scope gets 403; an attach with a wrong or reused token is
  refused; a screen on the Hive Stand is refused, and so is one on a `PACKET` Cell co-located
  with it or reporting no, an empty or an unreadable machine id, from anywhere;
  `--alongside` with `screen_view` is refused, and a screen Inspection on a Cell with a running
  task pauses it; a screen socket with a bad signature, a wrong or reused token, or another
  Inspection's id is closed before any byte is relayed. *Size:* medium, 3 agent-days.
- [ ] **15.12 On-device notice and local end for the screen.** An always-on-top notice on the
  device's screen ("the HiveMind operator is controlling this device") with an "end now" button,
  plus a tray or menu bar icon, on Windows, X11 and macOS; Wayland and macOS also show their own
  system indicators. The screen executor refuses to start until the notice is up. *Exit:* with the test
  client, the notice is up for the whole of every screen Inspection, and "end now" closes the
  client's connection within five seconds. *Size:* medium, 3 agent-days.
- [ ] **15.13a Linux X11 Real Cells.** x11vnc on the real display of an opted-in Linux Real Cell,
  run with `-inetd` on the packet's pipe and a one-time password from an owner-only file.
  *Exit:* control an Ubuntu laptop's desktop (notice up) from the test client; no listening port
  appears on the device. *Size:* medium, 2 agent-days.
- [ ] **15.13b Headless Virtual Cell screens.** The Hive Stand attaches from the host side and
  opens no TCP port: on Docker, a `docker exec` whose stdio carries RFB to x11vnc on the
  Exoskeleton's Xvfb and openbox inside the container (`-inetd`, never published); on QEMU, its own `-vnc` on a Unix socket (`AF_UNIX` on Windows too, since `-vnc` takes no named
  pipe) inside the same owner-only directory as 15.5a's console, for the VM console. Each session has a one-time VNC password. No VNC port is ever published on the host's
  `127.0.0.1`, because every QEMU and Docker Desktop guest can reach host loopback and no current
  backend prevents it. The Night Veil refusal applies to the screen as to the shell. *Exit:* control a headless Docker Cell and a QEMU Cell's
  console from the test client; no new listening TCP port appears on the host during either; a
  wrong or reused VNC password is refused; a Night Veil Cell refuses a screen; a Warden or
  autopilot call to the x11vnc `docker exec` or the QEMU `-vnc` attach without a live warrant is
  refused, and their output reaches no hub listener. *Size:* medium, 2.5 agent-days.
- [ ] **15.14a Windows capture and input.** Desktop Duplication capture and `SendInput` through the
  chosen path (a bundled VNC server run as a separate process, or the packet's own), in the
  interactive user session only; multi-monitor and DPI. *Exit:* with the test client,
  control a Windows 11 laptop's desktop with the notice up, across two monitors at mixed scaling. *Size:* large, 3 agent-days.
- [ ] **15.14b Windows elevation edges.** What a user-level process cannot reach is reported, never
  failed silently: the UAC secure desktop and the lock screen show black with a "secure desktop"
  banner and take no input; input to an elevated window is dropped by UIPI (User Interface
  Privilege Isolation), so the packet detects a higher-integrity foreground window and tells the
  pane "this window is elevated; input is blocked". Ctrl+Alt+Del is not offered on Windows, since
  `SendInput` cannot synthesise it and `SendSAS` needs a SYSTEM service; all three wait for a later,
  separately opted-in SYSTEM helper. *Exit:* a UAC prompt and an elevated window each show their
  notice in the test client's status line. *Size:* medium, 2 agent-days.
- [ ] **15.15a Wayland on GNOME and KDE.** The RemoteDesktop and ScreenCast portals over PipeWire.
  The device's user approves in a system dialog at least once; later sessions skip it only if that
  user ticks the portal's "remember" choice, in which case the portal's restore token
  (`persist_mode`) is obtained only by `pollen allow inspect --screen --remember`, run by the
  human on the device under the same elevation and console checks as 15.3a, and kept in the same
  store the packet's account cannot write, never on the Hive Stand; it is revoked with
  `pollen allow inspect --revoke`. A token written anywhere else is ignored. *Exit:* with the test client, control an Ubuntu GNOME Wayland
  desktop after the local approval, and again without it when it was remembered; a restore token
  planted by the packet's account or through `session.exec` is ignored. *Size:* medium to large,
  3.5 agent-days.
- [ ] **15.15b Wayland on wlroots.** wayvnc (ISC) on an owner-only Unix socket for Sway and
  other wlroots compositors. *Exit:* control a Sway desktop on Arch from the test client. *Size:* small, 1.5 agent-days.
- [ ] **15.16 macOS (best effort).** A bundled server or the packet's own capture (ScreenCaptureKit
  and CGEvent), attached over stdio or a socketpair (loopback TCP only under 15.11a's fallback
  rules), after the device owner grants Screen Recording and Accessibility
  to the packet in System Settings. The built-in Screen Sharing is not used: it listens on port 5900
  on every interface, cannot be bound to loopback, and is not governed by the grants the packet
  holds. macOS 15 asks again for Screen Recording from time to time, so `pollen status` reports a
  lapsed grant as an expected state ("re-approve on the device"), not a fault. *Exit:* with the test
  client, control a Mac where both grants are given, and a lapsed grant is reported, not failed. *Size:* medium, 2 to 3
  agent-days.
- [ ] **15.17 Screen pane and desktop client.** noVNC (MPL-2.0) in the Cell page with a view-only and
  a control mode, keyboard capture, the Windows key and Ctrl+Alt+Del sent as buttons (Ctrl+Alt+Del
  not on Windows targets, per 15.14b), touch on the phone; `hive inspect <cell> --screen` opens a
  loopback port bridged to the Entrance for any VNC viewer, or opens the browser pane. The port
  accepts one connection only, then closes its listener, and requires a one-time VNC password
  shown to the operator in the terminal; a second or unauthenticated connection is refused. On a
  machine that runs Virtual Cell backends (the Hive Stand above all), host loopback is reachable
  from guests and no current backend prevents it, so a guest could take the one connection: there
  the bridge is a Unix socket or named pipe for viewers that take one, or the browser pane only,
  and never a loopback TCP port. *Exit:* the 15.13a and 15.13b runs from the Android app, and
  from TigerVNC on a laptop; on the Hive Stand, `hive inspect --screen` opens no TCP port. *Size:* medium, 3
  agent-days.
- [ ] **15.18 Clipboard and file transfer (optional extras).** Text clipboard both ways through RFB,
  off by default, capped in size, counted on the trail, `C2`; files through new `inspect.put_file`
  and `inspect.get_file` frames on the Inspection's own channel, built only by the door and
  warrant-checked at `FrameSender.send` like every `inspect.*` frame, added to the `inspect.*`
  family on a new protocol minor with their section in `spec.md` and the gateway contract. What
  the operator fetches never enters the Basket or Honey: it streams straight to the operator's
  client, or is staged in 15.9's operator-only area and deleted once downloaded; its responses,
  like every device-to-Hive `inspect.*` frame, reach only the door's relay. Cells with
  `host_side_attach` move files on the host side instead: `docker cp` for Docker, the QEMU guest
  agent's file copy for QEMU, and local file operations for the Hive Stand's own PTY. Each of
  those entry points sits behind 15.4a's wall: it takes a live `InspectionWarrant` checked
  against the door's table, the lint contract keeps it out of Wardens, autopilot, `queen/awake`
  and `queen/guard_requests`, and what it fetches goes only to the door's relay and the
  operator-only area. Never
  `session.put_file`/`get_file` (refused on an operator lease and, under Smoke,
  on every other lease) and never a VNC file-transfer extension. *Exit:* both work when enabled
  and are absent when not; a `session.put_file` on the operator lease is still refused; an
  `inspect.put_file` without a warrant is dropped; a fetched file is in no Basket, Honey or
  hub listener, and no bee can read the download area; the download route (15.9's rules, its own
  route-test row) gives the UI's default credentials 403; on a Docker Cell, a QEMU Cell and the Hive
  Stand, a transfer works for the operator, a Warden or autopilot call to `docker cp`, the guest
  agent copy or the local file path without a live warrant is refused, and what it fetched
  reaches no hub listener. *Size:* medium, 3.5 agent-days.
- [ ] **15.19 Screen recording (optional).** The screen recorded with 6.6's recorder but stored in
  15.9's operator-only area, never as flight-recording Nectar; retention window, announced in the
  pane; refused on Night Veil. *Exit:* a recorded session replays; an unrecorded one leaves no
  bytes anywhere; a Night Veil Cell refuses to record; no bee can read a recording, and none
  appears in the Basket, Nectar or Honey; the screen-recording read route (15.9's rules,
  `screen_view`, its own route-test row) gives the UI's default credentials 403. *Size:* small, 1.5
  agent-days.

Track B total: about 36 agent-days, plus about 3 more if the spike chooses own capture over RFB.

Steps are sized for one PR each. 15.3a stays whole because the executor must never ship without
its opt-in and lease walls, 15.4b because the kill, the refusals and the trail events only make
sense landed together (its Hive Stand containment may land first as a preparatory PR), and 15.14a because capture and input are proven together.

### Exit criteria

- From a laptop over the VPN overlay and from the Android app, the operator opens a terminal on a
  Windows Pollen device, an Ubuntu Nuc, a Docker Virtual Cell and a QEMU Virtual Cell; a task
  running on the Nuc is paused when the Inspection opens and resumes from its Handoff when it
  closes, with no work redone. Each Real Cell shows a notification for each terminal Inspection.
- The same operator sees and drives the desktop of the Windows device and of a headless
  `desktop-ubuntu` Virtual Cell from the browser and from a desktop VNC viewer, and the Windows
  device shows the notice the whole time.
- A device that never ran `pollen allow inspect` refuses both tracks, and one that opted in with
  `--shell` only refuses the screen; a `SCRATCH` Cell refuses a shell; `READ_ONLY` and `SCRATCH`
  Cells refuse `screen_control` while a `FULL` Cell grants `screen_view`; a Night Veil Cell refuses the screen and recording; a device without `entrance:inspect` or
  without a fresh step-up is refused before any lease opens; with `hive_stand_remote = false`, a
  remote open of a shell on the Hive Stand or on a Cell sharing its host is refused before any
  lease opens, including a step-up and open made from inside a QEMU or Docker Desktop guest
  through the loopback listener; every open is pushed to every other enrolled device.
- A Sting Cut, Absconding, the Entrance Reducer, the idle timeout, `pollen inspect end` and the
  notice's "end now" each end an open Inspection within five seconds, and the trail has exactly one
  `cell.inspection_closed` with the right reason and no content.
- ADR-0068's guest-loopback fix has landed, and 15.5a's forged-`Host` check gets 403 from inside a
  QEMU and a Docker Desktop Cell.

### ADRs to write

- `inspection-is-a-human-operator-lease.md` (the operator lease and its left-as-found exemption,
  carried into codingrules 15 and CLAUDE.md; the device-side opt-in as human-on-device only,
  behind OS elevation in a store the packet cannot write, needing a standard account or "Always
  notify" on Windows, and reported by the heartbeat field outside 13.4a's dire pattern; access
  level, tier and Hive Stand limits; the amendment to codingrules 8.11 that the Observation Hive
  may carry an Inspection for a device the operator widened, opened through the Queen's door
  like a Sting Cut; the device's own default shell allowed by gateway-contract `:633-634` inside
  an operator lease only; the Capping exemption for a human's keystrokes; if `hive_stand_remote`
  is kept, the plain statement that it ends loopback-only approval in practice; remoteness from
  the `StepUpProof`, `LOOPBACK` only when the step-up arrived on the loopback listener and carried
  the stand key, and the stand key also required on the open `POST` (15.6), checked per Cell by
  the door; the Hive Stand rule applied to any `PACKET` Cell the Queen derives as
  `shares_hive_stand_host` from the device's machine id (a packet on the Hive
  Stand, or the new Hive Stand after a Supersedure), recomputed when the Hive Stand moves, since
  a same-account shell can read the serve record despite ADR-0068's per-boot stand key, failing
  closed on a missing or unreadable machine id, and a packet under WSL judged by its Windows
  host's `MachineGuid`, failing closed if that cannot be read, the Hive Stand judging its own id
  the same way, and any `virtualized` packet treated as sharing the host until the operator runs
  the loopback-only, stand-key `hive swarm mark-separate` for it, a mark that lifts only that
  fail-closed, records the Hive Stand id it was made against and is void when that id or the
  device's machine id changes; a virtualized Hive Stand treating every `PACKET` Cell as sharing
  its host until marked; reads of a Hive Stand
  Inspection's transcripts, recordings and downloads needing the stand key and a `LOOPBACK`
  proof; four capability fields held Hive-side and never reported, and
  `bee_account_privileged` on the heartbeat as availability, not a 13.4a report; on close only
  the tasks still marked `smoked_by` that Inspection resuming, each mark cleared in the same
  transaction as its resume or by a close that declines to resume; every Inspection's durable row
  written by the door at open into the Brood Chamber's `inspections` table (before the operator
  lease, `--alongside` and idle ones included) and Smoke adding the Smoked ids and the
  `smoked_by` marks before the first Handoff request, so Requeening resumes every task still
  marked by an `ENDED` Inspection; `INSPECTED` derived from those rows and re-sent whenever a
  Warden attaches; one durable gate, the row moving through `CLOSING` to `ENDED` only once the
  operator lease is confirmed ended (an `inspect.closed` matched against the durable rows, or the
  lease's release report), `inspect.close` idempotent, `CLOSING` bounded by `close_confirm_s` and
  a forced end of the operator lease alone, never a Sting Cut, an end inferred from the grace,
  the dead-man switch or the fallback marked `ENDED_UNCONFIRMED` and confirmed on the next
  reconnect before anything on the Cell is dispatched or resumed, or resolved by a loopback,
  stand-key `hive swarm revoke`, which is final, device timers that count
  suspended time, the gates scoped (`INSPECTED`, a second Inspection and the Supersedure preflight
  on any row not `ENDED`; dispatch, new leases and `CellSession` refusals only on a row that
  Smoked), and the operator shell started with history off, for both the `INSPECTED` lift and the Smoked-task resume, on
  every close and in Requeening, and none on an isolated Cell or from a tainted
  Handoff; loopback trusted only with the stand key, never by
  listener (a step-up is `LOOPBACK` only with it, and a Hive Stand or co-located shell open is a
  stand-key row), because the stand key guards only the loopback-only rows and a guest reaches
  every other row on the loopback listener; with that, remote shell Inspection of a Virtual Cell
  needs no per-backend refusal, while VNC servers and the screen bridge never listen on host loopback TCP
  where Virtual Cell backends run; the operator lease closed to every
  `cell.request` and `session.*` request; `inspect.open` built only by the Queen's door from an
  Entrance-built `StepUpProof`, every `inspect.*` frame checked against the door's live table at
  `FrameSender.send`, every host-side attach entry point (Docker tty exec, QEMU console and
  `-vnc`, the Hive Stand PTY, the x11vnc `docker exec`) taking a live warrant against the same
  table and sending output only to the door's relay, every `CellSession` kind refusing an
  operator-kind lease without the door's warrant, and `inspect.close` as the system-close path that never needs a proof;
  Inspection file transfer over door-built `inspect.put_file`/`get_file` frames, or on a
  `host_side_attach` Cell through warrant-checked `docker cp`, guest-agent copy or local file
  paths, the one exception to doing file work through `session.*`; recordings and downloads only
  in the guard-floor-denied operator-only area; a Requeening Queen system-closing every
  Inspection row with no close, lease or not, lifting `INSPECTED` on the Cell's Wardens and
  tearing down host-side state before resuming Smoked tasks, and a Supersedure refused while any
  Inspection is open, with no Inspection opened during one; the two layers, with `--alongside` getting
  only layer 1 and refused where `hive_owned`; Smoke as one mechanism on every backend, killing
  every non-operator lease's processes within `handoff_patience_s` while the lease stays open,
  chosen over freezing processes or a second account on a borrowed device, with un-handed-off
  sub-bees retired and every `CellSession` kind refusing work on non-operator leases; the kill by
  containment (a Job Object without breakaway, a cgroup v2 scope, else the process group), never
  by account, extended to the Hive Stand's own leases; the derived `shares_hive_stand_host` and
  the five capability fields (`hive_owned`, `operator_account_isolation`,
  `host_side_attach`, `bee_account_privileged`, `opt_in_source`) that replace any branch on a
  Cell's kind, and the loopback-only `hive_stand_shell` key. Disclosed and accepted residuals: persistence
  planted earlier runs outside any lease and survives Smoke (cron, `systemd --user` units,
  LaunchAgents, Scheduled Tasks, `PATH` shims, and root-in-container tampering on a Docker Cell),
  though the clean shell does not load it; `--alongside` leaves bees running beside the operator;
  a bee retired at `handoff_patience_s` redoes the work since its last checkpoint; on Linux
  without cgroup delegation (the Hive Stand included) and on macOS, a descendant that left its
  process group (`setsid`, a double fork, a tmux or screen server) may survive the kill, as may
  one that escaped a Virtual Cell session's tracking; containment stops ordinary and accidental
  descendants only, and a deliberate, live escape by the same account (a self-move within a
  delegated cgroup subtree or `systemd-run --user`; WMI `Win32_Process.Create`, a COM server or
  a run-now Scheduled Task; `launchctl submit`) survives Smoke on every platform; a Nuc's model
  servers may be down for the Inspection; on a Docker Cell, without `hidepid`, the in-Cell
  Warden process can see the operator's command lines; on a Virtual Cell screen Inspection, the
  in-Cell Warden process can talk X to the Exoskeleton's display; where `bee_account_privileged`
  is true and the Cell is idle, the in-Cell Warden can read the operator's terminal. Operator
  guidance: on a device or Cell that has hosted `FULL` leases, treat the session as observable
  and never type an elevation secret there; the operator shell keeps no history, but a command
  typed there can still be seen while it runs; never `mark-separate` a VM or container that runs on
  the Hive Stand).
- `inspection-channel-on-waggle.md` (the keyed byte channel inside a signed open; sequence numbers
  instead of the replay window; never queued; the amendments to gateway-contract section 6, making
  the `inspect.*` open kinds for shell and screen Queen-only under rule 3 so a Warden's signature
  cannot open one, and section 16).
- `remote-desktop-transport.md` (RFB from servers attached over stdio, `-inetd`, a socketpair or an
  owner-only Unix socket, with loopback TCP only as a single-connection fallback whose password
  never appears in argv or the environment, and an outbound data socket, against
  own capture, against WebRTC; the spike's numbers; the loopback-listener exception to "no inbound
  socket" (gateway-contract `:629`) and the operator-lease exception to "capture the screen or the
  input" (`:641`); the screen socket's second connection to the Queen's persisted URL at its own
  path, signed hello and one-time token (amending sections 5 and 16, `:149-152` and `:642`), and
  its exemption from the frame ceiling (section 7, `:263-266`); licensing of bundled servers).
- `inspection-audit-and-recording.md` (trail events and counts; optional recordings kept only in
  the operator-only area, never the Basket, Nectar or Honey, and read only through routes that
  need the matching `entrance:inspect` scope, a fresh `ActionKind.INSPECT` step-up and, for an
  Inspection opened under the Hive Stand rule, the stand key and a `LOOPBACK` proof, never with the UI's default credentials; the Night
  Veil host-side attach, and the two `cell.inspection_*` events this adds to the lifecycle
  skeleton in codingrules section 12).

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
9. Dashboard stack (phase 12): resolved. TypeScript + React + Vite, one stream-fed store with
   per-stream reducers and no state library (ADR-0049); Capacitor for Android with UnifiedPush
   delivery over the existing Web Push channel, not FCM (ADR-0050).
10. Packaging for `pollen` (phase 11.7).
11. License (phase 14).
12. VPN overlay for `expose = "vpn"`: WireGuard or Tailscale; and the tunnel client for
   `expose = "tunnel"` (phase 10.5a).
13. WebAuthn server library for passkeys, and web push delivery (phase 10.5e, 10.5b).
14. Default Whisper model size for `TRANSCRIBER`, and whether `faster-whisper` is a required or
   optional extra (phase 6.5a; the eval harness in 8.5 settles the size).
15. Speech synthesis for spoken replies, and whatever a glasses vendor's SDK allows beyond the
   share sheet (post-1.0).
16. How the Observation Hive proposes a Honey note or Cell Wax for the House Bee (12.9): today
   `hive honey propose` writes the queue row from the CLI process and nothing on the Landing
   Board carries a proposal, so the browser shows no propose control. The options are a
   structured intent the Queen parses out of a chat `HumanMessage`, a typed message kind in
   Waggle handled by the Queen, or a `POST /v1/honey/notes` row through the Queen's door added
   to codingrules 8.11's route test. Needs an ADR before the control exists.
17. Virtual Cells reach the Hive Stand's loopback (ADR-0068, follow-up): QEMU's user networking
   maps `10.0.2.2` to host `127.0.0.1` under every policy but `none`, and Docker Desktop proxies
   `host.docker.internal` to host loopback, so a guest reaches Ollama, LM Studio, dev servers and
   the Entrance's loopback listener (whose loopback-only routes the stand key now guards). The
   network fix to decide and build: for QEMU, the control-NIC split (`restrict=on` with one
   `guestfwd` per host service a Cell needs, the Waggle link and each loopback model server, plus
   an egress NIC for NAT; see `hive/backends/qemu/network.py`); for Docker Desktop, the control
   subnet everywhere with no `host.docker.internal` alias, or refusing untrusted work there. Needs a
   real QEMU and Docker Desktop host to prove.
18. Inspection (phase 15): whether the Observation Hive may carry a terminal and a screen for a
   device the operator widened (the 8.11 amendment); Smoke by default or bees alongside; whether a
   shell on the Hive Stand itself, or on a Cell sharing its host (derived from its machine id), is
   ever reachable remotely (its screen is out of scope; default no; yes would let a
   remote session reach the loopback listener, so a stolen phone plus the password and a step-up
   could enrol a new device; Justin's call); whether a virtualized packet should keep failing
   closed as co-located until `hive swarm mark-separate` (the plan's default), and every `PACKET`
   Cell likewise while the Hive Stand is itself virtualized, with each mark void after a
   Supersedure or a machine-id change; WSL being judged by its host's `MachineGuid` for packets
   and the Hive Stand alike; a Night Veil terminal
   (attached from the host side) or none at all; whether recordings are offered at all.
19. Remote desktop transport (phase 15.10): RFB through bundled loopback VNC servers and noVNC,
   own capture in the packet, or WebRTC; which VNC server per OS and its licence (x11vnc,
   TigerVNC, TightVNC and UltraVNC are GPL-2.0, run as separate processes, never linked; wayvnc is
   ISC; noVNC is MPL-2.0; xterm.js and pywinpty are MIT); RustDesk (AGPL, its own rendezvous and relay servers and its own
   login) is rejected because it would be a second door beside the Entrance.

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
| Workers pollute Real Cells. | Leases with scratch roots and process tracking, capability gating outside scratch, left-as-found tests, Undertaker release, Requeening sweep. What is meant to stay is declared by the plan, decided by policy or the human, and ledgered so it can always be listed and removed (5.0a to 5.0e). |
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
| A prompt injection through Honey, hot state or a tool result steers a bee. | Untrusted content labelled and delimited in every prompt (3.9, 7.8); the scanner and its trail event (10.6b); tool calls validated against schema and capabilities whichever rung produced them (3.16); Capping on every side effect outside scratch (3.17); the Guard Bee's injection-then-denial rule (10.6); Queen-only isolation and tainted memory (10.6a); the chaos seeds (13.6). |
| A compromised Swarm device lies in its reports or poisons its session output. | Pollen has no brain and holds no capabilities, so its key speaks only as that node (11.3); signed envelopes and replay rejection (1.7, 11.3); session output is untrusted content to the scanner (10.6b); a capability report changed without re-enrolment is a dire pattern (13.4a); trail segments verified on merge (11.9); the dead-man switch (11.4); isolation and Sting Cut on the Queen's decision only (10.6a, 13.4a). |
| A bee on the Hive Stand reads or rewrites the Hive's own state (its database, secret store or manifest). | Bees run as the Hive's own OS user today. The console key is wrapped under the operator password, so reading the secret store yields nothing usable (ADR-0041); a guard floor denies every bee `fs:*` on the state paths, `exec` of the Hive's entry points and `net` to loopback (10.3); arbitrary commands stay Capping-gated; work that must not be trusted with the Hive Stand runs with `isolation = "required"`. A separate OS user for Hive Stand bees is the lasting fix, not yet scheduled. |
| A Virtual Cell's guest reaches services on the Hive Stand's loopback (QEMU's SLIRP host alias, Docker Desktop's `host.docker.internal`). | The Entrance's loopback-only routes, the console's login and step-up, and any login or step-up while reduced need a per-boot stand key no Cell holds, a reduction ends every keyless session and socket, and the travel lock judges a keyless loopback login as unplaceable (ADR-0068); residual: while the Entrance is open, a guest holding an ordinary device's key and the password logs in on loopback without the overlay or mutual TLS, with that device's rights (and a step-up plus a notice when the travel lock is on); the guard floor refuses a bee's `net` to the host-gateway names; `docs/guard/isolation.md` says plainly that other loopback services are exposed. The network fix is open decision 17. |
| Inspection turns a stolen phone or a compromised Hive into a remote shell and screen on every device. | Device-side opt-in typed on the device that the Hive cannot grant itself; `entrance:inspect` widened only on loopback and never held by a bee; an interactive device and a fresh step-up for every open; a notification for every Inspection, the screen notice, and `pollen inspect end` or "end now" on the device; idle and hard timeouts; Sting Cut, Absconding, the Entrance Reducer and revocation end it; every open pushed to every other device (phase 15). |
| A stolen client key or an abusive enrolled program. | Sessions bound to the device key and step-up for anything sensitive (10.5e); narrow capability sets and spend caps per device (10.5d); lockout on capability-denial bursts, undone only on loopback (10.5e); revocation on loopback; every Entrance event pushed to every other device (10.5b). |
