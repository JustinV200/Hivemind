"""Provide SqliteTaskStore, the durable TaskStore, and its two-table schema.

The Brood Chamber's durable home is two tables, `tasks` and `questions`, in the Hive's single
SQLite file (ADR-0006). This module owns both end to end: the migration that creates them
(`hivemind.brood_chamber.store.migrations`), and `SqliteTaskStore`, the `hivemind.brood_chamber.
store.protocol.TaskStore` implementation built on them. Every mutation runs one transaction under
`asyncio.to_thread` that writes the task and/or question row(s) and calls `hivemind.pheromone.
insert_event` for the accompanying event on the same connection, so the state change and its trail
event commit together (codingrules section 12's "same transaction" rule, Appendix C rule 3) --
exactly the pattern `hivemind.pheromone.trail.sqlite.insert_event`'s own docstring names the Brood
Chamber as the first caller of.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Constructed by a composition root
    (`hivemind.cli.stores.open_chamber`, roadmap step 2.9) once the Hive Manifest names the
    database file; used by `hivemind.brood_chamber.chamber` (roadmap step 2.8). Calls into
    hivemind.common (connect, transaction, migrations, errors), hivemind.brood_chamber
    (store.protocol, task.model, questions, errors) and hivemind.pheromone (insert_event) only.

Key invariants:
    - `create` refuses to proceed unless `pheromone_events` already exists on `connection`'s
      database, so a Brood Chamber that writes trail events before the Pheromone Trail's own
      migration has run fails loudly (`MigrationError`) instead of writing an event into a table
      that is never read.
    - Every SQLite call runs under `asyncio.to_thread`, one whole transaction per hop, serialised
      by this instance's own `asyncio.Lock` (codingrules section 11).
    - A task or question row and the event(s) that accompany it are written inside one
      `hivemind.common.sqlite.transaction` block, so a failure partway through (a duplicate id, a
      missing row, a duplicate event id) rolls back every write that call made, event included.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md and docs/adr/0007-pheromone-trail-append-
      only-transactional-and-segmented.md for the decisions this module follows.
    - hivemind.common.sqlite and hivemind.common.migrations for connect/transaction and the
      migration runner this module builds on.
    - hivemind.brood_chamber.store.protocol for the TaskStore protocol and check_task_event, the
      guard every mutation calls before writing.
    - hivemind.pheromone.trail.sqlite for insert_event, the primitive this module calls inside its
      own transactions, and the SQLite adapter this module's shape mirrors.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from collections.abc import Sequence

from hivemind.brood_chamber.errors import (
    QuestionNotFoundError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from hivemind.brood_chamber.questions import Question, QuestionStatus
from hivemind.brood_chamber.store.protocol import TaskFilter, check_task_event
from hivemind.brood_chamber.task.model import Task
from hivemind.common.errors import ConflictError, InvariantViolationError, MigrationError
from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import transaction
from hivemind.pheromone import TaskEvent, insert_event
from waggle.clock import Clock
from waggle.ids import MessageId, TaskId

SUBSYSTEM = "brood_chamber"  # Keys this subsystem's rows in the shared schema_migrations table.
# Dotted package path importlib.resources.files() reads the numbered .sql files from; a string,
# not a direct package import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.brood_chamber.store.migrations"

# create()'s loud-failure check: the Brood Chamber must never be the first thing to touch a fresh
# database file, or its own migration would run before the pheromone_events table insert_event
# writes into exists.
_PHEROMONE_TABLE_CHECK_SQL = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pheromone_events'"
)

_INSERT_TASK_SQL = """
INSERT INTO tasks (id, goal_id, status, created_at, updated_at, body)
VALUES (?, ?, ?, ?, ?, ?)
"""
_UPDATE_TASK_SQL = "UPDATE tasks SET goal_id = ?, status = ?, updated_at = ?, body = ? WHERE id = ?"
_SELECT_TASK_BODY_SQL = "SELECT body FROM tasks WHERE id = ?"
_SELECT_TASKS_BODY_SQL = "SELECT body FROM tasks"
_ORDER_TASKS_BY = " ORDER BY created_at, id"  # Matches TaskStore.list_tasks's documented order.

_INSERT_QUESTION_SQL = (
    "INSERT INTO questions (id, task_id, status, asked_at, body) VALUES (?, ?, ?, ?, ?)"
)
_UPDATE_QUESTION_SQL = "UPDATE questions SET status = ?, body = ? WHERE id = ?"
_SELECT_QUESTION_BODY_SQL = "SELECT body FROM questions WHERE id = ?"
_SELECT_QUESTIONS_BODY_SQL = "SELECT body FROM questions"
_ORDER_QUESTIONS_BY = (
    " ORDER BY asked_at, id"  # Matches TaskStore.list_questions's documented order.
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "SqliteTaskStore",
    "apply_brood_chamber_migrations",
]


def apply_brood_chamber_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.brood_chamber.store.migrations`.

    Synchronous, like every function `hivemind.common.migrations` exports; `SqliteTaskStore.
    create` is the one caller, and it runs this under `asyncio.to_thread`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`, already carrying
            the `pheromone_events` table.
        clock: Injected clock; each applied migration's `applied_at` comes from it.

    Returns:
        The migration versions actually applied by this call, ascending; empty when the schema
        was already current.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteTaskStore:
    """The durable TaskStore: two SQLite tables, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has `tasks` and `questions`
                (normally produced by `create`, which applies the migration first).
        """
        self._connection = connection
        # Serialises every method on this instance, matching hivemind.pheromone.trail.sqlite.
        # SqlitePheromoneTrail's own lock: asyncio.to_thread may run each call on a different
        # worker thread, and sqlite3 connections are not safe for two threads to issue statements
        # on at once.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteTaskStore:
        """Check for the Pheromone Trail's table, apply the Brood Chamber's migrations, and wrap.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`. A composition
                root applies the Pheromone Trail's own migrations on its connection to the same
                file before calling this.
            clock: Injected clock, used for migration timestamps.

        Returns:
            A SqliteTaskStore whose `tasks` and `questions` tables exist and are current.

        Raises:
            MigrationError: `connection`'s database has no `pheromone_events` table yet: the
                Pheromone Trail's migrations must run on this file first, or a task's `task.*`
                event would be written into a table that does not exist.
        """
        # Blocking: a single indexed lookup against sqlite_master; sub-millisecond.
        has_pheromone_table = await asyncio.to_thread(_pheromone_table_exists, connection)
        if not has_pheromone_table:
            raise MigrationError(
                "cannot apply brood_chamber migrations: no pheromone_events table on this "
                "connection; call hivemind.pheromone.trail.sqlite.apply_pheromone_migrations (or "
                "SqlitePheromoneTrail.create) on this database file first."
            )
        # Blocking: at most one transaction per pending migration (usually zero, once current).
        await asyncio.to_thread(apply_brood_chamber_migrations, connection, clock)
        return cls(connection)

    async def insert_tasks(self, tasks: Sequence[Task], events: Sequence[TaskEvent]) -> None:
        """Insert every task in `tasks`, each with its own event; see TaskStore.insert_tasks."""
        if len(tasks) != len(events):
            raise InvariantViolationError(
                f"insert_tasks got {len(tasks)} tasks but {len(events)} events; they must match."
            )
        for task, event in zip(tasks, events, strict=True):
            check_task_event(task, event)
        async with self._lock:
            # Blocking: len(tasks) task inserts plus len(events) event inserts, one transaction.
            await asyncio.to_thread(_insert_tasks_transaction, self._connection, tasks, events)

    async def update_task(self, task: Task, event: TaskEvent) -> None:
        """Replace the stored task and record `event`; see TaskStore.update_task."""
        check_task_event(task, event)
        async with self._lock:
            # Blocking: one UPDATE plus one event insert, in one transaction.
            await asyncio.to_thread(_update_task_transaction, self._connection, task, event)

    async def get_task(self, task_id: TaskId) -> Task:
        """Return the stored task with id `task_id`; see TaskStore.get_task."""
        async with self._lock:
            # Blocking: one indexed SELECT by primary key.
            row = await asyncio.to_thread(_select_task_row, self._connection, task_id)
        if row is None:
            raise TaskNotFoundError(task_id)
        return Task.model_validate_json(row["body"])

    async def list_tasks(self, query: TaskFilter) -> tuple[Task, ...]:
        """Return every task matching `query`, ordered by (created_at, id); see TaskStore."""
        async with self._lock:
            # Blocking: one indexed SELECT bounded by query.limit.
            rows = await asyncio.to_thread(_select_tasks_rows, self._connection, query)
        return tuple(Task.model_validate_json(row["body"]) for row in rows)

    async def insert_question(self, task: Task, question: Question, event: TaskEvent) -> None:
        """Write `task`, `question` and `event` together; see TaskStore.insert_question."""
        check_task_event(task, event)
        async with self._lock:
            # Blocking: one task UPDATE, one question INSERT, one event insert, one transaction.
            await asyncio.to_thread(
                _insert_question_transaction, self._connection, task, question, event
            )

    async def update_question(self, task: Task, question: Question, event: TaskEvent) -> None:
        """Write `task` and `question`'s new values with `event`; see TaskStore.update_question."""
        check_task_event(task, event)
        async with self._lock:
            # Blocking: one task UPDATE, one question UPDATE, one event insert, one transaction.
            await asyncio.to_thread(
                _update_question_transaction, self._connection, task, question, event
            )

    async def get_question(self, question_id: MessageId) -> Question:
        """Return the stored question with id `question_id`; see TaskStore.get_question."""
        async with self._lock:
            # Blocking: one indexed SELECT by primary key.
            row = await asyncio.to_thread(_select_question_row, self._connection, question_id)
        if row is None:
            raise QuestionNotFoundError(question_id)
        return Question.model_validate_json(row["body"])

    async def list_questions(
        self, task_id: TaskId | None, status: QuestionStatus | None
    ) -> tuple[Question, ...]:
        """Return every question matching the filters, ordered by (asked_at, id); see TaskStore."""
        async with self._lock:
            # Blocking: one indexed SELECT, unbounded (the Brood Chamber never holds enough
            # questions at once for this to matter; TaskFilter's limit has no counterpart here).
            rows = await asyncio.to_thread(
                _select_questions_rows, self._connection, task_id, status
            )
        return tuple(Question.model_validate_json(row["body"]) for row in rows)


def _pheromone_table_exists(connection: sqlite3.Connection) -> bool:
    """Return whether `connection`'s database already has a `pheromone_events` table."""
    return connection.execute(_PHEROMONE_TABLE_CHECK_SQL).fetchone() is not None


def _task_row(task: Task) -> tuple[str, str, str, str, str, str]:
    """Build the six-column parametrised row both task insert and update statements bind."""
    return (
        task.id,
        task.goal_id,
        task.status.value,
        task.created_at.isoformat(),
        task.updated_at.isoformat(),
        task.model_dump_json(),
    )


def _question_row(question: Question) -> tuple[str, str, str, str, str]:
    """Build the five-column parametrised row the question insert statement binds."""
    return (
        question.id,
        question.task_id,
        question.status.value,
        question.asked_at.isoformat(),
        question.model_dump_json(),
    )


def _insert_tasks_transaction(
    connection: sqlite3.Connection, tasks: Sequence[Task], events: Sequence[TaskEvent]
) -> None:
    """Insert every task then every event, in one transaction; sync body run under to_thread."""
    with transaction(connection):
        for task in tasks:
            try:
                connection.execute(_INSERT_TASK_SQL, _task_row(task))
            except sqlite3.IntegrityError as exc:
                # The failing INSERT is still inside this transaction: raising here rolls back
                # every task and event this call already wrote, including any earlier in `tasks`.
                raise TaskAlreadyExistsError(task.id) from exc
        for event in events:
            insert_event(connection, event)


def _update_task_transaction(connection: sqlite3.Connection, task: Task, event: TaskEvent) -> None:
    """Update one task row then insert its event, in one transaction; run under to_thread."""
    with transaction(connection):
        cursor = connection.execute(
            _UPDATE_TASK_SQL,
            (
                task.goal_id,
                task.status.value,
                task.updated_at.isoformat(),
                task.model_dump_json(),
                task.id,
            ),
        )
        if cursor.rowcount != 1:
            # rowcount 0 means no row had this id: WHERE matched nothing to update.
            raise TaskNotFoundError(task.id)
        insert_event(connection, event)


def _select_task_row(connection: sqlite3.Connection, task_id: TaskId) -> sqlite3.Row | None:
    """Select one task's body row by id, or None when no row matches."""
    # sqlite3.Cursor.fetchone() is typed Any in typeshed; the explicit annotation is what
    # actually pins the return type down for mypy --strict, not the function signature alone.
    row: sqlite3.Row | None = connection.execute(_SELECT_TASK_BODY_SQL, (task_id,)).fetchone()
    return row


def _select_tasks_rows(connection: sqlite3.Connection, query: TaskFilter) -> list[sqlite3.Row]:
    """Build and run the filtered, ordered, limited SELECT `query` describes."""
    clauses, params = _task_query_where(query)
    sql = _SELECT_TASKS_BODY_SQL
    if clauses:
        # clauses holds only fixed column-name literals chosen from the branches in
        # _task_query_where below; every value is bound through "?" in params, never interpolated.
        sql = f"{sql} WHERE {' AND '.join(clauses)}"
    sql += _ORDER_TASKS_BY
    sql += " LIMIT ?"
    params.append(query.limit)
    return connection.execute(sql, params).fetchall()


def _task_query_where(query: TaskFilter) -> tuple[list[str], list[object]]:
    """Translate `query`'s set fields into WHERE clause fragments and their `?` parameters."""
    clauses: list[str] = []
    params: list[object] = []
    if query.status is not None:
        clauses.append("status = ?")
        params.append(query.status.value)
    if query.goal_id is not None:
        clauses.append("goal_id = ?")
        params.append(query.goal_id)
    return clauses, params


def _insert_question_transaction(
    connection: sqlite3.Connection, task: Task, question: Question, event: TaskEvent
) -> None:
    """Update the task, insert the question, then insert the event; run under to_thread."""
    with transaction(connection):
        cursor = connection.execute(
            _UPDATE_TASK_SQL,
            (
                task.goal_id,
                task.status.value,
                task.updated_at.isoformat(),
                task.model_dump_json(),
                task.id,
            ),
        )
        if cursor.rowcount != 1:
            raise TaskNotFoundError(task.id)
        try:
            connection.execute(_INSERT_QUESTION_SQL, _question_row(question))
        except sqlite3.IntegrityError as exc:
            # Rolls back the task UPDATE above too: insert_question is all-or-nothing.
            raise ConflictError(
                f"A question with id {question.id!r} already exists in the Brood Chamber."
            ) from exc
        insert_event(connection, event)


def _update_question_transaction(
    connection: sqlite3.Connection, task: Task, question: Question, event: TaskEvent
) -> None:
    """Update the task, update the question, then insert the event; run under to_thread."""
    with transaction(connection):
        task_cursor = connection.execute(
            _UPDATE_TASK_SQL,
            (
                task.goal_id,
                task.status.value,
                task.updated_at.isoformat(),
                task.model_dump_json(),
                task.id,
            ),
        )
        if task_cursor.rowcount != 1:
            raise TaskNotFoundError(task.id)
        question_cursor = connection.execute(
            _UPDATE_QUESTION_SQL, (question.status.value, question.model_dump_json(), question.id)
        )
        if question_cursor.rowcount != 1:
            raise QuestionNotFoundError(question.id)
        insert_event(connection, event)


def _select_question_row(
    connection: sqlite3.Connection, question_id: MessageId
) -> sqlite3.Row | None:
    """Select one question's body row by id, or None when no row matches."""
    # See _select_task_row's comment: the explicit annotation, not the signature, pins this down.
    row: sqlite3.Row | None = connection.execute(
        _SELECT_QUESTION_BODY_SQL, (question_id,)
    ).fetchone()
    return row


def _select_questions_rows(
    connection: sqlite3.Connection, task_id: TaskId | None, status: QuestionStatus | None
) -> list[sqlite3.Row]:
    """Select questions filtered by the given task_id/status, in (asked_at, id) order."""
    clauses: list[str] = []
    params: list[object] = []
    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status.value)
    sql = _SELECT_QUESTIONS_BODY_SQL
    if clauses:
        # clauses holds only fixed column-name literals chosen above; every value is bound
        # through "?" in params, never interpolated into sql itself.
        sql = f"{sql} WHERE {' AND '.join(clauses)}"
    sql += _ORDER_QUESTIONS_BY
    return connection.execute(sql, params).fetchall()
