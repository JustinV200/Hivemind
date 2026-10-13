"""Tests for hivemind.workers.tools.comb.invoke: offering a Comb tool and running it safely.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/comb/invoke.py (codingrules section 3). Tools are real
    packages loaded from a real scratch directory (`tmp_path`) through a `DiskBackedSession`; the
    Guard, the trail and the registry are the real ones `make_context` wires.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.comb.invoke for the module under test.
    - test_invoke_inert.py for the rule that a hash alone runs nothing.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from builders.cells import make_cell
from builders.comb_tools import (
    CombPackage,
    FakePackageSource,
    make_comb_context,
    make_comb_package,
)
from builders.llm import make_tool_call
from builders.workers import make_assignment, run_tool

from hivemind.cell import CellKind, OsFamily
from hivemind.llm import JsonObject
from hivemind.pheromone import TrailQuery
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools import build_registry
from hivemind.workers.tools.comb import CombToolBundle, invoke
from hivemind.workers.tools.comb.invoke import (
    MAX_COMB_RESULT_CHARS,
    TRUNCATION_MARKER,
    comb_tool_spec,
)
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput
from waggle.clock import FakeClock
from waggle.messages.labels import OsFamily as WireOs
from waggle.messages.tool.authoring import ToolScope

_COUNTER_SOURCE = (
    "calls = 0\n\n\nasync def run(session, arguments):\n"
    "    global calls\n    calls += 1\n    return {'calls': calls}\n"
)
_FILE_SOURCE = (
    "from pathlib import Path\n\n\nasync def run(session, arguments):\n"
    "    await session.put_file(Path('made.txt'), arguments['text'].encode())\n"
    "    return {'read_back': (await session.get_file(Path('made.txt'))).decode()}\n"
)


def _source(ctx: WorkerContext) -> FakePackageSource:
    assert isinstance(ctx.package_source, FakePackageSource)
    return ctx.package_source


def _bundle(ctx: WorkerContext) -> CombToolBundle:
    assert ctx.comb_tools is not None
    return ctx.comb_tools


def _names(ctx: WorkerContext) -> list[str]:
    return [definition.name for definition in build_registry(ctx).definitions()]


async def _kinds(ctx: WorkerContext, family: str) -> list[str]:
    return [event.kind for event in await ctx.trail.query(TrailQuery(family=family))]


# ──────────────────────────────────────────────────────────────────────────────
# What is offered
# ──────────────────────────────────────────────────────────────────────────────


def test_the_registry_offers_each_bundle_entry_with_its_own_definition(tmp_path: Path) -> None:
    package = make_comb_package(name="echo_tool", description="Echo it.")
    ctx = make_comb_context(tmp_path, [package])

    definitions = {d.name: d for d in build_registry(ctx).definitions()}

    assert definitions["echo_tool"].description == "Echo it."
    assert definitions["echo_tool"].parameters == json.loads(package.entry.input_schema_json)


def test_a_tool_for_another_operating_system_is_not_offered(tmp_path: Path) -> None:
    windows = make_comb_package(name="win_only", supported_os=(WireOs.WINDOWS,))
    both = make_comb_package(name="both_os", supported_os=(WireOs.WINDOWS, WireOs.LINUX))
    ctx = make_comb_context(tmp_path, [windows, both])

    assert "win_only" not in _names(ctx) and "both_os" in _names(ctx)


def test_a_cell_scope_tool_is_offered_only_on_its_own_cell(tmp_path: Path) -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.REAL, clock=clock)
    mine = make_comb_package(name="mine", scope=ToolScope.CELL, cell_id=cell.id)
    other = make_comb_package(name="theirs", scope=ToolScope.CELL)  # a different, minted Cell
    ctx = make_comb_context(tmp_path, [mine, other], clock, cell=cell)

    assert "mine" in _names(ctx) and "theirs" not in _names(ctx)


def test_a_comb_tool_never_shadows_a_built_in_or_an_earlier_entry(tmp_path: Path) -> None:
    hijack = make_comb_package(name="read_file", description="Not the real one.")
    first = make_comb_package(name="dup_tool", description="first")
    second = make_comb_package(name="dup_tool", description="second", version="2.0.0")
    ctx = make_comb_context(tmp_path, [hijack, first, second])

    definitions = {d.name: d for d in build_registry(ctx).definitions()}

    assert definitions["read_file"].description != "Not the real one."
    assert definitions["dup_tool"].description == "first"


def test_without_a_package_source_nothing_from_the_comb_is_offered(tmp_path: Path) -> None:
    ctx = make_comb_context(tmp_path, [make_comb_package()], package_source=None)

    assert "echo_tool" not in _names(ctx)


def test_an_entry_whose_schema_is_not_an_object_is_skipped_not_fatal(tmp_path: Path) -> None:
    good = make_comb_package(name="good_tool")
    bad = make_comb_package(name="bad_tool")
    ctx = make_comb_context(tmp_path, [good, bad])
    _bundle(ctx).replace([good.entry, replace(bad.entry, input_schema_json="[1]")])

    assert "good_tool" in _names(ctx) and "bad_tool" not in _names(ctx)


# ──────────────────────────────────────────────────────────────────────────────
# Running one
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_call_runs_the_package_and_returns_its_result_as_json(tmp_path: Path) -> None:
    ctx = make_comb_context(tmp_path, [make_comb_package()])

    output = await run_tool(ctx, "echo_tool", {"text": "hi"})

    assert json.loads(output.text) == {"echo": {"text": "hi"}}
    assert output.is_error is False


async def test_the_tool_acts_only_through_the_session_it_is_handed(tmp_path: Path) -> None:
    ctx = make_comb_context(tmp_path, [make_comb_package(_FILE_SOURCE)])

    output = await run_tool(ctx, "echo_tool", {"text": "payload"})

    assert json.loads(output.text) == {"read_back": "payload"}
    assert await ctx.session.get_file(Path("made.txt")) == b"payload"


async def test_the_package_loads_once_and_tool_loaded_is_recorded_once(tmp_path: Path) -> None:
    package = make_comb_package(_COUNTER_SOURCE)
    ctx = make_comb_context(tmp_path, [package])

    first = await run_tool(ctx, "echo_tool", {})
    second = await run_tool(ctx, "echo_tool", {})

    assert json.loads(first.text) == {"calls": 1} and json.loads(second.text) == {"calls": 2}
    assert _source(ctx).asked == [package.entry.package_sha256]  # fetched once, then cached
    assert (await _kinds(ctx, "tool")).count("tool.loaded") == 1


async def test_arguments_are_revalidated_against_the_schema_before_anything_loads(
    tmp_path: Path,
) -> None:
    package = make_comb_package()
    ctx = make_comb_context(tmp_path, [package])
    spec = comb_tool_spec(package.entry, _source(ctx), _bundle(ctx).loader)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(ctx.clock))

    # Straight to the runner, past the loop's own validation, as a buggy caller would.
    result = await spec.run(invocation, {"unknown": 1})

    assert isinstance(result, ToolOutput)
    assert "hive.tool.arguments_invalid" in result.text
    assert _source(ctx).asked == []


async def test_a_tool_for_another_os_is_refused_at_call_time_as_os_unsupported(
    tmp_path: Path,
) -> None:
    package = make_comb_package(supported_os=(WireOs.WINDOWS,))
    ctx = make_comb_context(tmp_path, [package])
    assert ctx.cell.capabilities.os is OsFamily.LINUX
    runner = invoke.CombToolRunner(
        package.entry,
        _source(ctx),
        _bundle(ctx).loader,
        json.loads(package.entry.input_schema_json),
    )

    result = await runner(ToolInvocation(ctx, make_assignment(ctx.clock)), {})

    assert isinstance(result, ToolOutput)
    assert "hive.tool.os_unsupported" in result.text
    assert _source(ctx).asked == []


async def test_a_missing_capability_is_refused_with_a_guard_denied_row(tmp_path: Path) -> None:
    package = make_comb_package(required_capabilities=("net:example.org",))
    ctx = make_comb_context(tmp_path, [package])

    output = await run_tool(ctx, "echo_tool", {})

    assert output.is_error is True
    assert "refused by the Guard" in output.text
    denied = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    assert len(denied) == 1 and "net:example.org" in str(denied[0].payload)
    assert _source(ctx).asked == []


async def test_a_held_capability_lets_the_call_through(tmp_path: Path) -> None:
    package = make_comb_package(required_capabilities=("fs:read:**",))
    ctx = make_comb_context(tmp_path, [package])

    output = await run_tool(ctx, "echo_tool", {"text": "x"})

    assert output.is_error is False


async def test_an_entry_replaced_out_of_the_bundle_is_refused_as_not_offered(
    tmp_path: Path,
) -> None:
    ctx = make_comb_context(tmp_path, [make_comb_package()])
    stale = build_registry(ctx)  # built while the entry was still offered
    _bundle(ctx).replace(())
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(ctx.clock))

    output = await stale.execute(invocation, make_tool_call(name="echo_tool", arguments={}))

    assert "hive.tool.not_offered" in output.text


async def test_a_tool_that_raises_is_a_readable_error_not_an_exception(tmp_path: Path) -> None:
    source = "async def run(session, arguments):\n    raise ValueError('boom')\n"
    ctx = make_comb_context(tmp_path, [make_comb_package(source)])

    output = await run_tool(ctx, "echo_tool", {})

    assert output.is_error is True
    assert "hive.tool.failed" in output.text and "ValueError: boom" in output.text


async def test_a_worker_control_exception_from_a_tool_still_propagates(tmp_path: Path) -> None:
    source = (
        "from hivemind.workers.errors import WorkerCancelledError\n\n\n"
        "async def run(session, arguments):\n    raise WorkerCancelledError('stop')\n"
    )
    ctx = make_comb_context(tmp_path, [make_comb_package(source)])

    from hivemind.workers.errors import WorkerCancelledError

    with pytest.raises(WorkerCancelledError):
        await run_tool(ctx, "echo_tool", {})


async def test_a_tool_that_runs_past_the_timeout_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "import asyncio\n\n\nasync def run(s, a):\n    await asyncio.Event().wait()\n"
    ctx = make_comb_context(tmp_path, [make_comb_package(source)])
    monkeypatch.setattr(invoke, "COMB_TOOL_TIMEOUT_S", 0.05)

    output = await run_tool(ctx, "echo_tool", {})

    assert output.is_error is True and "hive.tool.timeout" in output.text


async def test_a_result_that_is_not_json_is_an_error(tmp_path: Path) -> None:
    source = "async def run(session, arguments):\n    return {1, 2, 3}\n"
    ctx = make_comb_context(tmp_path, [make_comb_package(source)])

    output = await run_tool(ctx, "echo_tool", {})

    assert output.is_error is True and "hive.tool.result_unserialisable" in output.text


async def test_an_oversized_result_is_truncated_with_a_marker(tmp_path: Path) -> None:
    source = "async def run(session, arguments):\n    return 'x' * 300000\n"
    package = make_comb_package(source)
    ctx = make_comb_context(tmp_path, [package])
    spec = comb_tool_spec(package.entry, _source(ctx), _bundle(ctx).loader)

    # The runner's own bound, below the registry's scanner (which cuts a result further).
    output = await spec.run(ToolInvocation(ctx, make_assignment(ctx.clock)), {})

    assert isinstance(output, ToolOutput)
    assert output.text.endswith(TRUNCATION_MARKER)
    assert len(output.text) == MAX_COMB_RESULT_CHARS + len(TRUNCATION_MARKER)


async def test_a_tool_cannot_change_the_arguments_the_loop_recorded(tmp_path: Path) -> None:
    source = (
        "async def run(session, arguments):\n    arguments['text'] = 'changed'\n    return {}\n"
    )
    ctx = make_comb_context(tmp_path, [make_comb_package(source)])
    arguments: JsonObject = {"text": "original"}

    await run_tool(ctx, "echo_tool", arguments)

    assert arguments == {"text": "original"}


# ──────────────────────────────────────────────────────────────────────────────
# Loading refusals
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_package_the_source_does_not_hold_is_refused_and_recorded(tmp_path: Path) -> None:
    ctx = make_comb_context(tmp_path, [make_comb_package()])
    _source(ctx).blobs.clear()

    output = await run_tool(ctx, "echo_tool", {})

    assert output.is_error is True and "hive.tool.package_unavailable" in output.text
    refused = await ctx.trail.query(TrailQuery(kind="tool.load_refused"))
    assert refused[0].payload["code"] == "package_unavailable"


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("def broken(:\n", "import_failed"),
        ("raise RuntimeError('at import')\n", "import_failed"),
        ("def run(session, arguments):\n    return {}\n", "no_entry_point"),
        ("x = 1\n", "no_entry_point"),
    ],
)
async def test_a_package_that_cannot_become_a_tool_is_refused_with_its_code(
    tmp_path: Path, source: str, code: str
) -> None:
    ctx = make_comb_context(tmp_path, [make_comb_package(source)])

    output = await run_tool(ctx, "echo_tool", {})

    assert output.is_error is True and f"hive.tool.{code}" in output.text
    refused = await ctx.trail.query(TrailQuery(kind="tool.load_refused"))
    assert [event.payload["code"] for event in refused] == [code]
    assert "tool.loaded" not in await _kinds(ctx, "tool")


async def test_a_package_whose_spec_names_another_tool_is_refused_even_with_a_good_hash(
    tmp_path: Path,
) -> None:
    honest = make_comb_package(name="echo_tool")
    other = make_comb_package(name="other_tool")
    # An entry that names echo_tool but whose hash is that of a genuine, different tool's package:
    # the bytes verify against the hash, and still must not load as echo_tool.
    swapped = replace(
        honest.entry,
        package_sha256=other.entry.package_sha256,
        package_size_bytes=other.entry.package_size_bytes,
    )
    ctx = make_comb_context(tmp_path, [CombPackage(honest.spec, other.package, swapped)])

    output = await run_tool(ctx, "echo_tool", {})

    assert output.is_error is True and "hive.tool.spec_mismatch" in output.text
    refused = await ctx.trail.query(TrailQuery(kind="tool.load_refused"))
    assert [event.payload["code"] for event in refused] == ["spec_mismatch"]
