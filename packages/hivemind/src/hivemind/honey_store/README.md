# hivemind.honey_store

The honey_store package is the Honey Store, the Hive's cold-tier knowledge base. Raw Nectar
(unprocessed captured information) is taken in, ripened through a pipeline into Honey
(retrievable, labelled knowledge), and retrieved by Workers before they act.

## Public API

- **Errors** (`errors.py`): `HoneyStoreError` (root), `NectarNotFoundError`/`HoneyNotFoundError`
  (a lookup by id found nothing), `NectarRejectedError` and its ten concrete reasons
  (`NectarTooLargeError`, `OffsetMismatchError`, `TooManyOpenDepositsError`,
  `Sha256MismatchError`, `FirstChunkNotAtZeroError`, `NightVeilRefusedError`,
  `DepositTimedOutError`, `ChunkMismatchError`, `CellMismatchError`,
  `DepositLengthMismatchError` -- each fixes `code` to one stable dotted string; `except
  NectarRejectedError:` catches any of them), `NectarNotRipenableError` (a Night Veil Cell's
  ephemeral side channel asked to ripen), `LabelLoweringError` (a lowering that is not lower, or
  has no approver), `InvalidScopeError` (a scope does not match
  `waggle.messages.honey.hit.SCOPE_PATTERN`, or carries a `/` or a glob character in its id), and
  the lowering proposal's own (ADR-0034): `LoweringNotFoundError`, `LoweringTransitionError` (an
  edge the proposal's transition table forbids), `LoweringIneligibleError` (the human approved a
  REJECTED proposal that no longer stands), `LoweringPostconditionError` (a lowering's read-back
  disagreed; rolled back), `LoweringInputError` (a human decision without a usable reason) and
  `ClearanceJudgeAnswerError` (the judge could not answer: not a rejection).
- **Scope** (`scope.py`, pure): `HIVE_SCOPE`; `cell_scope`/`task_scope`/`bee_scope` builders;
  `folder_for_scope`/`scope_for_folder`, the browser-path mapping (roadmap 7.10); `honey_ref`/
  `parse_honey_ref`, a Honey row's public reference; `scope_for_nectar`, ADR-0031's scoping table;
  `queen_read_capabilities`/`warden_read_capabilities`/`worker_read_capabilities`, the default
  `honey:read` capability sets until phase 10's policy engine issues real ones; `readable_globs`/
  `is_readable`, built on `hivemind.guard`'s own glob matching.
- **Clearance** (`clearance.py`, pure): `LabelApprover` (`JUDGE`, `HUMAN`); `intake_floor`/
  `intake_label`, the label a fresh deposit gets; `raise_label`, the one direction ripening may
  move a label on its own; `reader_ceiling`, the highest label a request may see; `check_lowering`,
  the one gate between a lowering and `LabelLoweringError`.
- **Models** (`models/`): `Nectar`/`NectarDraft`/`NectarOrigin`/`NectarState` (raw findings before
  ripening, with ADR-0034's three labelling facts: `declared_clearance` and `floor_clearance` from
  intake, `ripener_clearance` from ripening; all None on a row from before it); `RipenerReading`
  (the Ripener's own label and reason for a text); `NectarSource` (a content duplicate's extra
  provenance -- task, Cell, bee, when, never content -- ADR-0033); `Honey`/`HoneyDraft`/`HoneyPart`
  (ripened, retrievable knowledge, with `Honey.path` and `Honey.to_hit`); `ReadFilter`/
  `TextCandidate`/`VectorCandidate`/`HoneyStats` (the shapes a search and `HoneyStore.stats` pass
  around).
- **Schema** (`schema/`): `apply_honey_store_migrations` applies `migrations/
  0001_create_honey_store.sql` -- `honey_nectar`, `honey`, `honey_vectors`, `honey_watermarks`,
  `honey_proposals`, the external-content FTS5 index `honey_fts` and its three sync triggers --
  then `migrations/0002_nectar_sources.sql` -- `honey_nectar_sources` (ADR-0033) -- then
  `migrations/0003_label_lowering.sql` -- the three labelling facts (with rank columns) and the
  Ripener's staged reason on `honey_nectar`, and `honey_lowerings`, one lowering proposal per
  Nectar (ADR-0034).
- **Store** (`store/`): `HoneyStore`, the persistence protocol every implementation honours (and
  the contract later dispatches code against), plus its own `NectarAdded`/`HoneyProposal`/
  `PruneResult` return shapes and `NectarEvents`/`PruneEvents` (how `add_nectar`/`prune_vectors`
  build their trail events from what they found: a new row, a duplicate, a raised label or
  nothing for a Night Veil deposit; a prune's per-model counts, or nothing for a refusal);
  `has_source` and the new `nectar_sources`, `scope_counts` reads (ADR-0033); `ripen`'s optional
  `reading` (the Ripener's own label, stored in the same transaction, ADR-0034) and the lowering
  methods `lowering_candidates`, `add_lowering`, `list_lowerings`, `pending_lowerings`,
  `get_lowering`, `note_lowering`, `apply_lowering` and `reject_lowering`, with their
  `LoweringEvents` builder shape; `build_match`, a safe FTS5 MATCH string from arbitrary text;
  `SqliteHoneyStore`, the durable implementation, split by table responsibility under
  `store/sqlite/` (`nectar.py`, `honey.py`, `vectors.py`, `sources.py` for
  `honey_nectar_sources`, `lowering.py` for `honey_lowerings`, `search.py`, `stats.py`, `vec.py`
  for the sqlite-vec extension load and float32 codec, `filters.py` for `ReadFilter`'s shared
  SQL).

- **Identity** (`identity.py`): `HoneyIdentity` and `honey_event`, the one place a `HoneyEvent` is
  minted (ids, counts, enum values and booleans only in a payload, never content or query text).
- **Intake** (`nectar/`, roadmap 7.4): `NectarIntake` (the one door: size cap, label -- kept with
  the declared label and the floor it is made of, ADR-0034 -- scope, the Night Veil rule, dedupe,
  events) over `ChunkGroups` (Waggle chunk reassembly, spec section 5);
  `NectarSubmission`, `DepositSource`, `IntakeResult`, `handoff_source_key`. See its README.
- **Ripening** (`ripening/`, roadmap 7.5): `Ripener.run_pass` (decode, chunk, summarise on
  `RIPENER`, embed on `EMBEDDER`, dedupe, index; then `embed_pending` for rows still lacking a
  vector for the current model), `RipenerDeps`, `PassOutcome`, `RipenOutcome`; `prune_vectors`,
  `PruneOutcome` (drop a superseded model's vectors on the operator's own word, ADR-0033, never
  automatically). See its README.
- **Retrieval** (`honey/`, roadmap 7.7): `HoneyRetriever` (hybrid full-text and vector search under
  a `HoneyReader`'s scope, clearance and budget), `HoneySearch`, `RetrieverDeps`, `SearchOutcome`.
  See its README.

- **Browsing** (`browse/`, roadmap 7.10): `HoneyBrowser` (a read-only folder tree over the store:
  `/hive`, `/cells/<id>` with the Cell's live wax under `wax/`, `/bees/<id>`, `/tasks/<id>`,
  `/bee-bread`; listing, reading and search per folder, every entry filtered by the reader's
  `honey:read` capabilities and clearance ceiling), `BrowserDeps`, the `LiveWaxSource` and
  `BeeBreadSource` seams a caller implements over memory, and `HoneyRelabeller` (the operator's
  own raise or lowering, approver HUMAN, recorded on the trail). `cat` returns a `HoneyDocument`
  for a Honey row: `Honey` plus its Nectar's extra sources (ADR-0033), so `hive honey cat` can
  list who else deposited the same text. Proposing a note from a folder queues it for the House
  Bee (a Honey note) or files Cell Wax (from a Cell's folder). See its README.
- **Label lowering** (`lowering/`, ADR-0034): `LabelLowering` over `LoweringDeps(store,
  identity, clock, settings, judge=None)`: `file_proposals()` files one proposal per newly
  eligible Nectar, `review_pending()` asks the clearance judge about waiting ones and applies or
  rejects each, `decide(proposal_id, approve, reason)` is the human's word. `lowering_target` (the
  pure rule), `LoweringState` (its one transition table), `ClearanceJudge`/`ModelClearanceJudge`
  (the JUDGE-slot review, prompt `judge_clearance.md`) and `FakeClearanceJudge`. See its README.
- **Access** (`access.py`): `HoneyAccess`, one Hive's store, intake, retriever and Ripener with
  their policy, plus `[honey.lowering]` and the optional clearance judge, built only by
  `hivemind.cli.compose.honey.build_honey_access`.

## Lowering a label (ADR-0034)

A model may raise a label and never lower one. Lowering is a proposal: the House Bee's pass, after
it ripens, files one for each Nectar whose label only the Real Cell floor holds up and whose text
the Ripener read as less sensitive, then asks the independent judge (the JUDGE slot, shown only the
deposit's title, text, kind and media type and the target label) about waiting ones. An APPROVE
applies in one transaction that re-checks eligibility, lowers the Nectar and every Honey row of it
still at the old label, reads them back, and records `honey.label_lowered`; a REJECT, the human's
denial or lost eligibility records `honey.lowering_rejected`; filing records
`honey.lowering_proposed`. A text over `[honey.lowering] max_judge_chars`, a judge that fails
`max_attempts` times, no JUDGE binding or `enabled = false` leave proposals waiting for the human,
who may also lower a REJECTED one.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/honey_store packages/hivemind/tests/unit/guard \
    packages/hivemind/tests/unit/cell packages/hivemind/tests/contracts/test_honey_store_contract.py \
    packages/hivemind/tests/contracts/test_honey_store_lowering_contract.py
```

Coverage floor is 95% for the pure cores (`scope.py`, `clearance.py`, `store/fts.py`,
`lowering/rules.py`, `lowering/state.py`) and 80% for the SQLite adapter (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.honey_store uv run --frozen pytest -p no:cacheprovider \
    --cov=hivemind.honey_store --cov-report=term-missing packages/hivemind/tests/unit/honey_store \
    packages/hivemind/tests/contracts/test_honey_store_contract.py \
    packages/hivemind/tests/contracts/test_honey_store_lowering_contract.py
```
