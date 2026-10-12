# Phase 9 (Royal Jelly Lab, Comb Registry, Basket): design, map, how to run it, open items

> Written 2026-10-12 on branch `ccr-4e10bf6f-y9zk5g` (from `main` at `4b66406`) before any phase 9
> code, as the design every implementer works from. Section 1 holds the decisions, section 2 the
> module map and the interfaces between steps, section 3 the wire and trail additions, section 4
> the build order. Sections 5 onward are filled in as the work lands and become the handoff.
>
> **Numbering note.** Phase 8 is on the unmerged branch `ccr-0235b6d7-okyhmn`; it took ADR-0044
> and ADR-0045 and Waggle minor **1.11**. Phase 9 therefore takes ADR-0046 to 0048 and Waggle
> minor **1.12**, and leaves 1.11 to phase 8. If phase 8 never merges, renumber both before
> merging this branch (the spec's version history and `waggle.envelope.PROTOCOL_MINOR`).

Read first: `CLAUDE.md`, `.claude/codingrules.md` (sections 5, 6, 7, 8.1 to 8.5, 8.7, 8.8, 8.12,
14, 15), `.claude/roadmap.md` phase 9 (lines 1415 to 1517), `README.md` core concept 10 and the
vocabulary rows for Royal Jelly Lab, Comb Registry, Basket and Quarantine Comb.

## 1. Decisions (the three ADRs, in brief)

### 1.1 ADR-0046 `tool-spec-comb-registry-and-scopes.md`

- **A tool is a package of files named by its sha256.** `ToolPackage` is a deterministic archive
  (sorted paths, fixed timestamps, no absolute paths, no `..`, bounded total size) of
  `tool.py` (the entry point module), `test_tool.py` (its `unittest` tests) and `spec.json` (the
  `ToolSpec`). The archive bytes live in the Basket as a blob; the registry stores the hash, never
  the bytes. One hash names one immutable version everywhere: the registry row, `ToolPromoted.
  package_sha256`, the resident copy a Warden loads.
- **`ToolSpec`** (`royal_jelly/spec/models.py`): `name` (the wire pattern), `version` (semver
  text), `description` (model-facing), `input_schema_json` (JSON Schema text; the top level must be
  an object with `additionalProperties: false`, checked by `validate`), `required_capabilities`
  (capability strings), `entry_point` (`tool:run`, i.e. module `tool`, attribute `run`),
  `dependencies` (PyPI names the tool imports beyond the standard library; the Comb's sandbox has
  no network, so in this phase a non-empty list fails the static gate with the reason "the sandbox
  cannot install dependencies"), `supported_os` (OsFamily tuple), `scope` (`ToolScope`),
  `cell_id` (required exactly when scope is CELL), `tool_id`, `requested_by` (Warden id). A
  generated tool's `run` is `async def run(session: CellSession, arguments: dict) -> JSON value`:
  it acts only through the session it is handed.
- **`validate_spec(spec, requester: CapabilitySet) -> SpecValidation`** is pure and rejects: a
  capability outside the requester's own set (`CapabilitySet.allows`), a schema that is not an
  object or lacks `additionalProperties: false`, a bad entry point, a CELL scope without a Cell, a
  `tool:` capability in `required_capabilities` (a tool never grants itself other tools).
- **Registry** (`royal_jelly/registry/`): SQLite, in the Hive's one database, table `comb_tools`
  (tool_id, name, version, scope, cell_id, spec_json, package_sha256, package_size, report_json,
  quarantine_path, promoted_by, promoted_at, state, supersedes_version, retired_at, reason).
  `CombRegistry` protocol: `list_tools(scope=None, cell_id=None)`, `get(name, scope, cell_id,
  version=None)` (None means current), `visible_tools(cell_id)` (HIVE current rows plus CELL
  current rows for that Cell), `promote(spec, report, package, principal, reason)`, `retire(name,
  scope, cell_id, version, principal, reason)`, `rollback(name, scope, cell_id, to_version,
  principal, reason)`. **`promote` is the only writer that creates a PROMOTED row and it refuses**:
  a report that did not pass, a report whose `spec_sha256` or `package_sha256` differ from the
  spec and package offered, HIVE scope from anything but `QueenPrincipal`, CELL scope from anything
  but `WardenPrincipal` whose `cell_id` equals the spec's, a version that already exists for that
  (name, scope, cell), and a REAL_CELL-path report at HIVE scope without `reviewed=True` on the
  promotion (the Queen's awake review, 9.3a). `tests/unit/royal_jelly/registry/test_no_bypass.py`
  proves each refusal and that the store exposes no other way to a PROMOTED row.
- **Principals**: `QueenPrincipal()` and `WardenPrincipal(warden_id, cell_id)` in
  `royal_jelly/registry/principals.py`. An in-process Warden builds its own principal from its
  own Cell; for a remote Warden the Queen builds it from `WardenLink.cell`, never from the
  message.
- **Versions are immutable.** `retire` marks a row RETIRED (never deletes); `rollback` makes an
  earlier PROMOTED row current again, recorded as a `tool.promoted` event with
  `supersedes_version`; the row's own passing report is its evidence, so no new quarantine.
- **State** (`royal_jelly/tool_state.py`): `REQUESTED → SCAFFOLDED → QUARANTINING → PROMOTED /
  REJECTED; PROMOTED → RETIRED`, Appendix C's row, with one `TRANSITIONS` table.

### 1.2 ADR-0047 `quarantine-comb-gates-and-sandbox.md`

- **The Comb runs where its sandbox is, driven by that Cell's Warden.** No Warden answers
  `session.*` messages before phase 11, so the Queen cannot drive a sandbox Virtual Cell's session
  from her process. Instead she ships the package to the sandbox Cell as a Basket blob, sends its
  Warden a `tool.quarantine` request naming the blob, and the Warden runs `QuarantineComb` over
  its own `CellSession` and replies `tool.quarantine_report`. A Warden that wants a sandbox for
  a cell-scope tool sends the same `tool.quarantine` to the Queen: that message **is** its
  sandbox request. The Queen provisions the sandbox, runs the Comb there on the requester's
  behalf and relays the report back. `CellRequest` is not used, because a sandbox is never handed
  to the requester: it stays the Queen's and is torn down after the report, which keeps "Wardens
  never provision" and keeps a sandbox out of every grant.
- **`Sandbox`** protocol (`quarantine_comb/sandbox.py`): `root` (a Path inside the sandbox),
  `put(relative_path, bytes)`, `exec(argv, timeout_s) -> CompletedCommand`, `read(relative_path)`,
  `cleanup()`. `SandboxCell` (`sandbox_cell.py`) wraps a `Cell` and its `CellSession`; it refuses
  a borrowed Cell (`cell.is_borrowed`; never `cell.kind`) and runs under a fresh directory below
  the session's scratch. `SandboxSubprocess` (`sandbox_subprocess.py`, dev only, the one allowed
  `subprocess` import in `royal_jelly`): a temp dir, a scrubbed environment, `sys.executable`;
  selected only by `[royal_jelly] sandbox = "subprocess"`, which logs a warning at build.
- **Gates, in order** (`quarantine_comb/gates/`): `static` (pure, AST: banned imports and names,
  dependencies declared, entry point present, tests present, spec round-trips), `tests` (the
  runner executes `test_tool.py` with `unittest`), `schema` (the schema compiles; a sample input
  validates; an input with an extra property is refused), `fuzz` (N schema-derived inputs; the
  tool must return a JSON value or raise cleanly, never hang), `resources` (CPU seconds, peak
  RSS and wall time under `QuarantineLimits`), `capabilities` (every session call the runner
  recorded maps to a capability the spec declares). The first failing gate ends the run; the
  report names it and why.
- **The runner** (`quarantine_comb/runner.py`) is executed inside the sandbox as
  `python -m hivemind.royal_jelly.quarantine_comb.runner <package_dir> <report_path> <limits_json>`.
  It applies `resource.setrlimit` where available, installs a `sys.addaudithook` that records
  `socket.*`, `subprocess.Popen`, `os.system`, `os.exec*`/`fork` and `open` outside the package
  dir, the scratch dir and the interpreter prefix, **attributed to the tool only when the innermost
  non-standard-library frame is inside the package dir** (so a tool that calls `session.exec`,
  whose own implementation spawns a process from `hivemind/cell`, is clean, and a tool that calls
  `subprocess.Popen` itself is not). It hands the tool a recording `CellSession` built over the
  sandbox's own scratch (`InCellSession` when inside a Cell; the same class over a temp lease in
  the subprocess sandbox), runs tests, schema, fuzz, and writes one JSON report the Comb reads.
- **`QuarantineReport`** (`royal_jelly/spec/report.py`): tool_id, name, version, spec_sha256,
  package_sha256, path (`QuarantinePath`), sandbox_cell_id, started_at, finished_at, gates
  (tuple of `GateResult(name, passed, reason, detail)`), passed, failing_gate, usage
  (`ResourceUsage`), `reviewed` (False until the Queen's awake review accepts a REAL_CELL-path
  report for HIVE scope).
- **Non-Ubuntu targets (9.3a)**: `quarantine_comb/path.py` decides `SANDBOX_CELL` when the target
  platform is LINUX with the sandbox image's distribution (`[royal_jelly] sandbox_distribution`,
  default `ubuntu`), else `REAL_CELL`. On the REAL_CELL path the static, schema and fuzz gates run
  in the Ubuntu sandbox and the tests gate runs through a `SandboxLease` over a lease's scratch on
  a Real Cell of the target platform, which needs the holder to carry `tool:quarantine_on_real`
  (a new flag capability family), runs with the flight recorder on where one is attached and at
  the `device_command` tier. This phase builds the decision, the capability, the report field and
  the registry rule; the Real Cell leg is exercised in a unit test with a fake session, not end
  to end (no second-platform Real Cell exists in CI).

### 1.3 ADR-0048 `basket-inert-blobs-residency-and-transfer.md`

- **Layer.** `hivemind/basket/` is a Layer 2 package beside `honey_store`, `supervision`,
  `brood_chamber` and `memory` (add it to the codingrules layer table, the layout tree and the
  import-linter layers contract in the same rank). It imports `guard` (scanner), `cell`
  (`HoneyClearance`, `CombShieldLevel`, `OsFamily` via waggle), `common`, `pheromone` and waggle.
  It never imports `honey_store`: the Honey record every deposit writes goes through a port.
  `memory` and `llm` never import `basket` (a forbidden contract), so `memory.assemble` cannot
  list it.
- **Models** (`basket/models.py`): `Blob` (sha256, size_bytes, media_kind: a descriptive tag such
  as `wheel`, `archive`, `dataset`, `tool_package`, `text`; media_type; platform_tags: tuple of
  OsFamily values; clearance; origin_cell, origin_task, origin_bee; deposited_at;
  deposit_event_id; tainted; state PRESENT / RETIRED), `BlobManifest` (the `Blob` as canonical
  JSON plus `signer` node id and an Ed25519 signature over those bytes, made with the Hive key
  at deposit; `verify(verifier)`), `DepositRequest` (bytes, title, summary, media_kind,
  media_type, platform_tags, declared clearance, provenance, tainted), `HoneyRecord` (title,
  summary, the hash, kind, platform tags, clearance, origin: what the port receives).
- **Clearance and taint at deposit** (`basket/clearance.py`, pure): the label is
  `max(declared, floor)` where the floor is C2 for a blob from a borrowed (Real) Cell and C0
  otherwise, the same rule `honey_store.clearance.intake_floor` applies to Nectar (restated here
  because Layer 2 siblings do not import each other; its test cites the other's). A blob whose
  provenance names an isolated or quarantined source arrives with `tainted=True` from the
  depositing Warden's taint ledger and is never shipped or loaded.
- **Store** (`basket/store/`): `BasketStore` protocol and `SqliteBasketStore`: metadata in the
  Hive database (`basket_blobs`, `basket_residency`, `basket_shipments`), bytes in a directory
  `<root>/<sha256[:2]>/<sha256>` written to a temp name and renamed. `deposit` dedupes by hash
  (a repeat returns the existing `Blob` and records `basket.deposit_deduplicated`); `get`,
  `open_bytes`, `list_blobs(filter)`, `retire`. Text-like blobs (`media_type` starting `text/`
  or media_kind `text`/`tool_package`) pass `guard.scanner` at deposit and a flagged one is
  refused with `basket.deposit_refused` plus `guard.injection_suspected`; a binary cannot be
  scanned and is deposited as is, since a blob is inert.
- **A blob is inert** (the tested rule): holding a blob grants nothing. `BasketStore` has no
  execute, install or load method. The only reader that turns bytes into code is
  `workers/tools/invoke.py`, which accepts a hash only through a `CombToolEntry` the Warden built
  from a registry row; `tests/unit/basket/test_inert.py` asserts the store's public surface and
  that `invoke` refuses a hash with no entry, recording `guard.denied` at `TOOL_INVOCATION`.
- **Residency** (`basket/residency.py`, pure, plus the `basket_residency` table): per Cell, the
  blobs kept resident under `Ceilings.resident_basket_disk_bytes` (already on the wire since 4.8).
  `decide_residency(resident, incoming, ceiling) -> ResidencyDecision(admit, evict)` evicts
  oldest first by `resident_since` until the incoming blob fits and refuses a blob larger than the
  ceiling alone. A Warden's resident set lives in its own `ResidentBasket` (a directory below the
  Cell's lease scratch for a Virtual Cell, the Hive Stand's store for the Hive Stand Warden) and
  survives Overwintering with the Cell's volume.
- **Transfer** (`basket/transfer/`): bytes travel as `basket.offer` → (`basket.ack` |
  `basket.refuse`) → `basket.chunk`* → `basket.ack(complete)`. Chunks are at most 256 KiB
  (`CHUNK_BYTES`, so a base64 frame fits `MAX_FRAME_BYTES`), ordered by offset, resumable: an ack
  carries `received_bytes` and a sender resumes from there. The receiver verifies the sha256 of
  the whole and the manifest signature before keeping a byte outside its temp file; a mismatch is
  `guard.basket_integrity_failed` and `basket.refuse(INTEGRITY)`. Every transfer goes through the
  Hive Stand (nodes connect out to the Queen), so a Warden-to-Warden shipment is two legs. The
  receiver refuses by rule before the first chunk: clearance above the Cell's ceiling (a Night Veil
  Cell takes nothing above C1, so nothing from a Real Cell), a tainted blob, no residency room, an
  unknown signer, an unsupported platform tag. `transfer/sender.py` and `transfer/receiver.py` are
  pure state machines over value models; `transfer/wire.py` maps them to and from the waggle
  messages.
- **Every deposit writes one Honey record** through `HoneyRecordPort.record(HoneyRecord)`, a
  required collaborator of `BasketDeposits`; the composition root plugs in an adapter that submits
  a `NectarSubmission` (kind TASK_OUTCOME-like text at the blob's clearance, `source_key`
  `basket:<sha256>`). That record is the only way a bee finds a blob.
- **Nothing enters by side effect.** A Worker proposes to keep an artefact with a `retain`
  proposal (`workers/tools/retain.py`: a `Proposal` with `ActionKind.COPY` naming the file by
  digest, checked by the Capping gate like any side effect); on `APPLIED` the Worker's
  `BasketChannel` sends the deposit up its mailbox to the Warden, which deposits (in process on the
  Hive Stand) or ships it to the Queen (from a Cell), and `basket.deposited` records it.

## 2. Module map and interfaces between steps

Paths are relative to `packages/hivemind/src/hivemind/` unless they say otherwise. Every module
starts with the codingrules 7.2 header; files under 300 LOC, functions under 50, at most ten
modules per directory (`scripts/check_fanout.py`).

| Step | Modules | Owner wave |
|---|---|---|
| wire, trail, docs | `packages/waggle/src/waggle/messages/basket/` (`offer.py`, `chunk.py`, `__init__.py`), `messages/tool/quarantine.py` (`ToolQuarantine`, `ToolQuarantineReport`, `ToolRetired`), `messages/catalogue.py`, `messages/registry.py`, `envelope.py` (1.12), `docs/waggle/spec.md` (8.8 additions, new 8.12 basket, version history, open points 34/35 resolved); `pheromone/events/families/resources.py` (`BasketEvent`, new `tool.*` kinds), `families/supervisors.py` (`guard.basket_integrity_failed`), `guard/policy/catalogue.py`; `guard/capabilities/families.py` (`TOOL_QUARANTINE_ON_REAL = "tool:quarantine_on_real"`, a flag family) and its scope table; `.claude/codingrules.md` (layer table, layout tree, section 3 rules), root `pyproject.toml` (import-linter: basket in the Layer 2 rank; `memory`/`llm` never import `basket`); `docs/adr/0046..0048` and `docs/adr/README.md` | 1A |
| 9.1, 9.2, 9.9 | `royal_jelly/spec/` (`models.py`, `validate.py`, `package.py`, `report.py`, `__init__.py`), `royal_jelly/tool_state.py`, `royal_jelly/errors.py`, `royal_jelly/registry/` (`protocol.py`, `principals.py`, `sqlite.py`, `rows.py`, `migrations/0001_create_comb_registry.sql`, `__init__.py`), `royal_jelly/__init__.py`, `royal_jelly/README.md`; `cli/stores.py` gains `open_comb_registry(db)` | 1B |
| 9.2a core | `basket/` (`__init__.py`, `README.md`, `models.py`, `clearance.py`, `errors.py`, `residency.py`, `deposits.py` (the deposit door with the scanner and the Honey port), `store/` (`protocol.py`, `sqlite.py`, `files.py`, `migrations/0001_create_basket.sql`, `__init__.py`), `transfer/` (`models.py`, `sender.py`, `receiver.py`, `__init__.py`); `cli/stores.py` gains `open_basket_store(db, root)` | 1C |
| 9.4, 9.5, 9.3a | `royal_jelly/quarantine_comb/` (`sandbox.py`, `sandbox_cell.py`, `sandbox_subprocess.py`, `runner.py` plus `runner_hooks.py`/`runner_session.py` if the runner needs splitting, `gates/` (`base.py`, `static.py`, `banned.py`, `tests.py`, `schema.py`, `fuzz.py`, `resources.py`, `capabilities.py`, `__init__.py`), `comb.py` (`QuarantineComb.run(package, spec, sandbox, limits) -> QuarantineReport`), `path.py`, `limits.py`, `__init__.py`) | 2D |
| 9.3, 9.6, 9.7 (Worker half) | `royal_jelly/scaffold/` (`scaffolder.py` on `ModelSlot.SCAFFOLDER` through `llm.ladders.complete_structured`, `models.py` (`ToolRequestBrief`, `ScaffoldResult`), `prompts` as `llm/prompts/scaffold_tool.md`, `__init__.py`), `royal_jelly/promotion/` (`flow.py`: `PromotionFlow` with ports `Scaffolder`, `SandboxRunner` (ships the package and runs the Comb somewhere, returning a report), `CombRegistry`, `HoneyDeposit`, `trail`; one retry with the report; `tool.*` event per stage; `events.py`), `royal_jelly/honey_link.py` (9.8: the Nectar text for a promotion); `workers/tools/invoke.py` (`CombToolEntry`, `CombToolRunner`: verify hash, unpack, load `tool:run`, re-validate schema, capabilities and OS before every call, bounded timeout and output), `workers/tools/request.py` (`request_tool`), `workers/tools/retain.py` (`retain`), `workers/context.py` gains `ToolChannel`, `BasketChannel` and `comb_tools: CombToolBundle` (a mutable holder read at every `definitions()`), `workers/runtime/tools.py` (mailbox-backed channels), `workers/tools/registry.py` (`build_registry` offers the bundle's tools) | 2E |
| basket wire + manifest + composition | `basket/transfer/wire.py`; `manifest/schema/royal_jelly.py` (`RoyalJellySection`: `sandbox`, `sandbox_image`, `sandbox_distribution`, `limits`, `max_retries`, `fuzz_cases`; `BasketSection`: `root`, `max_blob_bytes`, `chunk_bytes`, `default_resident_ceiling_mb`) and `manifest/schema/manifest.py`; `docs/manifests/*.toml`; `cli/compose/royal_jelly.py` (`build_royal_jelly(manifest, stores, ...) -> RoyalJellyAccess`: registry, basket store, deposits with the Honey adapter, Comb, scaffolder, sandbox choice); `queen/deps.py` and `wardens/deps.py` gain `royal_jelly: RoyalJellyAccess | None` and `basket: ResidentBasket | None` | 2F |
| Queen side | `queen/ticks/tools/` (`requests.py`: `tool.request` in → validate, `tool.requested`, start a `ToolJob`; `jobs.py`: scaffold → sandbox (provision a Virtual Cell with `network_policy=NONE` through `VirtualCellProvider`, or the Hive Stand Warden when `sandbox = "subprocess"`) → ship blob → `tool.quarantine` → await `tool.quarantine_report` → promote or retry → teardown; `quarantine.py`: a Warden's own `tool.quarantine` (cell scope) brokered the same way and the report relayed back; `announce.py`: `tool.promoted` to every Warden whose Cell's OS is in `supported_os`, shipping the blob first; `basket.py`: `basket.*` in (a Warden's deposit shipped up) and out), `queen/ticks/liveness.py` routes them; `queen/awake` gains the REAL_CELL-path review | 3G |
| Warden side | `wardens/ticks/tools.py` (relay a sub-bee's `tool.request` up with the asker remembered; on `tool.promoted`: ensure resident, re-attenuate the asker's bundle as an intervention and forward to the sub-bee; a cell-scope request: scaffold locally within the grant, deposit, ship, send `tool.quarantine` to the Queen, promote through the registry (in process) or `tool.promoted` (remote) with its `WardenPrincipal`), `wardens/ticks/quarantine_run.py` (a Queen-sent `tool.quarantine`: build the sandbox, run the Comb, reply), `wardens/ticks/basket.py` (receiver side of a shipment, residency, `basket.*` events; a sub-bee's retained artefact deposited or shipped up), `wardens/autopilot/table.py` rows, `wardens/spawn/` hands the bundle to `WorkerContext` | 3H |
| 9.10 CLI | `cli/tools/` (`list`, `show`, `request`, `retire`, `rollback`, `quarantine <path>` which runs the Comb in the subprocess sandbox on a package directory and prints the report), `cli/basket/` (`list`, `show`, `deposit`, `ship`, `evict`, `retire`), `cli/app.py` | 3I |
| exit criteria | `tests/e2e/test_phase9_exit_criteria.py` (+ helpers `tests/e2e/royal_jelly_runs.py`, builders `tests/builders/royal_jelly.py`, `tests/builders/basket.py`), `tests/evals/scaffold/` (the `local_llm` scenario) | 4J |

**Interfaces fixed up front** (so waves can run in parallel):

- `royal_jelly.spec` exports `ToolSpec`, `SpecValidation`, `validate_spec`, `ToolPackage`
  (`pack(files: Mapping[str, bytes]) -> bytes`, `unpack(data: bytes) -> Mapping[str, bytes]`,
  `sha256_of(data) -> str`, `MAX_PACKAGE_BYTES`, `TOOL_MODULE = "tool.py"`, `TESTS_MODULE =
  "test_tool.py"`, `SPEC_FILE = "spec.json"`), `QuarantineReport`, `GateResult`, `ResourceUsage`,
  `QuarantineLimits` (cpu_s, memory_bytes, wall_s, output_bytes, fuzz_cases), `spec_sha256(spec)`.
- `royal_jelly.registry` exports `CombRegistry`, `SqliteCombRegistry`, `CombToolRow`,
  `QueenPrincipal`, `WardenPrincipal`, `Principal`, `apply_comb_registry_migrations`.
- `royal_jelly.quarantine_comb` exports `Sandbox`, `SandboxCell`, `SandboxSubprocess`,
  `QuarantineComb`, `decide_path`, `BANNED_MODULES`, `BANNED_NAMES`.
- `basket` exports `Blob`, `BlobManifest`, `BlobState`, `DepositRequest`, `HoneyRecord`,
  `HoneyRecordPort`, `BasketDeposits`, `BasketStore`, `SqliteBasketStore`, `ResidencyDecision`,
  `decide_residency`, `ResidentBasket`, `blob_clearance`, the transfer models and the two state
  machines, `CHUNK_BYTES`.
- Trail events are built with the existing family classes and recorded through the trail each
  layer already holds; event kinds are listed in section 3.

## 3. Wire and trail additions

**Waggle 1.12** (all additive):

- `basket.offer` (`BasketOffer`, request): `sha256`, `size_bytes`, `media_kind`, `media_type`,
  `platform_tags`, `clearance`, `origin_cell_id`, `origin_task_id | None`, `origin_bee | None`,
  `tainted`, `manifest_json` (the signed `BlobManifest`, bounded text), `destination_cell_id`,
  `reason`. Sender: Queen → Warden, Warden → Queen.
- `basket.ack` (`BasketAck`, reply to `basket.offer` or `basket.chunk`): `sha256`,
  `received_bytes`, `complete`, `reason`.
- `basket.refuse` (`BasketRefuse`, reply): `sha256`, `code` (`BasketRefusal`: CLEARANCE,
  TAINTED, NO_ROOM, UNKNOWN_SIGNER, INTEGRITY, PLATFORM, UNKNOWN_BLOB), `reason`.
- `basket.chunk` (`BasketChunk`, request): `sha256`, `offset`, `data` (base64 text, at most
  `MAX_CHUNK_BYTES` 262144 raw bytes), `is_last`. Reply `basket.ack`.
- `tool.quarantine` (`ToolQuarantine`, request): `tool_id`, `name`, `version`, `scope`,
  `cell_id | None`, `spec_json`, `package_sha256`, `package_size_bytes`, `path`
  (`QuarantinePath`), `limits_json`, `requested_by_warden`, `reason`. Warden → Queen (a sandbox
  request) and Queen → Warden (the order to the sandbox's Warden).
- `tool.quarantine_report` (`ToolQuarantineReport`, reply to `tool.quarantine`): `tool_id`,
  `version`, `passed`, `path`, `sandbox_cell_id`, `failing_gate | None`, `report_json`, `reason`.
- `tool.retired` (`ToolRetired`, event): `tool_id`, `name`, `version`, `scope`, `cell_id |
  None`, `retired_by` (a bee address), `reason`.

**Trail kinds** (every one classified in `guard/policy/catalogue.py`):

- `tool` family (existing six plus): `tool.quarantine_requested`, `tool.sandbox_provisioned`,
  `tool.sandbox_released`, `tool.announced` (a `tool.promoted` sent to one Warden),
  `tool.loaded` (a Worker's bundle gained a Comb tool), `tool.load_refused`, `tool.rolled_back`.
- `basket` family (new `BasketEvent`): `basket.deposited`, `basket.deposit_deduplicated`,
  `basket.deposit_refused`, `basket.offered`, `basket.received`, `basket.refused`,
  `basket.evicted`, `basket.retired`, `basket.shipped`.
- `guard` family: `guard.basket_integrity_failed`.

**Capabilities**: `tool:quarantine_on_real` (flag family `TOOL_QUARANTINE_ON_REAL`), held by
nobody by default; `tool:request` and `tool:scope:cell` already exist. A Comb tool is invoked
under `tool:<name>` like every built-in.

## 4. Build order

- Wave 1 (parallel, disjoint files): 1A wire/trail/docs/ADRs; 1B spec and registry; 1C basket
  core.
- Wave 2: 2D the Comb; 2E scaffold, promotion flow and the Worker half; 2F wire mapping,
  manifest sections and composition.
- Wave 3: 3G Queen ticks; 3H Warden ticks; 3I CLI.
- Wave 4: 4J exit criteria, then the gates, the roadmap tick and this file's handoff sections.

## 5. Where things stand (handoff, written as the work landed)

- **The branch.** `ccr-4e10bf6f-y9zk5g`, from `main` at `4b66406`, pushed after every wave. `git log
  --oneline 4b66406..HEAD` lists the commits, one per wave, each body saying why. **No PR was
  opened**; the operator asked for a branch.
- **The roadmap.** Every box from 9.1 to 9.10, including 9.2a and 9.3a, is ticked with a dated
  as-built note; 9.3a is marked partly built (section 9). The exit criteria's "Met" note is in the
  roadmap below the bullets.
- **The gates.** Section 8 has the commands; the numbers at handover are recorded there.
- **Waggle** is **1.12** here (1.11 is reserved for phase 8's branch). ADRs are **0046 to 0048**
  (0044 and 0045 are phase 8's). If phase 8 never merges, renumber both before merging this.
- **Migrations.** `royal_jelly/registry/migrations/0001_create_comb_registry.sql` (`comb_tools`)
  and `basket/store/migrations/0001_create_basket.sql` (`basket_blobs`, `basket_residency`,
  `basket_shipments`), both in the Hive's one database on their own connections. A Cell's resident
  Basket is its own file under the lease scratch (`basket/basket.sqlite`, `basket/blobs`).
- **Manifest.** `[royal_jelly]` (`sandbox = "virtual" | "subprocess"`, `sandbox_image`,
  `sandbox_distribution`, `limits.*`, `max_retries`, `request_timeout_s`, the sandbox Cell's
  size) and `[basket]` (`root`, `max_blob_bytes`, `chunk_bytes`, `default_resident_ceiling_bytes`,
  `ship_on_promotion`), documented in `docs/manifests/full.toml`. `sandbox = "virtual"` set
  explicitly requires `[virtual_cells] backend`; the dev `subprocess` sandbox warns at build.
- **Dependencies.** None added.

## 6. What phase 9 implemented (map)

Paths are relative to `packages/hivemind/src/hivemind/` unless they say otherwise.

| Step | Modules | Notes |
|---|---|---|
| 9.1 | `royal_jelly/spec/` (models, validate, package, report) | `validate_spec` is pure; a package is a byte-identical zip; `QuarantineReport` requires a passing report to hold exactly `GATE_ORDER`. |
| 9.2, 9.9 | `royal_jelly/registry/` (protocol, principals, rules, sql, writes, sqlite, fake, migrations), `royal_jelly/tool_state.py`, `royal_jelly/errors.py` | `promote` is the only path to a PROMOTED row; `retire` keeps the row; `rollback` re-currents an older passing row. Contract suite over SQLite and the in-memory fake; a no-bypass suite. |
| 9.2a | `basket/` (models, manifest, clearance, errors, events, deposits, residency, `store/` with `sql/`, `transfer/` with state, sender, receiver, spool, wire), `waggle/messages/basket/`, `pheromone/.../resources.py` `BasketEvent`, `guard/policy/catalogue.py`, `cli/basket/` | The deposit door signs with the Hive key and writes one Honey record through a port; the receiver keeps bytes only after hash and signature verify; residency evicts oldest first under the ceiling. |
| 9.3 | `royal_jelly/scaffold/` (models, scaffolder, render, fake), `llm/prompts/scaffold_tool.md` | Tests first; the model never sets name, scope, ids or capabilities. |
| 9.3a | `quarantine_comb/path.py`, `guard/capabilities/families.py` (`tool:quarantine_on_real`), `queen/ticks/tools/review.py` | The Real Cell tests-gate leg is not built (section 9). |
| 9.4 | `quarantine_comb/sandbox.py`, `sandbox_cell.py`, `sandbox_subprocess.py`, `queen/cell_gate/provider.py` (`acquire_sandbox`, `release_sandbox`) | A sandbox Virtual Cell has `network_policy = NONE` and is the Queen's; torn down after the report. |
| 9.5 | `quarantine_comb/comb.py`, `gates/` (base, banned, scan, static, tests, schema, fuzz, resources, capabilities, defaults), `runner/` (main, phases, hooks, session, inputs, usage, report) | The runner executes inside the sandbox; `python -m hivemind.royal_jelly.quarantine_comb.runner`. |
| 9.6 | `royal_jelly/promotion/` (flow, ports, events), `queen/ticks/tools/` (requests, jobs, runner, quarantine, replies, review, announce, promotions, honey, events, `transfer/` ship, receive, drain), `wardens/ticks/tools/` (relay, pending, install, cell_scope, ports, remote_registry, quarantine_run, seams, messages) | The Queen runs the flow beside her tick; a Warden runs the cell-scope flow in a job of its own. |
| 9.7 | `workers/tools/comb/` (entry, invoke, loader, request, retain), `workers/runtime/tools.py`, `workers/roles/bounded_loop/` (`ToolsChangedError` rebuilds the registry), `wardens/spawn/comb.py`, `wardens/ticks/basket/` (receive, send, retain, door, source, state, jobs, events, handle) | A bundle changes only when the Warden replaces it; `request_tool` and `retain` are Worker tools. |
| 9.8 | `royal_jelly/honey_link.py`, `queen/ticks/tools/honey.py` | One Nectar per promotion, `source_key` `tool:<name>:<version>`. |
| 9.10 | `cli/tools/`, `cli/basket/`, `cli/app.py` | `hive tools --manifest M list|show|request|retire|rollback|quarantine <path>`. |
| composition | `manifest/schema/royal_jelly.py`, `cli/compose/royal_jelly/` (build, honey_port), `cli/compose/deps.py`, `cli/stores.py` (`open_comb_registry`, `open_basket_store`), `royal_jelly/access.py`, `cli/in_cell/basket.py` | `RoyalJellyAccess` reaches `QueenDeps.royal_jelly` and `WardenDeps.royal_jelly`; a Cell's Warden gets `resident_basket`, `basket_store`, `node_signer`, `hive_verifier`. |

## 7. How the pieces meet at runtime

- **Hive-scope request.** A Worker calls `request_tool` (needs `tool:request`); its Warden checks
  first-hop identity and relays `tool.request` to the Queen. The Queen validates the capabilities
  against the task's goal ceiling narrowed by the Worker role, records `tool.requested`, and starts
  a job: scaffold on SCAFFOLDER → deposit the package in the Basket (`tool.scaffolded`) →
  provision a sandbox Virtual Cell (or use the dev subprocess sandbox) → ship the blob there →
  `tool.quarantine` → await the correlated `tool.quarantine_report` (`tool.quarantined`) →
  `registry.promote` with the Queen principal (`tool.promoted`) → Nectar → announce to every
  Warden whose Cell's OS the tool supports, blob first (`tool.announced`). The requester's Warden
  installs the entry once the blob is resident and verified, replaces the bundle of every running
  sub-bee allowed to call it, and forwards one `tool.promoted` to the asker; the bounded loop
  rebuilds its registry and the model sees the tool on its next round. One retry with the report;
  then `tool.rejected` and `control.error hive.tool.rejected` to the asker.
- **Cell-scope request.** The asker needs `tool:request` and the Warden `tool:scope:cell`. The
  Warden scaffolds on the first SCAFFOLDER binding the sub-bee's grant allows, deposits (Hive
  Stand) or deposits locally and ships up (a Cell), sends `tool.quarantine` to the Queen as its
  sandbox request, and on a passing report promotes: in process with `WardenPrincipal(warden,
  cell)` on the Hive Stand, or by `tool.promoted` to the Queen, who accepts it only against the
  run she brokered for that Warden. Only that Cell's bees ever see the tool.
- **Shipping.** `basket.offer` → ack or refuse by rule (clearance above the Cell's ceiling, so a
  Real Cell origin never reaches Night Veil; tainted; no room; unknown signer; platform) →
  `basket.chunk`s, one in flight per ack → `basket.ack(complete)`. The receiver spools to a temp
  file, verifies the whole hash and the manifest signature, and only then keeps the bytes;
  `guard.basket_integrity_failed` otherwise. Upward offers are signed with the Warden's node key
  and re-deposited by the Queen through the signed door, which writes the Honey record.
- **Retain.** `retain(path, title, summary, kind)` is a capped COPY in scratch; once VERIFIED the
  Worker's `RetainSink` hands the Warden a `RetainDeposit`, which the Warden re-hashes, labels by
  provenance (C2 from a borrowed Cell) and taint, and deposits or ships up.
- **Invocation.** A promoted tool appears in a Worker's registry by name; every call re-validates
  the arguments against the schema, every required capability against the Worker's set and the
  Cell's OS against `supported_os`, then loads the resident package only if its bytes hash to the
  entry's `package_sha256`. A hash with no Comb entry has no API to be invoked.

## 8. Testing state and how to run the gates

Run everything from the repo root with `uv run --frozen`:

```sh
uv run --frozen ruff format --check . && uv run --frozen ruff check . && uv run --frozen mypy
uv run --frozen lint-imports
for s in check_sizes check_fanout check_no_model_ids check_no_kind_branches check_no_transcripts; do
  uv run --frozen python scripts/$s.py || break
done
uv run --frozen pytest -q -p no:cacheprovider \
  -m "not integration and not e2e and not live_llm and not local_llm" packages scripts/tests
uv run --frozen pytest -q -p no:cacheprovider -m e2e packages/hivemind/tests/e2e
```

The Comb's own tests start a real runner in the subprocess sandbox (about 3 s of CPU each, since
importing `hivemind.royal_jelly` pulls in `hivemind.llm`); run the suite on an idle host, as the
phase 7 handoff already warns for placement.

GATES_AT_HANDOVER

## 9. Open items

1. **The Real Cell tests-gate leg of 9.3a is not built.** `decide_path` chooses `REAL_CELL` for a
   non-Ubuntu target, the capability, the report field, the registry rule and the Queen's awake
   review exist, but `QuarantineComb.run(path=REAL_CELL)` only stamps the report; a sandbox over a
   lease's scratch on a Real Cell of the target platform, with the flight recorder on at the
   `device_command` tier, is still to write, and no second-platform Real Cell exists in CI.
2. **Tool packages are C2.** The Queen deposits a scaffolded package from the Hive Stand with
   `from_borrowed_cell=True`, so the Basket floor labels it C2 and a Night Veil Cell (ceiling C1)
   refuses every tool package: Night Veil Cells get no Comb tools. Either label the Queen's own
   packages C0 (the model wrote them; they hold no operator data) or add a lowering path like
   ADR-0038's. An operator decision.
3. **Remote cell-scope promotion is weakly acknowledged.** A Cell's Warden sends `tool.promoted`
   and treats two seconds without a `control.error` as acceptance; the Queen's `tool.promoted`
   trail event is the record. An explicit ack kind would be an additive minor bump.
4. **Swarm uploads.** `QueenDeps.uplink_keys` knows Virtual Cells' node keys from the cell gate;
   Real Cells enrolled through the Swarm (phase 11) have no key source yet, so their upward offers
   are accepted only when signed with the Hive key.
5. **`request_tool` is offered to every Worker** (one extra tool definition per prompt);
   `tool:request` decides at invocation. Offer it only to Workers that hold the flag if prompt size
   matters.
6. **A sandbox Cell may receive a catch-up announcement** on attach (the Queen cannot tell a fresh
   sandbox from an ordinary Cell until provisioning returns); harmless, one blob and one
   `tool.promoted`.
7. **`hive basket show` lists residency only for the Hive Stand, the origin Cell and pending
   shipment destinations**; a `holders(sha256)` read on `BasketStore` would make it exact.
8. **A Cell-retained blob's Honey title is the Queen's generic text**, because only the offer's
   `reason` (200 chars) reaches her; carry title and summary on `basket.offer` in a later minor.
9. **`basket/context.py` repeats the Basket-root rule** (`[basket] root` else `basket/` beside the
   database) that `cli/compose/deps._basket_root` holds privately; make it public and share.
10. **`ScanSource` has no Basket member**; the deposit door scans as `NECTAR_INTAKE`.
11. **`tool.retired` is not handled by Wardens** (falls to `NEEDS_JUDGEMENT`); a Warden learns a
    retirement only through the registry it reads at spawn.
12. **In-Cell residency ceilings** are the `[basket]` default; `Ceilings.resident_basket_disk_bytes`
    reaches a Cell only on a later grant (`resident_ceiling_bytes` applies the non-zero rule) and a
    `ResidentBasket` has a fixed ceiling, so a new one must be built when a grant changes it.

13. **No Worker role holds `tool:request` in the shipped policy**, and an in-Cell Warden reads no
    `[guard]` section, so a Worker on a Virtual Cell cannot request a tool at all; the e2e scenarios
    grant the flag through a `[guard.roles.<role>]` overlay. Decide which roles should hold it and
    ship the overlay to in-Cell Wardens.
14. **`hive tools request --cell` cannot succeed**: the Queen's door takes only hive-scope requests
    from a Warden and a cell-scope tool is built by its own Cell's Warden for a Worker. Either drop
    the option or run the request through a stand-in sub-bee on the Hive Stand Warden.
15. **A call to a tool that was never offered leaves no trail row**: the tool ladder refuses it
    before the registry. Adding a `guard.denied` row for a hallucinated name would feed the Guard
    Bee's denial rules, so it was left out; say if you want it.
16. **Every grant now carries a SCAFFOLDER binding** (`GrantInputs.extra_slots`) so a Warden can
    scaffold within its grant; a goal narrowed to `llm:worker` records one `guard.denied` row per
    grant for it. Carry it only when a goal may request tools if that noise matters.