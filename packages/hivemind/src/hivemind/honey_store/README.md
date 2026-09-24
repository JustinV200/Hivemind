# hivemind.honey_store

The honey_store package is the Honey Store, the Hive's cold-tier knowledge base. Raw Nectar
(unprocessed captured information) is taken in, ripened through a pipeline into Honey
(retrievable, labelled knowledge), and retrieved by Workers before they act.

## Public API (roadmap 7.2/7.3)

- **Errors** (`errors.py`): `HoneyStoreError` (root), `NectarNotFoundError`/`HoneyNotFoundError`
  (a lookup by id found nothing), `NectarRejectedError` and its seven concrete reasons
  (`NectarTooLargeError`, `OffsetMismatchError`, `TooManyOpenDepositsError`,
  `Sha256MismatchError`, `FirstChunkNotAtZeroError`, `NightVeilRefusedError`,
  `DepositTimedOutError` -- each fixes `code` to one stable dotted string; `except
  NectarRejectedError:` catches any of them), `NectarNotRipenableError` (a Night Veil Cell's
  ephemeral side channel asked to ripen), `LabelLoweringError` (a lowering that is not lower, or
  has no approver), `InvalidScopeError` (a scope does not match
  `waggle.messages.honey.hit.SCOPE_PATTERN`, or carries a `/` or a glob character in its id).
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
  ripening); `Honey`/`HoneyDraft`/`HoneyPart` (ripened, retrievable knowledge, with `Honey.path`
  and `Honey.to_hit`); `ReadFilter`/`TextCandidate`/`VectorCandidate`/`HoneyStats` (the shapes a
  search and `HoneyStore.stats` pass around).
- **Schema** (`schema/`): `apply_honey_store_migrations` applies `migrations/
  0001_create_honey_store.sql` -- `honey_nectar`, `honey`, `honey_vectors`, `honey_watermarks`,
  `honey_proposals`, the external-content FTS5 index `honey_fts` and its three sync triggers.
- **Store** (`store/`): `HoneyStore`, the persistence protocol every implementation honours (and
  the contract later dispatches code against), plus its own `NectarAdded`/`HoneyProposal` return
  shapes and `NectarEvents` (how `add_nectar` builds its trail events from what it found: a new
  row, a duplicate, a raised label, or nothing for a Night Veil deposit); `build_match`, a safe
  FTS5 MATCH string from arbitrary text; `SqliteHoneyStore`, the durable implementation, split by
  table responsibility under `store/sqlite/` (`nectar.py`,
  `honey.py`, `vectors.py`, `search.py`, `stats.py`, `vec.py` for the sqlite-vec extension load
  and float32 codec, `filters.py` for `ReadFilter`'s shared SQL).

`nectar/` (intake), `ripening/` (the Ripener), `honey/` (retrieval) and `browse.py` (the read-only
folder tree) are later dispatches' work and still carry no public names.

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
