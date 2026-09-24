"""Unit tests for hivemind.workers.roles.scout.Scout: the strictly budgeted recon role.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/scout/role.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.scout.role for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from builders.llm import make_bound, make_tool_call
from builders.workers import make_assignment, make_context

from hivemind.llm import FakeLLMProvider, Usage, tool_call_response
from hivemind.workers.roles.scout import Scout
from waggle.clock import FakeClock
from waggle.messages.task import SCOUT_REPORT_FILE, WorkerRole


async def test_scout_happy_path_files_a_report_and_ends_the_loop_at_once() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()
    ctx = make_context(clock=clock, bound=make_bound(provider=provider))
    assignment = make_assignment(
        clock=clock, role=WorkerRole.SCOUT, objective="Look at the login page's form."
    )
    report = make_tool_call(
        name="report_findings",
        arguments={
            "feasible": True,
            "summary": "The login form has a username field and a password field.",
            "targets": ["https://fixture.test/login"],
            "suggested_steps": ["Fill username", "Fill password", "Click Log in"],
        },
    )
    provider.script(tool_call_response(report))

    outcome = await Scout().run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    assert outcome.handoff is None
    assert outcome.scout_report is not None
    assert outcome.scout_report.feasible is True
    assert outcome.scout_report.targets == ("https://fixture.test/login",)
    # Written through the same capped path write_file uses, so acceptance's FILE_EXISTS holds.
    data = await ctx.session.get_file(Path(SCOUT_REPORT_FILE))
    assert b"username field" in data


async def test_scout_report_records_what_it_spent_though_its_loop_never_finished() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()
    ctx = make_context(clock=clock, bound=make_bound(provider=provider))
    assignment = make_assignment(clock=clock, role=WorkerRole.SCOUT)
    report = make_tool_call(
        name="report_findings",
        arguments={
            "feasible": True,
            "summary": "A plain login form.",
            "targets": ["https://fixture.test/login"],
            "suggested_steps": ["Log in"],
        },
    )
    priced = Usage(input_tokens=40, output_tokens=8, cost_usd=0.25)
    provider.script(tool_call_response(report).model_copy(update={"usage": priced}))

    outcome = await Scout().run(ctx, assignment, resume_from=None)

    # report_findings ended the loop before run_tool_loop could sum it; the gate's tally did.
    assert outcome.spend_usd == 0.25
    assert ctx.telemetry.snapshot().tokens_used == 48


async def test_scout_falls_back_to_an_infeasible_report_when_rounds_run_out() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()
    ctx = make_context(clock=clock, bound=make_bound(provider=provider))
    assignment = make_assignment(clock=clock, role=WorkerRole.SCOUT)
    # Never calls report_findings: the model just keeps reading, forever, so the round cap (not
    # a filed report) is what ends this attempt.
    read = make_tool_call(name="read_file", arguments={"path": "does-not-exist.txt"})
    provider.script(tool_call_response(read), tool_call_response(read))

    outcome = await Scout(max_rounds=2).run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    assert outcome.handoff is None
    assert outcome.scout_report is not None
    assert outcome.scout_report.feasible is False
    assert "round" in outcome.scout_report.summary.lower()
    # The fallback report is still written, so the Queen still gets one and acceptance still
    # finds a file, even though report_findings was never called.
    data = await ctx.session.get_file(Path(SCOUT_REPORT_FILE))
    assert b"false" in data.lower()


async def test_scout_handoff_is_returned_unchanged_not_treated_as_exhaustion() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()
    ctx = make_context(clock=clock, bound=make_bound(provider=provider), handoff_threshold=0.5)
    ctx.telemetry.record_tokens(ctx.bound.context_window, ctx.bound.context_window)
    assignment = make_assignment(clock=clock, role=WorkerRole.SCOUT)
    read = make_tool_call(name="read_file", arguments={"path": "does-not-exist.txt"})
    provider.script(tool_call_response(read))

    outcome = await Scout().run(ctx, assignment, resume_from=None)

    assert outcome.claimed is False
    assert outcome.handoff is not None
    assert outcome.scout_report is None  # A checkpoint, not a report either way.


def test_scout_role_is_always_scout() -> None:
    assert Scout().role is WorkerRole.SCOUT
