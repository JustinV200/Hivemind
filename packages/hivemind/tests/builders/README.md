# hivemind test-data builders

Small factory functions used across the other test trees to build valid test data without
repeating pydantic model boilerplate in every test: `make_task`, `make_cell_spec`, and similar.
Builders return real, validated models; they never bypass validation to save time.

Import them as `from builders.tasks import make_task` (this directory is on pytest's and mypy's
path; see the root `pyproject.toml`'s `pythonpath`/`mypy_path` comments for roadmap step 2.4).

## `tasks.py` (roadmap phase 2, brood_chamber)

`make_task_spec`, `make_outcome`, `make_task`, `make_question`, `make_answer`,
`make_graph_draft`: build `hivemind.brood_chamber`'s task and question models. Every builder takes
an optional `clock: waggle.clock.Clock` (default a fresh `FakeClock`) so minted ids and timestamps
are deterministic; `make_task(status=...)` fills in whatever placement, outcome or pending-question
fields that status requires, so any `TaskStatus` is valid on its own with no further overrides.

## `honey.py` and `honey_wire.py` (roadmap phase 7, honey_store)

`honey.py` builds the Honey Store's own data and stores: `make_nectar_draft`, `make_honey_draft`,
`make_nectar`, `make_nectar_submission`, `make_nectar_deposit`/`make_deposit_chunks` (a chunked
Waggle deposit), `make_honey_identity`, `make_ripener_deps`, and `open_test_honey_store` /
`open_test_honey_store_with_trail` (a real temp-file SQLite store, the Pheromone Trail migrated
first). `make_nectar_draft`'s default content is fixed, and the store dedupes by content digest:
override `content` when a test needs distinct rows. `honey_wire.py` builds what crosses Waggle for
it: `make_honey_access` (a whole `HoneyAccess` over one store), `make_honey_link` (a `WardenLink`
for a given Cell), `make_honey_query`, `make_honey_hit`, `make_deposit_meta`, and `seed_finding`
(one ripened finding a query can find).
