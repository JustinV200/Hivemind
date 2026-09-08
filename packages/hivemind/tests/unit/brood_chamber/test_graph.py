"""Tests for hivemind.brood_chamber.graph: is_acyclic_edges, is_acyclic, ready_tasks, descendants.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/graph.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.graph for the module under test.
    - .claude/codingrules.md section 14.3 for the hypothesis property-test rule this module
      follows for the four pure functions under test.
"""

from __future__ import annotations

from collections.abc import Mapping

from builders.tasks import make_task, make_task_spec
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.brood_chamber.graph import descendants, is_acyclic, is_acyclic_edges, ready_tasks
from hivemind.brood_chamber.task import Task
from hivemind.brood_chamber.task_state import TaskStatus
from waggle.clock import FakeClock
from waggle.ids import TaskId, new_task_id

# codingrules 14.3: hypothesis property tests get a generous, deterministic example budget and no
# per-test deadline, since a slow CI host should never turn a correct test flaky.
_SETTINGS = settings(max_examples=100, deadline=None)


def _build_tasks(
    edges: Mapping[int, tuple[int, ...]], statuses: Mapping[int, TaskStatus], clock: FakeClock
) -> tuple[tuple[Task, ...], dict[int, TaskId]]:
    """Build real Tasks whose depends_on edges match `edges`, indexed the same way.

    Args:
        edges: Node index -> the indices it depends on (only lower indices, so the result is
            always acyclic; see _chained_dag_edges).
        statuses: Node index -> the status to build that Task with; PENDING when absent.
        clock: Shared clock every minted id and timestamp comes from.

    Returns:
        The built Tasks, and the index -> minted TaskId mapping used to wire depends_on.
    """
    ids = {index: new_task_id(clock) for index in edges}
    tasks = []
    for index, deps in edges.items():
        spec = make_task_spec(depends_on=tuple(ids[dep] for dep in deps))
        status = statuses.get(index, TaskStatus.PENDING)
        tasks.append(make_task(status=status, clock=clock, id=ids[index], spec=spec))
    return tuple(tasks), ids


@st.composite
def _chained_dag_edges(draw: st.DrawFn) -> dict[int, tuple[int, ...]]:
    """Build a random DAG where node i always depends on i-1, plus random earlier extras.

    The guaranteed i -> i-1 chain gives every node a path back to node 0, which
    test_is_acyclic_edges_false_once_a_back_edge_closes_a_cycle relies on to add exactly one
    edge that is certain to close a cycle, regardless of which random extras were drawn.
    """
    size = draw(st.integers(min_value=2, max_value=12))
    edges: dict[int, tuple[int, ...]] = {0: ()}
    for index in range(1, size):
        earlier = list(range(index - 1))
        extra = draw(st.lists(st.sampled_from(earlier), unique=True)) if earlier else []
        edges[index] = tuple({index - 1, *extra})
    return edges


# ──────────────────────────────────────────────────────────────────────────────
# is_acyclic_edges: direct cases
# ──────────────────────────────────────────────────────────────────────────────


def test_is_acyclic_edges_accepts_an_empty_graph() -> None:
    assert is_acyclic_edges({}) is True


def test_is_acyclic_edges_accepts_a_diamond() -> None:
    edges = {"a": ("b", "c"), "b": ("d",), "c": ("d",), "d": ()}

    assert is_acyclic_edges(edges) is True


def test_is_acyclic_edges_treats_an_unknown_target_as_a_leaf() -> None:
    # "b" is a dependency but never a key: is_acyclic_edges must not raise or treat it as an error.
    edges = {"a": ("b",)}

    assert is_acyclic_edges(edges) is True


def test_is_acyclic_edges_rejects_a_self_loop() -> None:
    edges = {"a": ("a",)}

    assert is_acyclic_edges(edges) is False


def test_is_acyclic_edges_rejects_a_two_node_cycle() -> None:
    edges = {"a": ("b",), "b": ("a",)}

    assert is_acyclic_edges(edges) is False


def test_is_acyclic_edges_rejects_a_longer_cycle_reached_through_a_shared_prefix() -> None:
    # "a" -> "b" -> "c" -> "a" is a cycle, but "d" also points into "b" without being part of one;
    # this exercises the DFS resuming a partially-explored node's iterator correctly.
    edges = {"a": ("b",), "b": ("c",), "c": ("a",), "d": ("b",)}

    assert is_acyclic_edges(edges) is False


# ──────────────────────────────────────────────────────────────────────────────
# is_acyclic_edges: hypothesis properties
# ──────────────────────────────────────────────────────────────────────────────


@_SETTINGS
@given(_chained_dag_edges())
def test_is_acyclic_edges_true_for_a_graph_with_only_higher_to_lower_edges(
    edges: dict[int, tuple[int, ...]],
) -> None:
    assert is_acyclic_edges(edges) is True


@_SETTINGS
@given(_chained_dag_edges())
def test_is_acyclic_edges_false_once_a_back_edge_closes_a_cycle(
    edges: dict[int, tuple[int, ...]],
) -> None:
    # The i -> i-1 chain guarantees a forward path from the highest index down to 0; adding a
    # single edge from 0 back up to the highest index closes exactly one cycle through that path.
    highest = max(edges)
    cyclic = dict(edges)
    cyclic[0] = (*cyclic[0], highest)

    assert is_acyclic_edges(cyclic) is False


# ──────────────────────────────────────────────────────────────────────────────
# is_acyclic: the Task-shaped wrapper
# ──────────────────────────────────────────────────────────────────────────────


def test_is_acyclic_accepts_a_linear_dependency() -> None:
    clock = FakeClock()
    upstream = make_task(clock=clock)
    downstream = make_task(clock=clock, spec=make_task_spec(depends_on=(upstream.id,)))

    assert is_acyclic((upstream, downstream)) is True


def test_is_acyclic_rejects_a_mutual_dependency() -> None:
    clock = FakeClock()
    task_a_id = new_task_id(clock)
    task_b_id = new_task_id(clock)
    task_a = make_task(clock=clock, id=task_a_id, spec=make_task_spec(depends_on=(task_b_id,)))
    task_b = make_task(clock=clock, id=task_b_id, spec=make_task_spec(depends_on=(task_a_id,)))

    assert is_acyclic((task_a, task_b)) is False


# ──────────────────────────────────────────────────────────────────────────────
# ready_tasks: direct cases
# ──────────────────────────────────────────────────────────────────────────────


def test_ready_tasks_excludes_a_pending_task_with_an_unfinished_dependency() -> None:
    clock = FakeClock()
    upstream = make_task(status=TaskStatus.RUNNING, clock=clock)
    downstream = make_task(
        status=TaskStatus.PENDING, clock=clock, spec=make_task_spec(depends_on=(upstream.id,))
    )

    assert ready_tasks((upstream, downstream)) == ()


def test_ready_tasks_includes_a_pending_task_whose_dependency_succeeded() -> None:
    clock = FakeClock()
    upstream = make_task(status=TaskStatus.SUCCEEDED, clock=clock)
    downstream = make_task(
        status=TaskStatus.PENDING, clock=clock, spec=make_task_spec(depends_on=(upstream.id,))
    )

    assert ready_tasks((upstream, downstream)) == (downstream,)


def test_ready_tasks_excludes_a_pending_task_whose_dependency_is_missing_entirely() -> None:
    clock = FakeClock()
    missing_id = new_task_id(clock)
    downstream = make_task(
        status=TaskStatus.PENDING, clock=clock, spec=make_task_spec(depends_on=(missing_id,))
    )

    assert ready_tasks((downstream,)) == ()


def test_ready_tasks_orders_by_created_at_then_id() -> None:
    clock = FakeClock()
    first = make_task(status=TaskStatus.PENDING, clock=clock)
    clock.advance(1)
    second = make_task(status=TaskStatus.PENDING, clock=clock)

    assert ready_tasks((second, first)) == (first, second)


# ──────────────────────────────────────────────────────────────────────────────
# ready_tasks: hypothesis property
# ──────────────────────────────────────────────────────────────────────────────

_STATUS_CHOICES = st.sampled_from(list(TaskStatus))


@_SETTINGS
@given(_chained_dag_edges(), st.data())
def test_ready_tasks_is_pending_only_with_every_dependency_succeeded(
    edges: dict[int, tuple[int, ...]], data: st.DataObject
) -> None:
    clock = FakeClock()
    statuses = {index: data.draw(_STATUS_CHOICES) for index in edges}
    tasks, _ids = _build_tasks(edges, statuses, clock)

    ready = ready_tasks(tasks)

    status_by_id = {task.id: task.status for task in tasks}
    for task in ready:
        # ready_tasks (Docs) subseteq PENDING.
        assert task.status is TaskStatus.PENDING
        # Every dependency of a ready task is present and SUCCEEDED.
        assert all(status_by_id[dep] is TaskStatus.SUCCEEDED for dep in task.spec.depends_on)


# ──────────────────────────────────────────────────────────────────────────────
# descendants: direct cases
# ──────────────────────────────────────────────────────────────────────────────


def test_descendants_is_empty_for_an_unknown_task_id() -> None:
    clock = FakeClock()
    task = make_task(clock=clock)

    assert descendants((task,), new_task_id(clock)) == frozenset()


def test_descendants_finds_direct_and_transitive_dependents() -> None:
    clock = FakeClock()
    root = make_task(clock=clock)
    child = make_task(clock=clock, spec=make_task_spec(depends_on=(root.id,)))
    grandchild = make_task(clock=clock, spec=make_task_spec(depends_on=(child.id,)))

    result = descendants((root, child, grandchild), root.id)

    assert result == frozenset({child.id, grandchild.id})


def test_descendants_deduplicates_a_diamond_shaped_dependency() -> None:
    clock = FakeClock()
    root = make_task(clock=clock)
    left = make_task(clock=clock, spec=make_task_spec(depends_on=(root.id,)))
    right = make_task(clock=clock, spec=make_task_spec(depends_on=(root.id,)))
    joined = make_task(clock=clock, spec=make_task_spec(depends_on=(left.id, right.id)))

    result = descendants((root, left, right, joined), root.id)

    assert result == frozenset({left.id, right.id, joined.id})


# ──────────────────────────────────────────────────────────────────────────────
# descendants: hypothesis property
# ──────────────────────────────────────────────────────────────────────────────


@_SETTINGS
@given(_chained_dag_edges(), st.data())
def test_descendants_is_closed_under_the_dependency_relation_and_excludes_the_root(
    edges: dict[int, tuple[int, ...]], data: st.DataObject
) -> None:
    clock = FakeClock()
    root_index = data.draw(st.sampled_from(sorted(edges)))
    tasks, ids = _build_tasks(edges, {}, clock)
    root_id = ids[root_index]

    result = descendants(tasks, root_id)

    # The root is never its own descendant.
    assert root_id not in result

    # Closure: any task that depends (directly) on the root or on something already found must
    # itself be found, since it therefore also transitively depends on the root.
    covered = {root_id, *result}
    for task in tasks:
        if any(dep in covered for dep in task.spec.depends_on):
            assert task.id in covered
