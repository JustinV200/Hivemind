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

Store and facade: later steps (2.6 `store.py`/`sqlite.py`/`memory.py`, 2.8 `chamber.py`).

## How to test this

`uv run --frozen pytest packages/hivemind/tests/unit/brood_chamber --cov=hivemind.brood_chamber
--cov-report=term-missing`. Test-data builders (`make_task`, `make_task_spec`, `make_outcome`,
`make_question`, `make_answer`, `make_graph_draft`) live in
`packages/hivemind/tests/builders/tasks.py` (codingrules 14.5); import them as
`from builders.tasks import make_task`.
