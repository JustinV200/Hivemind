"""Tests for hivemind.workers.roles.drone.outcome.executor: a tool's media and failure pass through.

Split by feature (codingrules 14.2) from test_executor.py: these cover roadmap step 6.5's
`ToolOutput`, whose media must reach the model as `ToolResultPart.media` and nowhere else, and
whose explicit failure must mark the result as an error.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/drone/outcome/executor.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.drone.outcome.executor for the module under test.
"""

from __future__ import annotations

from builders.llm import make_tool_call
from builders.workers import make_assignment, make_context

from hivemind.llm import AudioPart, ImagePart, JsonObject, ToolDefinition
from hivemind.workers.roles.drone.outcome.executor import _RecordingExecutor
from hivemind.workers.tools import ToolInvocation, ToolOutput, ToolRegistry, ToolRunner, ToolSpec

_IMAGE = ImagePart(media_type="image/png", data_base64="iVBORw0KGgo=")
_AUDIO = AudioPart(media_type="audio/wav", data_base64="UklGRg==")
_SCHEMA: JsonObject = {"type": "object", "properties": {}, "additionalProperties": False}


async def _looks(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Return a screenshot and a recording beside a line of text, as see and listen do."""
    return ToolOutput(text="captured", media=(_IMAGE, _AUDIO))


async def _refused(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Return a result whose text does not read as a failure but is one, as a judge's REJECT is."""
    return ToolOutput(text="judge=REJECT (no); state=VERIFIED", is_error=True)


def _executor(name: str, run: ToolRunner) -> _RecordingExecutor:
    ctx = make_context()
    spec = ToolSpec(
        definition=ToolDefinition(name=name, description="d", parameters=_SCHEMA), run=run
    )
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())
    return _RecordingExecutor(ToolRegistry([spec]), invocation, ctx.telemetry, 0.9)


async def test_execute_passes_a_tools_media_through_to_the_result_part() -> None:
    executor = _executor("look", _looks)

    result = await executor.execute(make_tool_call(name="look"))

    assert result.content == "captured"
    assert result.media == (_IMAGE, _AUDIO)
    assert result.is_error is False


async def test_execute_keeps_only_the_text_in_its_record() -> None:
    executor = _executor("look", _looks)

    await executor.execute(make_tool_call(name="look"))

    (record,) = executor.records
    assert record.result_text == "captured"
    assert _IMAGE.data_base64 not in repr(record)


async def test_execute_marks_a_result_the_tool_flagged_as_an_error() -> None:
    executor = _executor("gated", _refused)

    result = await executor.execute(make_tool_call(name="gated"))

    assert result.is_error is True
    assert executor.records[0].is_error is True
