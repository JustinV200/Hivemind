# hivemind.brood_chamber

The brood_chamber package is the Brood Chamber, the Hive's task store: the task graph, the task
state machine and its persistence in SQLite. Every task the Queen decomposes a goal into lives
here for the rest of its life.

## Modules (phase 2 steps 2.4, 2.5, model half of 2.7)

- `errors.py` -- `BroodChamberError` and its subclasses: `TaskNotFoundError`,
  `QuestionNotFoundError`, `TaskAlreadyExistsError`, `InvalidTransitionError` (shared by both
  state machines below), `InvalidGraphError`.
- `task_state.py` -- `TaskStatus` and the one transition table (`TRANSITIONS`) a task's `status`
  moves through, plus `can_transition`, `assert_transition` and `is_terminal`. Appendix C's "Task"
  row.
- `task.py` -- `TaskSpec` (the brief), `TaskOutcome` (how it ended) and `Task` (the full record),
  plus `TaskDraft`/`TaskGraphDraft`, the JSON graph file a human hands to `hive tasks submit`.
- `graph.py` -- pure functions over a task graph: `is_acyclic_edges` (generic, used by
  `TaskGraphDraft`'s own validator), `is_acyclic`, `ready_tasks`, `descendants`. No I/O.
- `questions.py` -- `Question` and `Answer`, plus their own transition table
  (`QUESTION_TRANSITIONS`, `assert_question_transition`). Appendix C's "Question" row.

## Store (phase 2 step 2.6, and the store half of 2.7)

- `store.py` -- `TaskFilter` (`list_tasks`'s query shape: status, goal_id, limit), the `TaskStore`
  Protocol (eight async methods: `insert_tasks`, `update_task`, `get_task`, `list_tasks`,
  `insert_question`, `update_question`, `get_question`, `list_questions`), and
  `check_task_event(task, event)`, the guard both implementations call before every write so a
  `TaskEvent` can never be filed under the wrong task. Every mutation method records its
  `hivemind.pheromone.TaskEvent`(s) in the same transaction as the state change (Appendix C rule
  3): the trail can never disagree with the store.
- `memory.py` -- `MemoryTaskStore(trail)`: two dicts (tasks by id, questions by id) behind an
  `asyncio.Lock`, for tests and demos. A mutation validates, records its event(s) on `trail`, and
  only then swaps in the new dict values, so a failed `record` (e.g. `DuplicateEventError`) leaves
  the dicts unchanged.
- `sqlite.py` -- `SqliteTaskStore(connection)` with `@classmethod async create(connection, clock)`,
  built on two tables (`tasks`, `questions`; `hivemind.brood_chamber.migrations`). `create` refuses
  to proceed (`MigrationError`) unless `pheromone_events` already exists on the connection's
  database, so a Brood Chamber never writes a `task.*` event into a table the Pheromone Trail has
  not created yet. Every mutation runs one transaction under `asyncio.to_thread` that writes the
  task/question row(s) and calls `hivemind.pheromone.insert_event` for the event, so both commit
  or neither does.

Facade: a later step (2.8 `chamber.py`).

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/brood_chamber packages/hivemind/tests/contracts \
    --cov=hivemind.brood_chamber --cov-report=term-missing
```

Test-data builders (`make_task`, `make_task_spec`, `make_outcome`, `make_question`, `make_answer`,
`make_graph_draft`) live in `packages/hivemind/tests/builders/tasks.py` (codingrules 14.5); import
them as `from builders.tasks import make_task`. `tests/contracts/test_task_store_contract.py`
parametrises one behavioural suite over `MemoryTaskStore` (over `MemoryPheromoneTrail`) and
`SqliteTaskStore` (over `SqlitePheromoneTrail`, both on the same `tmp_path` SQLite file); every
test builds events with `TaskEvent(...)` and `waggle.ids.new_event_id`, never wall-clock time.
