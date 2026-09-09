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
