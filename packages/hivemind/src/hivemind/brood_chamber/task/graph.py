"""Define pure functions over a task graph: cycle detection, readiness, and descendants.

A task graph is a set of Tasks (`hivemind.brood_chamber.task.model.Task`) linked by
`TaskSpec.depends_on`: a task lists the ids of the tasks it needs finished first. This module holds
the three questions the rest of the Hive asks of that graph, as pure functions over plain data
(codingrules section 8.3: "Decision logic ... is written as pure functions over plain data"), with
no store, no I/O, and no knowledge of how a graph got the shape it has: `is_acyclic_edges` (and
`is_acyclic`, its `Task`-shaped wrapper) checks a graph has no dependency cycle, `ready_tasks` picks
the PENDING tasks every one of whose dependencies has already SUCCEEDED, and `descendants` finds
every task that transitively depends on a given one, so cancelling or blocking it can be understood
to affect them too.

`is_acyclic_edges` is generic over a type parameter bound to `Hashable` (PEP 695 syntax) rather
than fixed to `TaskId`, because `hivemind.brood_chamber.task.model.TaskGraphDraft` (a submission
before any `TaskId` has been minted) needs the same cycle check over its own string keys; that is
also why this module never imports `hivemind.brood_chamber.task.model` at the top level (see Key
invariants). The DFS is written iteratively with an explicit stack, never recursively:
`MAX_GRAPH_TASKS` (`hivemind.brood_chamber.task.model`) allows graphs up to 256 tasks, comfortably
inside Python's default recursion limit, but a pure function with no I/O has no good reason to
depend on the caller's stack depth at all, so the iterative form is used regardless.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by
    `hivemind.brood_chamber.task.model.TaskGraphDraft` (`is_acyclic_edges`, over draft keys) and by
    `hivemind.brood_chamber.chamber` (roadmap step 2.8: `ready_tasks` for `next_ready`,
    `descendants` when cancelling a goal). Calls into `hivemind.brood_chamber.task.state` only, for
    `TaskStatus`.

Key invariants:
    - This module never imports `hivemind.brood_chamber.task.model` except under `TYPE_CHECKING`:
      `Task` is used only in type hints (`is_acyclic`, `ready_tasks`, `descendants` all take
      `Iterable[Task]` but only ever touch `.id`, `.status` and `.spec.depends_on` at runtime, so
      no import is needed to run). `hivemind.brood_chamber.task.model` imports this module for
      real (to call `is_acyclic_edges` from `TaskGraphDraft`'s validator); keeping the `Task`
      import type-checking-only here is what keeps that a one-way runtime dependency instead of a
      cycle.
    - is_acyclic_edges never recurses: depth is handled by an explicit list used as a stack, so a
      256-task graph (or a deliberately pathological one) can never hit Python's recursion limit.
    - An id that appears as a dependency but names no node in `edges` is treated as a leaf (no
      out-edges of its own), never as an error: `is_acyclic_edges` answers "is this acyclic", not
      "is this a graph over a closed vertex set".

See Also:
    - .claude/codingrules.md section 8.3 for the pure-core rule this module follows.
    - .claude/codingrules.md section 14.3 for the hypothesis property tests this module needs.
    - hivemind.brood_chamber.task.model for Task and TaskGraphDraft, the two callers of this
      module.
    - hivemind.brood_chamber.task.state for TaskStatus, read by ready_tasks.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Iterator, Mapping
from enum import Enum
from typing import TYPE_CHECKING

from hivemind.brood_chamber.task.state import TaskStatus

if TYPE_CHECKING:
    # Type-only: see the module docstring's Key invariants for why this is never a runtime import.
    from hivemind.brood_chamber.task.model import Task
    from waggle.ids import TaskId

__all__ = ["descendants", "is_acyclic", "is_acyclic_edges", "ready_tasks"]


class _Colour(Enum):
    """Iterative-DFS visitation state, the standard three-colour cycle-detection scheme."""

    WHITE = "WHITE"  # Not yet visited.
    GREY = "GREY"  # On the current DFS stack; an edge into a GREY node is a back-edge (a cycle).
    BLACK = "BLACK"  # Fully explored; every reachable node from here is already coloured.


def is_acyclic_edges[K: Hashable](edges: Mapping[K, Iterable[K]]) -> bool:
    """Return whether `edges` (an adjacency mapping) has no dependency cycle.

    Args:
        edges: Node id to the ids it depends on (its out-edges). A value naming an id that is not
            itself a key in `edges` is allowed: that id is treated as a leaf with no out-edges of
            its own (see the module docstring's Key invariants).

    Returns:
        True if a depth-first walk from every node finds no edge back to a node still on the
        current path; False the first time it finds one.
    """
    # WHITE for every declared node; a dependency that is not itself a key gets coloured lazily
    # the first time _dfs_from reaches it, still starting from WHITE (a leaf, by definition).
    colour: dict[K, _Colour] = dict.fromkeys(edges, _Colour.WHITE)
    for node in edges:
        # Only start a fresh walk from a node no earlier walk has already fully explored.
        if colour[node] is _Colour.WHITE and not _dfs_from(node, edges, colour):
            return False
    return True


def is_acyclic(tasks: Iterable[Task]) -> bool:
    """Return whether the `depends_on` edges among `tasks` form no cycle.

    Args:
        tasks: The tasks to check. A dependency id naming a task not in `tasks` is treated as a
            leaf (see `is_acyclic_edges`).

    Returns:
        True if the tasks' dependency edges have no cycle.
    """
    # Build the plain adjacency mapping is_acyclic_edges expects, keyed by TaskId.
    edges = {task.id: task.spec.depends_on for task in tasks}
    return is_acyclic_edges(edges)


def ready_tasks(tasks: Iterable[Task]) -> tuple[Task, ...]:
    """Return the PENDING tasks every one of whose dependencies has already SUCCEEDED.

    Args:
        tasks: The tasks to consider, in any order.

    Returns:
        The ready tasks, ordered by `(created_at, id)` so the result is deterministic even when
        two tasks were created in the same instant.
    """
    all_tasks = tuple(tasks)
    # A dependency counts only when it is both present in this set and SUCCEEDED; .get returns
    # None for an absent id, which is never equal to SUCCEEDED, so "present and SUCCEEDED"
    # collapses into this one comparison per dependency.
    status_by_id = {task.id: task.status for task in all_tasks}
    ready = [
        task
        for task in all_tasks
        if task.status is TaskStatus.PENDING
        and all(status_by_id.get(dep) is TaskStatus.SUCCEEDED for dep in task.spec.depends_on)
    ]
    ready.sort(key=lambda task: (task.created_at, task.id))
    return tuple(ready)


def descendants(tasks: Iterable[Task], task_id: TaskId) -> frozenset[TaskId]:
    """Return every task that transitively depends on `task_id`.

    Args:
        tasks: The tasks to search.
        task_id: The task whose dependents (direct and indirect) to find.

    Returns:
        The transitive dependents of `task_id`, empty if `task_id` is unknown or nothing depends
        on it. `task_id` itself is never included.
    """
    # Invert depends_on into "who depends on me" once, then walk it breadth-first from task_id;
    # cheaper than re-scanning `tasks` per level for anything but a tiny graph.
    dependents: dict[TaskId, set[TaskId]] = {}
    for task in tasks:
        for dep in task.spec.depends_on:
            dependents.setdefault(dep, set()).add(task.id)

    found: set[TaskId] = set()
    frontier = [task_id]
    while frontier:
        current = frontier.pop()
        for dependent in dependents.get(current, ()):
            # Only queue a dependent the first time it is found, so a diamond-shaped graph
            # (two paths to the same descendant) never loops or double-counts.
            if dependent not in found:
                found.add(dependent)
                frontier.append(dependent)
    return frozenset(found)


def _dfs_from[K: Hashable](
    start: K, edges: Mapping[K, Iterable[K]], colour: dict[K, _Colour]
) -> bool:
    """Walk from `start` iteratively; return False the instant a back-edge closes a cycle.

    The stack holds `(node, remaining-children-iterator)` pairs so the walk can resume exactly
    where it left off after descending into a child, without ever recursing.
    """
    stack: list[tuple[K, Iterator[K]]] = [(start, iter(edges.get(start, ())))]
    colour[start] = _Colour.GREY
    while stack:
        node, children = stack[-1]
        descended = False
        for child in children:
            child_colour = colour.get(child, _Colour.WHITE)
            # A GREY child is on the current path: the edge to it closes a cycle.
            if child_colour is _Colour.GREY:
                return False
            # A WHITE child is unexplored: descend into it and resume this node's iterator later.
            if child_colour is _Colour.WHITE:
                colour[child] = _Colour.GREY
                stack.append((child, iter(edges.get(child, ()))))
                descended = True
                break
        # No unexplored child was found: this node is fully explored, so pop it and mark it done.
        if not descended:
            colour[node] = _Colour.BLACK
            stack.pop()
    return True
