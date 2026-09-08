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
