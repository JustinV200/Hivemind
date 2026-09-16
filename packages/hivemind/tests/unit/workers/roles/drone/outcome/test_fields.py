"""Tests for hivemind.workers.roles.drone.outcome.fields: deriving a Handoff's guidance fields.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/drone/outcome/fields.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.drone.outcome.fields for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from builders.llm import make_tool_call
from builders.memory import make_pin
from builders.workers import make_assignment, make_context

from hivemind.cell import HoneyClearance
from hivemind.memory import MemoryContext, add_pin
from hivemind.workers.roles.drone.outcome.fields import (
    MAX_DERIVED_LIST_ITEMS,
    constraint_lines,
    do_not_redo_lines,
    next_step_lines,
    open_thread_lines,
    pinned_fact_lines,
    tried_and_failed_lines,
)
from hivemind.workers.roles.drone.outcome.records import ToolCallRecord
from waggle.messages import Postcondition, PostconditionKind


def _record(
    name: str, *, is_error: bool, result_text: str = "", **arguments: object
) -> ToolCallRecord:
    call = make_tool_call(name=name, arguments=dict(arguments))
    return ToolCallRecord(call=call, result_text=result_text, is_error=is_error)


def test_do_not_redo_lines_names_every_landed_side_effect() -> None:
    landed = _record(
        "write_file", is_error=False, result_text="state=VERIFIED", path="a.txt", content="x"
    )
    rejected = _record(
        "write_file", is_error=True, result_text="state=REJECTED", path="b.txt", content="x"
    )
    harmless = _record("read_file", is_error=False, result_text="hello", path="c.txt")

    lines = do_not_redo_lines([landed, rejected, harmless])

    assert len(lines) == 1
    assert "a.txt" in lines[0]
    assert "b.txt" not in lines[0]


def test_do_not_redo_lines_is_capped_to_max_derived_list_items() -> None:
    records = [
        _record("write_file", is_error=False, result_text="state=VERIFIED", path=f"f{i}.txt")
        for i in range(MAX_DERIVED_LIST_ITEMS + 5)
    ]

    lines = do_not_redo_lines(records)

    assert len(lines) == MAX_DERIVED_LIST_ITEMS
    # The cap keeps the most recent entries (module docstring): the earliest ones are dropped.
    assert "f0.txt" not in "".join(lines)
    assert f"f{MAX_DERIVED_LIST_ITEMS + 4}.txt" in "".join(lines)


def test_tried_and_failed_lines_names_every_error_with_its_first_line() -> None:
    failed = _record(
        "write_file",
        is_error=True,
        result_text="state=REJECTED; reason=blocked\nextra detail never shown",
        path="a.txt",
    )
    ok = _record("write_file", is_error=False, result_text="state=VERIFIED", path="b.txt")

    lines = tried_and_failed_lines([failed, ok])

    assert len(lines) == 1
    assert "a.txt" in lines[0]
    assert "state=REJECTED; reason=blocked" in lines[0]
    assert "extra detail never shown" not in lines[0]


def test_constraint_lines_reports_an_allowlist_rejection() -> None:
    allowlist_failure = _record(
        "run_command",
        is_error=True,
        result_text="state=REJECTED; reason=not allowed; checks=(ALLOWLIST=FAILED)",
        argv=["rm", "-rf", "/"],
    )

    lines = constraint_lines([allowlist_failure])

    assert len(lines) == 1
    assert "allowlist" in lines[0]


def test_constraint_lines_reports_a_size_cap_quota_rejection() -> None:
    size_cap_failure = _record(
        "write_file",
        is_error=True,
        result_text="state=REJECTED; reason=too large; checks=(SIZE_CAP=FAILED)",
        path="huge.txt",
    )

    lines = constraint_lines([size_cap_failure])

    assert len(lines) == 1
    assert "size cap" in lines[0]


def test_constraint_lines_reports_a_capability_denial() -> None:
    capability_denial = _record(
        "http_request",
        is_error=True,
        result_text="no net capability covers 'example.com'; the request was never sent.",
        method="GET",
        url="https://example.com",
    )

    lines = constraint_lines([capability_denial])

    assert len(lines) == 1
    assert "no net capability covers" in lines[0]


def test_constraint_lines_ignores_a_failure_that_named_no_limit_at_all() -> None:
    plain_failure = _record(
        "write_file", is_error=True, result_text="path must be a non-empty string.", path=""
    )

    assert constraint_lines([plain_failure]) == ()


def test_open_thread_lines_names_the_call_refused_for_a_checkpoint() -> None:
    pending = make_tool_call(name="write_file", arguments={"path": "step_3.txt", "content": "x"})

    lines = open_thread_lines(pending)

    assert len(lines) == 1
    assert "write_file" in lines[0]
    assert "step_3.txt" in lines[0]


def test_open_thread_lines_is_empty_when_nothing_was_pending() -> None:
    assert open_thread_lines(None) == ()


async def test_pinned_fact_lines_returns_pins_verbatim() -> None:
    ctx = make_context()
    pin = make_pin(text="Never exceed the manifest spend cap.")
    memory_ctx = MemoryContext(store=ctx.memory, identity=ctx.identity, clock=ctx.clock)
    await add_pin(pin, memory_ctx)

    facts = await pinned_fact_lines(ctx, HoneyClearance.C1)

    assert facts == (pin.text,)


async def test_next_step_lines_marks_a_file_exists_criterion_done_once_written() -> None:
    ctx = make_context()
    await ctx.session.put_file(Path("output.txt"), b"done")
    assignment = make_assignment(
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject="output.txt", argv=(), expected=None
            ),
        )
    )

    steps = await next_step_lines(ctx, assignment)

    assert steps == (
        "Resume the objective from this Handoff; every named acceptance criterion already "
        "appears satisfied -- verify before reporting done.",
    )


async def test_next_step_lines_names_a_file_exists_criterion_not_yet_written() -> None:
    ctx = make_context()
    assignment = make_assignment(
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject="output.txt", argv=(), expected=None
            ),
        )
    )

    steps = await next_step_lines(ctx, assignment)

    assert len(steps) == 1
    assert "output.txt" in steps[0]


async def test_next_step_lines_leaves_a_command_criterion_open_regardless() -> None:
    """A COMMAND_EXITS_ZERO criterion is never marked done.

    Running it here would itself be a second, unproposed side effect (module docstring).
    """
    ctx = make_context()
    assignment = make_assignment(
        acceptance=(
            Postcondition(
                kind=PostconditionKind.COMMAND_EXITS_ZERO,
                subject="check",
                argv=("pytest",),
                expected=None,
            ),
        )
    )

    steps = await next_step_lines(ctx, assignment)

    assert len(steps) == 1
    assert "pytest" in steps[0]
