"""Test hivemind.entrance.routes.hive.tasks: tasks paged by goal and status, without their words.

Over real listeners and a real Queen's Brood Chamber: an observing device pages through a goal's
tasks with the keyset cursor (every task once, in the chamber's order), filters them by status,
reads one task with how it ended, and never sees a task's words; the brief, which holds them, is
refused without ``honey:clearance:c2`` and answered with it; a submit-only device is refused the
reads; an unknown task is a 404; and the remote listener serves the same reads.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/hive/tasks.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, RigOptions, ServingRig, serving
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus

_OBSERVE = ProgramGrant(capabilities=("observe",))
_CLEARED = ProgramGrant(capabilities=("observe", "honey:clearance:c2"))
_WORDS = {"title", "objective", "acceptance", "last_summary", "summary", "artifacts"}


async def _goal(rig: ServingRig) -> tuple[Task, ...]:
    """Submit a three-task goal to the Brood Chamber: the goal first, then two that follow it."""
    draft = make_graph_draft({"garden": (), "beds": ("garden",), "paths": ("garden",)})
    return await rig.deps.chamber.submit(draft)


async def _listed(rig: ServingRig, query: TaskFilter) -> list[str]:
    """The ids the Brood Chamber itself lists for ``query``, in its own order."""
    return [task.id for task in await rig.deps.chamber.list(query)]


async def test_an_observing_device_pages_a_goals_tasks_every_one_once_without_words() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        tasks = await _goal(rig)
        await _goal(rig)  # Another goal's tasks, which the filter leaves out.
        goal_id = tasks[0].id

        first = await client.call(session, "GET", f"/v1/tasks?goal_id={goal_id}&limit=2")
        cursor = first.json()["next_after"]
        second = await client.call(
            session, "GET", f"/v1/tasks?goal_id={goal_id}&limit=2&after={cursor}"
        )
        expected = await _listed(rig, TaskFilter(goal_id=goal_id))

    assert first.status_code == second.status_code == 200, first.text
    pages = first.json()["tasks"] + second.json()["tasks"]
    assert [task["id"] for task in pages] == expected
    assert sorted(expected) == sorted(task.id for task in tasks)
    assert second.json()["next_after"] is None
    assert all(not _WORDS & set(task) for task in pages)


async def test_a_status_filter_answers_only_tasks_in_that_status() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        tasks = await _goal(rig)
        await rig.deps.chamber.cancel(tasks[2].id, "operator_cancelled")

        cancelled = await client.call(session, "GET", "/v1/tasks?status=CANCELLED")
        pending = await client.call(session, "GET", "/v1/tasks?status=PENDING")
        still_pending = await _listed(rig, TaskFilter(status=TaskStatus.PENDING))

    assert [task["id"] for task in cancelled.json()["tasks"]] == [tasks[2].id]
    assert [task["id"] for task in pending.json()["tasks"]] == still_pending
    assert sorted(still_pending) == sorted([tasks[0].id, tasks[1].id])


async def test_one_task_answers_with_how_it_ended_and_an_unknown_one_is_404() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        tasks = await _goal(rig)
        await rig.deps.chamber.cancel(tasks[0].id, "the human changed their mind")

        read = await client.call(session, "GET", f"/v1/tasks/{tasks[0].id}")
        missing = await client.call(session, "GET", "/v1/tasks/task_0000000000000000000000000A")

    assert read.status_code == 200, read.text
    task = read.json()
    assert (task["status"], task["goal_id"], task["depends_on"]) == ("CANCELLED", tasks[0].id, [])
    assert task["outcome"] == {
        "status": "CANCELLED",
        "verified_by": None,
        "spend_usd": 0.0,
        "artifact_count": 0,
    }
    assert "the human changed their mind" not in read.text
    assert missing.status_code == 404


async def test_the_brief_holds_the_words_and_is_answered_only_with_c2() -> None:
    async with serving() as rig:
        observer, observer_session = await rig.program(_OBSERVE)
        cleared, cleared_session = await rig.program(_CLEARED)
        tasks = await _goal(rig)
        path = f"/v1/tasks/{tasks[1].id}/brief"

        refused = await observer.call(observer_session, "GET", path)
        brief = await cleared.call(cleared_session, "GET", path)

    assert refused.status_code == 403
    assert brief.status_code == 200, brief.text
    assert (brief.json()["title"], brief.json()["objective"]) == (
        tasks[1].spec.title,
        tasks[1].spec.objective,
    )
    assert len(brief.json()["acceptance"]) == len(tasks[1].spec.acceptance)


async def test_a_submit_only_device_is_refused_the_task_reads() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))
        tasks = await _goal(rig)

        listed = await client.call(session, "GET", "/v1/tasks")
        read = await client.call(session, "GET", f"/v1/tasks/{tasks[0].id}")

    assert (listed.status_code, read.status_code) == (403, 403)


async def test_the_remote_listener_serves_the_task_reads() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("observe",), remote=True))
        tasks = await _goal(rig)

        listed = await client.call(session, "GET", "/v1/tasks")

    assert listed.status_code == 200, listed.text
    assert sorted(task["id"] for task in listed.json()["tasks"]) == sorted(t.id for t in tasks)
