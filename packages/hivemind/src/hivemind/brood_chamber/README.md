# hivemind.brood_chamber

The brood_chamber package is the Brood Chamber, the Hive's task store: the task graph, the task
state machine and its persistence in SQLite. Every task the Queen decomposes a goal into lives
here for the rest of its life.

## Errors and questions (phase 2 steps 2.4, model half of 2.7)

- `errors.py` -- `BroodChamberError` and its subclasses: `TaskNotFoundError`,
  `QuestionNotFoundError`, `TaskAlreadyExistsError`, `InvalidTransitionError` (shared by both
  state machines below), `InvalidGraphError`.
- `questions.py` -- `Question` and `Answer`, plus their own transition table
  (`QUESTION_TRANSITIONS`, `assert_question_transition`). Appendix C's "Question" row.

## `task/` -- the task model, state machine and graph (phase 2 steps 2.4, 2.5)

A package because a third file (`graph.py`) joined the model and its state machine (codingrules
5.2). `task/__init__.py` is its face: a caller writes `from hivemind.brood_chamber.task import
Task` without knowing the split.

- `task/state.py` -- `TaskStatus` and the one transition table (`TRANSITIONS`) a task's `status`
  moves through, plus `can_transition`, `assert_transition` and `is_terminal`. Appendix C's "Task"
  row.
- `task/model.py` -- `TaskSpec` (the brief), `TaskOutcome` (how it ended) and `Task` (the full
  record), plus `TaskDraft`/`TaskGraphDraft`, the JSON graph file a human hands to
  `hive tasks submit`.
- `task/graph.py` -- pure functions over a task graph: `is_acyclic_edges` (generic, used by
  `TaskGraphDraft`'s own validator), `is_acyclic`, `ready_tasks`, `descendants`. No I/O.

## `store/` -- the TaskStore protocol and its two implementations (phase 2 step 2.6, store half of 2.7)

A package because two implementations plus their own migration series pushed the store past a
single file (codingrules 5.2). `store/__init__.py` is its face: a caller writes `from
hivemind.brood_chamber.store import TaskStore` without knowing the split.

- `store/protocol.py` -- `TaskFilter` (`list_tasks`'s query shape: status, goal_id, limit), the
  `TaskStore` Protocol (eight async methods: `insert_tasks`, `update_task`, `get_task`,
  `list_tasks`, `insert_question`, `update_question`, `get_question`, `list_questions`), and
  `check_task_event(task, event)`, the guard both implementations call before every write so a
  `TaskEvent` can never be filed under the wrong task. Every mutation method records its
  `hivemind.pheromone.TaskEvent`(s) in the same transaction as the state change (Appendix C rule
  3): the trail can never disagree with the store.
- `store/memory.py` -- `MemoryTaskStore(trail)`: two dicts (tasks by id, questions by id) behind
  an `asyncio.Lock`, for tests and demos. A mutation validates, records its event(s) on `trail`,
  and only then swaps in the new dict values, so a failed `record` (e.g. `DuplicateEventError`)
  leaves the dicts unchanged.
- `store/sqlite.py` -- `SqliteTaskStore(connection)` with `@classmethod async
  create(connection, clock)`, built on two tables (`tasks`, `questions`;
  `hivemind.brood_chamber.store.migrations`). `create` refuses to proceed (`MigrationError`)
  unless `pheromone_events` already exists on the connection's database, so a Brood Chamber never
  writes a `task.*` event into a table the Pheromone Trail has not created yet. Every mutation
  runs one transaction under `asyncio.to_thread` that writes the task/question row(s) and calls
  `hivemind.pheromone.insert_event` for the event, so both commit or neither does.
- `store/migrations/` -- the numbered SQL migration series `store/sqlite.py` applies
  (`0001_create_tasks.sql`); a real Python package (an `__init__.py`, however empty) because
  `importlib.resources` addresses it by dotted name.

## `chamber/` -- the public facade (phase 2 step 2.8)

A package because the whole class broke the codingrules 5.1 200-line class limit as one file.
`chamber/base.py` holds `ChamberIdentity` (the hive, node and actor every `TaskEvent` this facade
writes is stamped with) and the private `_ChamberBase` every mixin below inherits (the
store/clock/identity plus `_build_event`, `_write`, `_transition`). `chamber/submission.py`
(`submit`), `chamber/lifecycle.py` (`assign`, `unassign`, `start`, `report_progress`, `pause`,
`resume`), `chamber/outcomes.py` (`complete`, `fail`, `cancel`) and `chamber/questions.py` (`ask`,
`answer`, `withdraw`) are one mixin each, split by responsibility; `chamber/queries.py` holds the
four read-only methods (`get`, `list`, `next_ready`, `pending_questions`). `chamber/__init__.py`
composes `BroodChamber` from all five mixins and re-exports it with `ChamberIdentity`; every other
module in the package is private. Every mutating method loads the current `Task`/`Question`,
asserts the move is legal against `task/state.py`'s `TRANSITIONS` or `questions.py`'s
`QUESTION_TRANSITIONS`, builds the new value with `model_copy`, and writes it with its `TaskEvent`
(payload: ids, reasons, statuses and counts only, never a task's objective or a question's text)
through `TaskStore` in one call.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/brood_chamber packages/hivemind/tests/contracts \
    --cov=hivemind.brood_chamber --cov-report=term-missing
```

`tests/unit/brood_chamber/` mirrors this layout: `task/` (`test_model.py`, `test_state.py`,
`test_graph.py`), `store/` (`test_protocol.py`, `test_memory.py`, `test_sqlite.py`), `chamber/`
(one file per mixin plus `test_base.py`), and `test_errors.py`/`test_questions.py` for the two
modules that stayed flat.

Test-data builders (`make_task`, `make_task_spec`, `make_outcome`, `make_question`, `make_answer`,
`make_graph_draft`) live in `packages/hivemind/tests/builders/tasks.py` (codingrules 14.5); import
them as `from builders.tasks import make_task`. `tests/contracts/test_task_store_contract.py`
parametrises one behavioural suite over `MemoryTaskStore` (over `MemoryPheromoneTrail`) and
`SqliteTaskStore` (over `SqlitePheromoneTrail`, both on the same `tmp_path` SQLite file); every
test builds events with `TaskEvent(...)` and `waggle.ids.new_event_id`, never wall-clock time.
