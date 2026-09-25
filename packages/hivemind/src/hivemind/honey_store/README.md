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
  `waggle.messages.honey.hit.SCOPE_PATTERN`, or carries a `/` or a glob character in its id).
- **Scope** (`scope.py`, pure): `HIVE_SCOPE`; `cell_scope`/`task_scope`/`bee_scope` builders;
  `folder_for_scope`/`scope_for_folder`, the browser-path mapping (roadmap 7.10); `honey_ref`/
  `parse_honey_ref`, a Honey row's public reference; `scope_for_nectar`, ADR-0035's scoping table;
  `queen_read_capabilities`/`warden_read_capabilities`/`worker_read_capabilities`, the default
  `honey:read` capability sets until phase 10's policy engine issues real ones; `readable_globs`/
  `is_readable`, built on `hivemind.guard`'s own glob matching.
- **Clearance** (`clearance.py`, pure): `LabelApprover` (`JUDGE`, `HUMAN`); `intake_floor`/
  `intake_label`, the label a fresh deposit gets; `raise_label`, the one direction ripening may
  move a label on its own; `reader_ceiling`, the highest label a request may see; `check_lowering`,
  the one gate between a lowering and `LabelLoweringError`.
- **Models** (`models/`): `Nectar`/`NectarDraft`/`NectarOrigin`/`NectarState` (raw findings before
  ripening); `NectarSource` (a content duplicate's extra provenance -- task, Cell, bee, when,
  never content -- ADR-0037); `Honey`/`HoneyDraft`/`HoneyPart` (ripened, retrievable knowledge,
  with `Honey.path` and `Honey.to_hit`); `ReadFilter`/`TextCandidate`/`VectorCandidate`/
  `HoneyStats` (the shapes a search and `HoneyStore.stats` pass around).
- **Schema** (`schema/`): `apply_honey_store_migrations` applies `migrations/
  0001_create_honey_store.sql` -- `honey_nectar`, `honey`, `honey_vectors`, `honey_watermarks`,
  `honey_proposals`, the external-content FTS5 index `honey_fts` and its three sync triggers --
  then `migrations/0002_nectar_sources.sql` -- `honey_nectar_sources` (ADR-0037).
- **Store** (`store/`): `HoneyStore`, the persistence protocol every implementation honours (and
  the contract later dispatches code against), plus its own `NectarAdded`/`HoneyProposal`/
  `PruneResult` return shapes and `NectarEvents`/`PruneEvents` (how `add_nectar`/`prune_vectors`
  build their trail events from what they found: a new row, a duplicate, a raised label or
  nothing for a Night Veil deposit; a prune's per-model counts, or nothing for a refusal);
  `has_source` and the new `nectar_sources`, `scope_counts` reads (ADR-0037); `build_match`, a
  safe FTS5 MATCH string from arbitrary text; `SqliteHoneyStore`, the durable implementation,
  split by table responsibility under `store/sqlite/` (`nectar.py`, `honey.py`, `vectors.py`,
  `sources.py` for `honey_nectar_sources`, `search.py`, `stats.py`, `vec.py` for the sqlite-vec
  extension load and float32 codec, `filters.py` for `ReadFilter`'s shared SQL).

- **Identity** (`identity.py`): `HoneyIdentity` and `honey_event`, the one place a `HoneyEvent` is
  minted (ids, counts, enum values and booleans only in a payload, never content or query text).
- **Intake** (`nectar/`, roadmap 7.4): `NectarIntake` (the one door: size cap, label, scope, the
  Night Veil rule, dedupe, events) over `ChunkGroups` (Waggle chunk reassembly, spec section 5);
  `NectarSubmission`, `DepositSource`, `IntakeResult`, `handoff_source_key`. See its README.
- **Ripening** (`ripening/`, roadmap 7.5): `Ripener.run_pass` (decode, chunk, summarise on
  `RIPENER`, embed on `EMBEDDER`, dedupe, index; then `embed_pending` for rows still lacking a
  vector for the current model), `RipenerDeps`, `PassOutcome`, `RipenOutcome`; `prune_vectors`,
  `PruneOutcome` (drop a superseded model's vectors on the operator's own word, ADR-0037, never
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
  for a Honey row: `Honey` plus its Nectar's extra sources (ADR-0037), so `hive honey cat` can
  list who else deposited the same text. Proposing a note from a folder queues it for the House
  Bee (a Honey note) or files Cell Wax (from a Cell's folder). See its README.
- **Access** (`access.py`): `HoneyAccess`, one Hive's store, intake, retriever and Ripener with
  their policy, built only by `hivemind.cli.compose.honey.build_honey_access`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/honey_store packages/hivemind/tests/unit/guard \
    packages/hivemind/tests/unit/cell packages/hivemind/tests/contracts/test_honey_store_contract.py
```

Coverage floor is 95% for the pure cores (`scope.py`, `clearance.py`, `store/fts.py`) and 80% for
the SQLite adapter (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.honey_store uv run --frozen pytest -p no:cacheprovider \
    --cov=hivemind.honey_store --cov-report=term-missing packages/hivemind/tests/unit/honey_store \
    packages/hivemind/tests/contracts/test_honey_store_contract.py
```
