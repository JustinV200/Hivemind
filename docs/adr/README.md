# Architecture Decision Records

An Architecture Decision Record, ADR for short, is a short document that captures one decision
that is hard to reverse once code depends on it: a transport choice, a storage engine, a sandbox
technology, an auth model, and anything else of that shape. It is not a place for decisions that
are cheap to change later; those stay as comments or commit messages instead.

## When to write one

Write the ADR before the code that depends on it, not after. If a roadmap step forces a
hard-to-reverse choice, the ADR for that choice lands first, so the implementation can cite it
rather than the implementation quietly becoming the decision.

## Numbering and naming

Files live in this directory as `NNNN-kebab-case-title.md`, where `NNNN` is a four-digit number,
zero-padded, assigned next in sequence after the highest number already in use. `0000` is reserved
for the template (`0000-adr-template.md`) and is never itself a real decision. For example, the
eighteenth ADR, about how Virtual Cell backends share one interface, would be filed as
`0018-virtual-cell-backend-protocol.md`.

Start a new ADR from the template at `docs/adr/0000-adr-template.md`: copy it, fill in the title,
status, date, and the four sections, and replace every italic guidance line.

## Status lifecycle

Every ADR carries exactly one status, on the `- Status:` line:

- **Proposed**: drafted and awaiting acceptance.
- **Accepted**: in force. Code may rely on it.
- **Superseded by ADR-NNNN**: replaced by a later decision; the number points at the ADR that
  replaced it.

An accepted ADR is never edited again, not even to fix a typo in the decision itself (wording and
formatting fixes that do not change the recorded decision are fine). If the decision changes,
write a new ADR with the next number, set its status to Accepted, and change the old one's status
line to `Superseded by ADR-NNNN` pointing at the new one. The old ADR's Context, Decision,
Consequences and Alternatives sections stay untouched: they are the historical record of what was
decided and why, at the time.

## Who accepts

The implementer that drafts an ADR never accepts it. Acceptance (moving the status from Proposed
to Accepted) is done by the project owner or by the orchestrating agent coordinating the work,
because the point of an ADR is a second, independent look at a decision before code depends on it.

## Index

ADRs that exist today, in numeric order:

- `0000-adr-template.md`: the template every ADR is copied from. Not a decision.
- `0001-language-and-toolchain.md`: Python plus `uv`, `ruff` and `mypy` for the backend;
  TypeScript (strict) plus React for the front end.
- `0002-workspace-layout-and-layering.md`: the three Python packages plus the web package, the
  layer table, and `llm` importing `forage` rather than the reverse.
- `0003-ids-clock-and-loop-live-in-waggle.md`: prefixed ULIDs, an injected `Clock`, and the shared
  loop shape all live in the `waggle` package, not in `hivemind.common`.
- `0004-waggle-transport-websocket-json.md`: JSON envelopes, one per binary WebSocket frame, over
  `websockets`; loopback by default, every Cell dials out, and no reconnect inside `receive()`.
- `0005-waggle-envelope-signing-and-offline-outbox.md`: the ten envelope fields, Ed25519 over
  canonical JSON keyed by `node_id`, and the append-only JSONL outbox of unsigned envelopes.
- `0006-sqlite-as-the-single-hive-store.md`: one SQLite file per Hive through the standard
  library's `sqlite3` under `asyncio.to_thread`, explicit `BEGIN IMMEDIATE` transactions, one
  migration series per subsystem, and what would force a move to Postgres.
- `0007-pheromone-trail-append-only-transactional-and-segmented.md`: one event class per family
  with a closed kind vocabulary, no `UPDATE`/`DELETE` in the store module, state and event in one
  transaction, segments keyed by node id merged by id, and the Night Veil purge as the one deletion.
- `0008-llm-provider-independence-and-model-slots.md`: one `LLMProvider` door, HiveMind's own
  request/response types, declared capabilities instead of provider-name branches, model slots
  and `Effort` in `forage`, offline mode, and normalised `Usage`.
- `0009-structured-output-and-tool-call-degradation-ladders.md`: rungs and protocols chosen from
  declared capabilities, a `CallGate` seam for the Fanner, a `LadderObserver` seam for the
  Pheromone Trail, and a documented JSON-schema subset instead of a `jsonschema` dependency.
- `0010-cells-are-real-or-virtual-terminal-first.md`: one `Cell`/`CellSession` abstraction for
  both Real and Virtual Cells, a `RealCellLease` that owns its mutable bookkeeping and delegates
  killing/restoring to an injected `LeaseReleaser`, and the Hive Stand as the first Real Cell
  source because it needs nothing provisioned or enrolled.
- `0011-kernel-shape-autopilot-then-awake.md`: the Queen and every Warden run autopilot (a
  deterministic dispatch table that never awaits a model) first, and only what autopilot cannot
  decide runs a stateless awake episode assembled fresh from durable state.
- `0012-wardens-alarms-and-the-escalation-chain.md`: one Warden per Cell, the same alarm id at
  every hop, escalation policy as data (TOML), and the human reached only through the Queen.
- `0013-attendant-for-every-supervisor.md`: one deterministic `Attendant` scorer parametrised by a
  `WeightTable`, shared by the Queen and every Warden; a model only arbitrates an exact tie, and
  only where the grant allows it.
- `0014-forage-grants-and-attenuation.md`: a `ForageGrant` only ever narrows a Cell's raw capacity
  (Royal Reserve, then headroom, then the tightest of five limits), computed by one pure
  `forage.allocate.grant`; its lifecycle is a separate `GrantState` table from its terms.
- `0015-forage-map-seats-footprints-and-the-fanner.md`: `ModelSource` splits a static
  `ModelSourceSpec` from live `Distance`/`Abundance`; `ForageMap` locks only its two writers; the
  Fanner (seat enforcement) lives in `llm`, one layer above, and meets the map through
  `observe`/`set_abundance`.
- `0016-forage-two-pools-ceilings-and-hosting-plans.md`: `LocalPool`/`Ceilings` are distinct types
  from the shared-pool `ForageGrant`/`RoyalReserve`; a `HostingPlan` is a per-slot `SourceChain`,
  not a single hosting flag.
- `0017-tempo-speed-against-accuracy.md`: one table, `GRADE_FLOORS`, is the only place a task's
  accuracy bar becomes a minimum Forage map grade (`LOW`→1, `NORMAL`→2, `HIGH`→3, `CRITICAL`→4).
- `0018-capping-gate-postconditions-and-risk-tiers.md`: risk tiers as data with checks
  cheapest-first and a fail-closed unavailable check, `REVERSE_DIFF` vs snapshot rollback chosen
  by catching `SnapshotUnsupportedError` rather than branching on Cell kind, `LeaseView` as a
  Protocol seam to `cell`, and an in-memory proposal table with the trail as the durable record
  for now.
- `0019-queen-kernel-autopilot-first-with-stateless-awake-episodes.md`: the Queen runs a
  deterministic dispatch table before ever waking a model, an awake episode is assembled fresh
  from durable state and discarded after one decision, she holds no `CellSession` and no Comb
  Registry, the planner emits acceptance for every subtask, and placement v0 is the Hive Stand
  only.
- `0020-llm-records-its-own-trail-events.md`: Layer 1 becomes two ranks, `llm | manifest` above
  `pheromone | forage`, so the ladders and the Fanner record their own `llm.*` events; amends
  ADR-0002's Layer 1 row only.
- `0021-queen-never-executes-but-rebinds-and-takes-over.md`: the Queen holds no session and
  assigns only to Wardens; recovery is a Warden respawning from the failed bee's `Handoff`
  (taking over through a fresh bee, never in the Queen's process) and rebinding within the
  grant, with the Queen answering an escalated Alarm by `Intervene(REBIND)` to the manifest's
  fallback binding, and the human inbox last.
- `0022-memory-tiers-relevance-and-compaction.md`: one pure relevance score orders hot state,
  Bee Bread is lookup-only and is where every dropped item lands, Cell Wax is a capped hot-state
  item only the Queen writes, compaction summarises source records one level deep, and overflow
  shrinks and retries.
- `0023-forage-ledger-and-model-hosting-decisions.md`: the Forage ledger is a persisted store of
  reports and grants with headroom derived on read, local pools are reported never granted,
  grants are leases on one state table, hosting plans and ceilings are written by the Queen with
  a reason, and hosted rate limits are measured from provider headers with a 429 throttling the
  source on the map.
- `0024-clustering-protocol.md`: Clustering pauses per provider through the checkpoint-and-Handoff
  path, keeps leases and Cells alive, resumes from Handoffs without redoing work, and takes
  `hive cluster` / `hive wake` orders through a durable table the running Queen polls.
- `0025-leavings-declared-by-the-plan-decided-by-policy.md`: what a task may leave on a Cell is
  declared by the plan and never widened by a bee, decided by a pure policy table whose `DENY`
  restores rather than rejects, asked of the human only on `ASK`, and listed in a ledger that
  keeps the original bytes, so "left as found" means the snapshot plus exactly the ledger's paths.
- `0026-cell-backends-docker-first-qemu-second.md`: one `CellBackend` protocol with declared
  `BackendCapabilities`, Docker first and QEMU second, everything a backend creates labelled with
  the Hive id so orphans are found from the infrastructure alone, all-or-nothing `provision` and
  idempotent `destroy`.
- `0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md`: a Virtual Cell listens on
  nothing; its entry point boots a Warden that dials the Queen, `provision` returns only after
  `CellReady` and the first `Heartbeat`, sub-bees use the `in_cell` strategy, and each Cell gets
  its own signing key.
- `0028-placement-policy-real-versus-virtual.md`: `decide` is pure and returns a union (reuse a
  Real Cell, reuse a dormant one, or provision from a spec) with its reason; hard rules
  (isolation, Night Veil, `BLOCK` wax, fit, Forage) run before `prefer` is ever read.
- `0029-overwintering-policy.md`: a bounded pool of paused Virtual Cells, scrubbed on the way in,
  reused through placement, expired by the Undertaker, and never a Night Veil Cell.
- `0030-night-veil-retention-and-clearance-boundary.md`: readiness is attestation of an image,
  placement is Virtual-only, human-originated and local-model-only, only the lifecycle skeleton
  survives teardown, and C0/C1 Honey labelled with its origin tier is the one export.
- `0031-capability-model-attenuation-and-enforcement-points.md`: one capability grammar with six
  scope kinds and longest-prefix parsing, role default sets that only attenuate down the tree,
  access levels that narrow only what touches the Cell, a pure `evaluate` with a rule and a
  reason, and a named enforcement point for every trail event kind that records an action.
- `0032-hive-entrance-http-websocket-api-and-human-inbox.md`: FastAPI on uvicorn, two listeners
  built as two applications from one route table, the Entrance in the Queen's process calling her
  public API, and the chat as the human end of her inbox.
- `0033-landing-board-enrolment-two-factor-login-and-exposure.md`: devices enrol with their own
  key and are approved only on loopback, log in with it plus the operator's Argon2id password,
  sign every request with a session-bound key, step up for anything sensitive, and reach the
  Entrance over a Tailscale overlay by default, with mutual TLS for LAN and tunnel and no public
  mode.
- `0034-landing-board-versioning-and-push.md`: `/v1/` changes only additively and the committed
  OpenAPI document is checked in CI; push notices carry no content, over WebSocket, signed
  webhooks and hand-rolled RFC 8291 Web Push, and an answered question is withdrawn everywhere.
- `0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md`: the Guard Bee watches from
  the Queen's process and can only request; only the Queen isolates a Cell; quarantine is one
  intervention; taint is one label that nothing assembles until a judge clears it; the injection
  signal comes from one deterministic scanner over pattern data.
