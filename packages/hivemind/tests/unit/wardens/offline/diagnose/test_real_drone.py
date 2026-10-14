"""Tests for the diagnostic Drone as the real role: what its model is offered, told and may do.

The rest of this package runs the Drone as a script, which can do anything a script is told. These
tests run the real `Drone` role (its prompt, its tool registry, its Guard and its Capping gate) on
a model scripted at the provider, because what a search depends on is what only the real role
shows: that the goal ceiling leaves its model the tools a diagnosis needs and takes the ones that
ask or reach out, what a command tells it, and that the closing text it writes is where the Warden
reads its cause from.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/diagnose/assign.py and `.drone` (codingrules section 3)
    from the model's side, driven through a real Warden on a device's deps that holds ceilings and
    a model server (builders.diagnose.start_thinking_nuc_scene).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.diagnose.assign for the task and the ceiling these tests read.
    - hivemind.workers.roles.drone for the role that runs.
"""

from __future__ import annotations

from builders.diagnose import diagnosed, nectar_text, start_thinking_nuc_scene
from builders.llm import make_tool_call
from builders.offline_warden import OfflineScene
from builders.quarantine import settle

from hivemind.llm import (
    FakeLLMProvider,
    LLMRequest,
    TextPart,
    ToolCall,
    ToolResultPart,
    text_response,
    tool_call_response,
)
from hivemind.wardens.offline.diagnose import Phase
from hivemind.wardens.ticks.honey import LOCAL_RUN_REASON
from hivemind.workers.tools.authorize import GUARD_REFUSAL_PREFIX

_CAUSE = "the resolver file names a host that is gone"
_CLOSING = f"CAUSE: {_CAUSE}\nI ran the lookup, saw it fail and removed the line."
_CHECK = ["sh", "-c", "getent hosts hive.example > out.txt 2>&1; echo exit=$? >> out.txt"]


def _call(tool: str, /, **arguments: object) -> ToolCall:
    """A call of `tool` the scripted model makes, with an id from the tool's name."""
    return make_tool_call(id=f"call_{tool}", name=tool, arguments=arguments)


def _words(request: LLMRequest) -> str:
    """Everything a call shows its model: the system prompt and the text of every message."""
    texts = [request.system or ""]
    texts += [
        part.text
        for message in request.messages
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    return "\n".join(texts)


def _tool_results(request: LLMRequest) -> dict[str, str]:
    """What the tools answered, as the call's conversation holds it, by the call's id."""
    return {
        part.call_id: part.content
        for message in request.messages
        for part in message.parts
        if isinstance(part, ToolResultPart)
    }


def _phase(scene: OfflineScene) -> Phase | None:
    """The search's phase, read through a call so mypy keeps no narrowing of the attribute."""
    search = scene.outage.investigation
    return None if search is None else search.phase


async def _searching(provider: FakeLLMProvider, *round_one: ToolCall) -> OfflineScene:
    """Cut the link of a Nuc whose Drone is the real role, and wait for the Drone to name a cause.

    Args:
        provider: What the Drone's model says; scripted here with `round_one`, then the closing.
        round_one: The tool calls its model makes first, all in one round.
    """
    calls = round_one or (_call("run_command", argv=_CHECK),)
    provider.script(tool_call_response(*calls), text_response(_CLOSING))
    scene = await start_thinking_nuc_scene(provider)
    scene.cut()
    await settle(lambda: _phase(scene) is Phase.VERIFYING)
    return scene


async def test_a_command_goes_through_the_gate_and_the_model_hears_its_verdict_only() -> None:
    provider = FakeLLMProvider()
    scene = await _searching(provider)

    answer = _tool_results(provider.calls[1])["call_run_command"]

    # A Drone whose ceiling named no `tool:*` would be refused here: its role's tools are one
    # wildcard entry, and a ceiling keeps only what one of its own entries covers.
    assert not answer.startswith(GUARD_REFUSAL_PREFIX), answer
    assert answer.startswith("state=") and "reason=" in answer  # What the Capping gate says ...
    assert "command not found" not in answer  # ... and not what the command printed.
    await scene.stop()


async def test_a_tool_that_asks_or_reaches_out_is_refused_by_the_guard_when_it_is_called() -> None:
    provider = FakeLLMProvider()
    reaching = (
        _call("ask", text="May I restart the network?"),
        _call("http_request", method="GET", url="https://hive.example/"),
        _call(
            "request_tool",
            name="dig",
            description="Look up a name.",
            required_capabilities=[],
            scope="cell",
        ),
    )
    scene = await _searching(provider, *reaching)

    answers = _tool_results(provider.calls[1])

    # The model is offered every tool its role has, and each call asks the Guard when it is made:
    # a question to a human, a network of its own and a tool from the Hive are not held, so nothing
    # was asked of anybody and nothing left the machine. (A write outside scratch is refused the
    # same way; `test_assign` shows the held set has none.)
    assert set(answers) == {"call_ask", "call_http_request", "call_request_tool"}
    for call_id, answer in answers.items():
        assert answer.startswith(GUARD_REFUSAL_PREFIX), (call_id, answer)
    assert scene.queen.questions == [] and scene.queen.nectar == []
    await scene.stop()


async def test_the_drones_honey_traffic_is_answered_by_its_warden_and_never_goes_up() -> None:
    provider = FakeLLMProvider()
    scene = await _searching(provider, _call("recall", query="dns"))

    answer = _tool_results(provider.calls[1])["call_recall"]

    # Its role holds the tool, but its task is not one the Queen has, so her Honey is not asked.
    assert answer == f"No Honey matched. {LOCAL_RUN_REASON}"
    await scene.stop()


async def test_the_model_is_told_why_it_is_there_and_how_its_ending_is_read() -> None:
    provider = FakeLLMProvider()
    scene = await _searching(provider)

    words = _words(provider.calls[0])

    assert "Diagnose and, if possible, restore the link to the Queen at hive.example." in words
    assert "`CAUSE:`" in words  # The rubric its Warden will check word for word.
    assert "never what it printed" in words  # What its command tool does not say.
    await scene.stop()


async def test_the_line_the_model_closes_with_is_where_the_warden_reads_the_cause() -> None:
    provider = FakeLLMProvider()
    scene = await _searching(provider)

    scene.restore()
    await settle(lambda: scene.outage.investigation is None)

    [event] = await diagnosed(scene)
    assert (event.payload["outcome"], event.payload["reason"]) == ("FIXED", "link_back")
    assert _CAUSE in await nectar_text(scene)
    assert len(provider.calls) == 2  # One round of tools, one closing message: nothing more asked.
    await scene.stop()
