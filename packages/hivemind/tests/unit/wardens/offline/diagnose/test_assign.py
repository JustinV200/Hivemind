"""Tests for hivemind.wardens.offline.diagnose.assign: the task and grant a cut-off Warden mints.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/diagnose/assign.py (codingrules section 3). The Drone's
    task never came from the Queen, so everything her `task.assign` and `forage.grant_issued` would
    have carried is checked here: the objective is the one the roadmap states, with the host and
    what an earlier Drone said; the goal ceiling leaves the Drone its tools, commands and files and
    nothing that asks or reaches out; the grant is one sub-bee on one local source, free, and
    lasts as long as the budget has left.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.diagnose.assign for the module under test.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for what the Drone is for.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import PurePosixPath

from builders.cells import make_cell
from builders.forage import make_source
from builders.wardens import make_warden_deps

from hivemind.cell import AccessLevel
from hivemind.guard import Capability, warden_set
from hivemind.wardens.offline.diagnose import (
    CEILING,
    Finding,
    LocalBinding,
    LocalPlan,
    diagnosis_objective,
    local_assignment,
)
from hivemind.wardens.offline.diagnose.assign import COMMAND_HINT, MAX_EARLIER_CHARS
from hivemind.wardens.spawn.attenuate import sub_bee_capabilities
from waggle.clock import FakeClock
from waggle.ids import new_warden_id
from waggle.messages import AccuracyBar, HoneyClearance, PostconditionKind
from waggle.messages.task import WorkerRole
from waggle.messages.task.assignment import MAX_OBJECTIVE_CHARS

_SCRATCH = PurePosixPath("/hive/scratch/lease-1")  # A lease's scratch root, as the Guard fills it.


def _plan(clock: FakeClock, **changes: object) -> LocalPlan:
    """A plan for a Drone of the first attempt, on a local source, with 90 s of budget left."""
    cell = make_cell(clock=clock)
    source = make_source(source_id="small", provider="box", model="small-model")
    fields: dict[str, object] = {
        "warden_id": new_warden_id(clock),
        "cell": cell,
        "binding": LocalBinding("local_worker", source),
        "peer": "hive.example",
        "earlier": (),
        "valid_for_s": 90.0,
        "attempt": 1,
    }
    fields.update(changes)
    return LocalPlan(**fields)  # type: ignore[arg-type]  # test-only field table


def test_the_objective_is_the_roadmaps_with_the_host_the_warden_dials() -> None:
    objective = diagnosis_objective("hive.example", ())

    assert objective.startswith(
        "Diagnose and, if possible, restore the link to the Queen at hive.example."
    )
    assert "CAUSE:" in objective and "none found" in objective  # How the Warden reads the ending.
    assert "earlier attempt" not in objective


def test_the_objective_says_what_the_command_tool_does_not() -> None:
    objective = diagnosis_objective("hive.example", ())

    # A command's result is only whether it worked, and three failures stop a Drone, so a check
    # is written to run so that it leaves its output in a file and exits 0.
    assert COMMAND_HINT in objective
    assert "never what it printed" in objective and "always succeeds" in objective
    assert "out.txt" in objective


def test_a_warden_that_knows_no_name_for_the_queen_still_gives_an_objective() -> None:
    assert diagnosis_objective("", ()).startswith(
        "Diagnose and, if possible, restore the link to the Queen."
    )


def test_a_second_drone_is_told_what_the_first_reported_and_to_try_something_else() -> None:
    earlier = (Finding(None, "CAUSE: none found\nThe resolver answers."), Finding(None, "No luck."))

    objective = diagnosis_objective("hive.example", earlier)

    assert "An earlier attempt reported the following. Try something it did not." in objective
    assert "The resolver answers." in objective and "No luck." in objective


def test_what_earlier_drones_reported_is_cut_to_a_page() -> None:
    objective = diagnosis_objective("hive.example", (Finding(None, "#" * 10 * MAX_EARLIER_CHARS),))

    assert objective.count("#") == MAX_EARLIER_CHARS


def test_the_longest_objective_a_search_can_write_still_fits_a_task() -> None:
    longest = diagnosis_objective("h" * 253, (Finding(None, "#" * 10 * MAX_EARLIER_CHARS),))

    assert len(longest) <= MAX_OBJECTIVE_CHARS


def test_the_task_is_a_low_tempo_drone_on_the_workers_slot_with_a_rubric_to_meet() -> None:
    clock = FakeClock()
    plan = _plan(clock)

    wanted = local_assignment(plan, clock)
    task = wanted.assignment

    assert task.role is WorkerRole.DRONE
    assert task.slot == "WORKER"
    assert task.tempo.accuracy is AccuracyBar.LOW and task.tempo.latency_budget_s is None
    assert task.clearance is HoneyClearance.C1
    assert task.cell_id == plan.cell.id
    assert task.goal_id == task.task_id  # A root: it belongs to no goal of the Queen's.
    assert task.attempt == 1 and task.resume_from is None and task.handoff is None
    [criterion] = task.acceptance
    assert criterion.kind is PostconditionKind.JUDGE_RUBRIC and criterion.expected
    assert wanted.binding == "local_worker"


def test_the_goal_ceiling_names_commands_files_the_slot_and_the_tools_the_role_lists() -> None:
    clock = FakeClock()

    task = local_assignment(_plan(clock), clock).assignment

    assert task.capabilities == CEILING
    assert set(CEILING) == {
        "exec:*",
        "exec:@diagnostic",  # Marks a diagnostic task (hivemind.guard.diagnostics).
        "fs:read:**",
        "fs:write:**",
        "llm:worker",
        "tool:*",
    }


def test_under_that_ceiling_a_drone_holds_its_tools_and_nothing_that_asks_or_reaches_out() -> None:
    # The Drone's role lists its tools as `tool:*`, and a ceiling keeps only what one of its own
    # entries covers: a ceiling that named `tool:run_command` and the like would leave it none.
    clock = FakeClock()
    deps, _queen_end, _warden_id = make_warden_deps(clock)
    task = local_assignment(_plan(clock), clock).assignment
    ceiling = warden_set(deps.guard, AccessLevel.FULL, _SCRATCH)

    held, _roots = sub_bee_capabilities(deps, ceiling, _SCRATCH, task)

    for needed in (
        "tool:run_command",
        "tool:read_file",
        "tool:write_file",
        "exec:ping",
        "llm:worker",
    ):
        assert held.allows(Capability.parse(needed)), needed
    assert held.allows(Capability.parse(f"fs:write:{_SCRATCH.as_posix()}/notes.txt"))
    # Everything else a tool asks for is not held, so its call is refused when it is made: a
    # question to a human, a network of its own, a write outside scratch, a tool from the Hive,
    # Honey, Cell Wax and spend.
    for refused in (
        "question:human",
        "net:hive.example",
        "fs:write:/etc/hosts",
        "tool:request",
        "honey:write",
        "wax:propose",
        "spend:1",
    ):
        assert not held.allows(Capability.parse(refused)), refused


def test_the_grant_is_one_sub_bee_on_one_local_source_that_costs_nothing() -> None:
    clock = FakeClock()
    plan = _plan(clock)

    wanted = local_assignment(plan, clock)
    grant, task = wanted.grant, wanted.assignment

    assert grant.grant_id == task.grant_id and grant.task_id == task.task_id
    assert grant.holder == plan.warden_id and grant.cell_id == plan.cell.id
    assert grant.revision == 0 and grant.max_sub_bees == 1 and grant.seats == ()
    assert grant.spend_budget == 0.0 and grant.spent == 0.0 and grant.tokens_spent == 0
    [allowed] = grant.allowed
    assert allowed.slot == "WORKER"
    assert (allowed.source.source_id, allowed.source.provider) == ("small", "box")
    assert allowed.source.model == "small-model"
    assert allowed.source.host_cell_id == plan.cell.id  # Hosted on this very Cell.
    assert grant.expires_at == clock.now() + timedelta(seconds=90.0)


def test_each_drone_gets_a_task_and_a_grant_of_its_own() -> None:
    clock = FakeClock()
    plan = _plan(clock)

    first, second = local_assignment(plan, clock), local_assignment(plan, clock)

    assert first.assignment.task_id != second.assignment.task_id
    assert first.grant.grant_id != second.grant.grant_id


def test_the_attempt_number_is_the_drones_place_in_the_search() -> None:
    clock = FakeClock()

    task = local_assignment(_plan(clock, attempt=2), clock).assignment

    assert task.attempt == 2
