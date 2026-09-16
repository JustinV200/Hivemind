"""Tests for hivemind.workers.roles.drone.outcome.build.build_handoff_outcome.

Defect 1 (roadmap step 4.5's own eval exposed it): `build_handoff_outcome` used to build
`do_not_redo`, `tried_and_failed`, `constraints`, `open_threads` and `pinned_facts` as empty
tuples regardless of what the attempt actually did. These tests build a `_RecordingExecutor`
directly and populate its `records`/`pending_call` the way a real attempt would, then assert every
Handoff field this module derives reflects them.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/drone/outcome/build.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.drone.outcome.build for the module under test.
"""

from __future__ import annotations

from builders.llm import make_tool_call
from builders.memory import make_pin
from builders.workers import make_assignment, make_context

from hivemind.memory import MemoryContext, add_pin
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.drone.outcome.build import build_handoff_outcome
from hivemind.workers.roles.drone.outcome.executor import _RecordingExecutor
from hivemind.workers.roles.drone.outcome.fields import MAX_DERIVED_LIST_ITEMS
from hivemind.workers.roles.drone.outcome.records import ToolCallRecord
from hivemind.workers.tools import ToolInvocation, build_registry
from waggle.messages import Postcondition, PostconditionKind
from waggle.messages.task import TaskAssign


def _executor(ctx: WorkerContext, assignment: TaskAssign) -> _RecordingExecutor:
    """Build a real _RecordingExecutor for `ctx`/`assignment`; a test fills in records itself."""
    registry = build_registry(ctx)
    invocation = ToolInvocation(ctx=ctx, assignment=assignment)
    return _RecordingExecutor(registry, invocation, ctx.telemetry, ctx.handoff_threshold)


async def test_build_handoff_outcome_derives_do_not_redo_from_a_landed_write() -> None:
    ctx = make_context()
    assignment = make_assignment()
    executor = _executor(ctx, assignment)
    call = make_tool_call(name="write_file", arguments={"path": "step_1.txt", "content": "x"})
    executor.records.append(ToolCallRecord(call=call, result_text="state=VERIFIED", is_error=False))

    outcome = await build_handoff_outcome(ctx, assignment, executor)

    assert outcome.claimed is False
    assert outcome.handoff is not None
    assert len(outcome.handoff.do_not_redo) == 1
    assert "step_1.txt" in outcome.handoff.do_not_redo[0]


async def test_build_handoff_outcome_derives_tried_and_failed_from_an_error() -> None:
    ctx = make_context()
    assignment = make_assignment()
    executor = _executor(ctx, assignment)
    call = make_tool_call(name="run_command", arguments={"argv": ["missing-binary"]})
    executor.records.append(
        ToolCallRecord(call=call, result_text="state=REJECTED; reason=bad", is_error=True)
    )

    outcome = await build_handoff_outcome(ctx, assignment, executor)

    assert outcome.handoff is not None
    assert len(outcome.handoff.tried_and_failed) == 1
    assert "missing-binary" in outcome.handoff.tried_and_failed[0]
    # A rejected proposal is not itself a landed side effect: nothing to warn against redoing.
    assert outcome.handoff.do_not_redo == ()


async def test_build_handoff_outcome_derives_constraints_from_a_quota_style_rejection() -> None:
    ctx = make_context()
    assignment = make_assignment()
    executor = _executor(ctx, assignment)
    call = make_tool_call(name="write_file", arguments={"path": "huge.txt", "content": "x"})
    rejected_text = "state=REJECTED; reason=too large; checks=(SIZE_CAP=FAILED)"
    executor.records.append(ToolCallRecord(call=call, result_text=rejected_text, is_error=True))

    outcome = await build_handoff_outcome(ctx, assignment, executor)

    assert outcome.handoff is not None
    assert len(outcome.handoff.constraints) == 1
    assert "huge.txt" in outcome.handoff.constraints[0]


async def test_build_handoff_outcome_derives_open_threads_from_the_pending_call() -> None:
    ctx = make_context()
    assignment = make_assignment()
    executor = _executor(ctx, assignment)
    executor.pending_call = make_tool_call(
        name="write_file", arguments={"path": "step_3.txt", "content": "x"}
    )

    outcome = await build_handoff_outcome(ctx, assignment, executor)

    assert outcome.handoff is not None
    assert len(outcome.handoff.open_threads) == 1
    assert "step_3.txt" in outcome.handoff.open_threads[0]


async def test_build_handoff_outcome_derives_pinned_facts_verbatim() -> None:
    ctx = make_context()
    assignment = make_assignment()
    executor = _executor(ctx, assignment)
    pin = make_pin(text="Always run the tests before committing.")
    memory_ctx = MemoryContext(store=ctx.memory, identity=ctx.identity, clock=ctx.clock)
    await add_pin(pin, memory_ctx)

    outcome = await build_handoff_outcome(ctx, assignment, executor)

    assert outcome.handoff is not None
    assert outcome.handoff.pinned_facts == (pin.text,)


async def test_build_handoff_outcome_derives_next_steps_from_unmet_acceptance() -> None:
    ctx = make_context()
    assignment = make_assignment(
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject="report.txt", argv=(), expected=None
            ),
        )
    )
    executor = _executor(ctx, assignment)

    outcome = await build_handoff_outcome(ctx, assignment, executor)

    assert outcome.handoff is not None
    assert len(outcome.handoff.next_steps) == 1
    assert "report.txt" in outcome.handoff.next_steps[0]


async def test_build_handoff_outcome_honours_the_derived_list_cap() -> None:
    ctx = make_context()
    assignment = make_assignment()
    executor = _executor(ctx, assignment)
    for i in range(MAX_DERIVED_LIST_ITEMS + 10):
        call = make_tool_call(name="write_file", arguments={"path": f"f{i}.txt", "content": "x"})
        executor.records.append(
            ToolCallRecord(call=call, result_text="state=VERIFIED", is_error=False)
        )

    outcome = await build_handoff_outcome(ctx, assignment, executor)

    assert outcome.handoff is not None
    assert len(outcome.handoff.do_not_redo) == MAX_DERIVED_LIST_ITEMS
