"""Define TaintScope: which memory items one taint covers, by author, task and time.

The two callers roadmap 10.6 builds next each taint a slice of memory, not one item: quarantine
(10.6c) taints "every checkpoint, Handoff and Nectar of this bee from the suspect episode on", and
isolation (10.6a) taints "every item from this Cell from the report's first cited event on". A
`TaintScope` is that slice as data: the bees whose items it covers (a Handoff's `written_by`, an
episode record's `principal`), the tasks whose items it covers (a Handoff's or a Bee Bread entry's
task), the moment from which items count, and which kinds of item it reaches. The memory tables
carry no Cell id, so a Cell's scope is named by the bees that ran on it and the tasks placed on it,
which the Queen already knows from the Brood Chamber and the trail; `for_cell` takes those.
`covers` is the one predicate every ledger applies, so the in-memory and the SQLite stores (and the
Honey Store in phase 7) can never disagree about what a scope reaches.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.taint`. Built by
    the taint's callers (`queen/isolation.py`, the quarantine path in `wardens/`) and passed to
    `hivemind.memory.taint.set.taint_memory`; read by every `TaintLedger.find_taintable`. Calls into
    this package's `marker` and waggle only.

Key invariants:
    - A scope names at least one author or task: an empty scope would taint nothing, which is a
      caller's bug, not a request.
    - "From E on" is inclusive and at millisecond resolution (an id's ULID time): an item written
      in the same millisecond as E is covered, never missed.

See Also:
    - hivemind.memory.taint.set for taint_memory, the one caller of a ledger with a scope.
    - hivemind.memory.taint.ledger for TaintLedger.find_taintable.
    - waggle.ids.timestamp_of for how an id becomes the scope's starting moment.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.memory.taint.marker import TaintedKind
from waggle.ids import TaskId, timestamp_of
from waggle.messages.base import TaskIdField, UtcDatetime

MAX_SCOPE_AUTHORS = 256  # Every bee one Cell ever ran, with room to spare.
MAX_SCOPE_TASKS = 1_024  # Every task one Cell ever held, with room to spare.

__all__ = ["MAX_SCOPE_AUTHORS", "MAX_SCOPE_TASKS", "TaintScope"]


class TaintScope(BaseModel):
    """Which memory items one taint covers: by who wrote them, which task, and from when.

    An item is covered when its kind is in `kinds`, it was written at or after `since`, and either
    its author is in `authors` or its task is in `task_ids`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authors: frozenset[str] = Field(
        default_factory=frozenset,
        max_length=MAX_SCOPE_AUTHORS,
        description="Bee ids whose own items are covered: a Handoff's written_by, an episode "
        "record's principal.",
    )
    task_ids: frozenset[TaskIdField] = Field(
        default_factory=frozenset,
        max_length=MAX_SCOPE_TASKS,
        description="Tasks whose items are covered: a Handoff's task and a Bee Bread entry's.",
    )
    since: UtcDatetime = Field(description="Items written at or after this are covered.")
    kinds: frozenset[TaintedKind] = Field(
        default_factory=lambda: frozenset(TaintedKind),
        description="Which kinds of item the scope reaches; every kind by default.",
    )

    @model_validator(mode="after")
    def _names_something(self) -> TaintScope:
        """Refuse a scope with neither an author nor a task: it would taint nothing."""
        if not self.authors and not self.task_ids:
            raise ValueError("a taint scope names at least one author or one task.")
        return self

    @classmethod
    def for_bee(cls, bee_id: str, since_id: str, task_ids: Iterable[TaskId] = ()) -> TaintScope:
        """Cover every item of one bee (and its tasks) from one episode or event on.

        The quarantine shape (roadmap 10.6c): the bee's own Handoffs and episode records, and,
        when its task is named, that task's checkpoint deposits too.

        Args:
            bee_id: The suspect bee's own id (a worker_ or warden_ id).
            since_id: The episode id (or any event id) from which its memory is suspect.
            task_ids: The tasks the bee worked, so their Bee Bread deposits are covered too.

        Returns:
            The scope.
        """
        return cls(
            authors=frozenset({bee_id}), task_ids=frozenset(task_ids), since=timestamp_of(since_id)
        )

    @classmethod
    def for_cell(
        cls, authors: Iterable[str], task_ids: Iterable[TaskId], since_id: str
    ) -> TaintScope:
        """Cover every item from one Cell from one event on: its bees' items and its tasks'.

        The isolation shape (roadmap 10.6a): the memory tables carry no Cell id, so the Cell is
        named by the Warden and sub-bees that ran on it and the tasks placed there.

        Args:
            authors: Every bee id that ran on the Cell.
            task_ids: Every task placed on the Cell.
            since_id: The first trail event the Guard report cited.

        Returns:
            The scope.
        """
        return cls(
            authors=frozenset(authors), task_ids=frozenset(task_ids), since=timestamp_of(since_id)
        )

    def covers(
        self, kind: TaintedKind, author: str | None, task_id: str | None, at: datetime
    ) -> bool:
        """Return whether an item with these facts falls inside this scope.

        Args:
            kind: The item's kind.
            author: Who wrote it, when the item records that (None for a Bee Bread entry).
            task_id: The task it concerns, when it has one.
            at: When it was written.

        Returns:
            True when the kind is reached, it was written at or after `since`, and its author or
            its task is named.
        """
        named = (author is not None and author in self.authors) or (
            task_id is not None and task_id in self.task_ids
        )
        return kind in self.kinds and at >= self.since and named
