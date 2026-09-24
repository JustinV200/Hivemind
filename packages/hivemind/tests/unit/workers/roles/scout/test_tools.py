"""Unit tests for hivemind.workers.roles.scout.tools: the Scout's own narrow tool set.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/scout/tools.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.scout.tools for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.llm import make_bound, make_tool_call
from builders.workers import GUI_GRANTS, make_assignment, make_gui_context

from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton import Peripherals, ScreenSize
from hivemind.exoskeleton.browser.fake import FakeBrowser, login_site
from hivemind.exoskeleton.compound_eye import FakeCompoundEye, FakeScreen
from hivemind.guard import CapabilitySet
from hivemind.llm import FakeLLMProvider, ProviderCapabilities
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.bounded_loop.executor import LoopStoppedError
from hivemind.workers.roles.scout.tools import report_findings, scout_build_registry, scout_http_get
from hivemind.workers.tools import ToolInvocation
from waggle.clock import FakeClock

_SIZE = ScreenSize(200, 100)
_ALWAYS = {"read_file", "report_findings"}
_DISALLOWED_ACTIONS = (
    "click",
    "move",
    "type",
    "press",
    "scroll",
    "browser_click",
    "browser_fill",
    "browser_press",
    "say",
    "run_command",
    "write_file",
    "keep",
    "ask",
)


def _offered(parts: str, *, vision: bool = True, net: bool = True) -> set[str]:
    """Name the tools `scout_build_registry` offers for `parts` (eye, browser) and `net`."""
    clock = FakeClock()
    session = FakeSession(scratch_dir=Path("scratch"), clock=clock)
    peripherals = Peripherals(
        compound_eye=FakeCompoundEye(FakeScreen(_SIZE), clock) if "eye" in parts else None,
        browser=FakeBrowser(login_site(), clock) if "browser" in parts else None,
    )
    capabilities = ProviderCapabilities.full().model_copy(update={"vision": vision})
    grants = GUI_GRANTS if net else tuple(g for g in GUI_GRANTS if not g.startswith("net:"))
    ctx: WorkerContext = make_gui_context(
        peripherals,
        clock,
        session=session,
        bound=make_bound(provider=FakeLLMProvider(capabilities=capabilities)),
        capabilities=CapabilitySet.parse(*grants),
    )
    return {definition.name for definition in scout_build_registry(ctx).definitions()}


def test_with_nothing_attached_and_no_net_only_read_file_and_report_findings_are_offered() -> None:
    assert _offered("", net=False) == _ALWAYS


def test_http_request_is_offered_only_with_a_net_capability() -> None:
    assert _offered("", net=True) == _ALWAYS | {"http_request"}


def test_browser_reads_are_offered_with_a_browser_but_never_a_browser_action() -> None:
    offered = _offered("browser", vision=False, net=False)

    assert offered == _ALWAYS | {"browser_navigate", "browser_snapshot", "browser_read"}
    assert offered.isdisjoint({"browser_click", "browser_fill", "browser_press"})


def test_browser_screenshot_is_offered_only_for_a_vision_model() -> None:
    offered = _offered("browser", vision=True, net=False)

    assert "browser_screenshot" in offered
    assert _offered("browser", vision=False, net=False) - offered == set()


def test_see_is_offered_only_with_a_display_and_a_vision_model() -> None:
    assert "see" in _offered("eye browser", vision=True, net=False)
    assert "see" not in _offered("eye browser", vision=False, net=False)
    assert "see" not in _offered("browser", vision=True, net=False)  # No compound_eye attached.


@pytest.mark.parametrize("name", _DISALLOWED_ACTIONS)
async def test_a_disallowed_tool_is_never_offered_and_is_refused_if_called(name: str) -> None:
    clock = FakeClock()
    peripherals = Peripherals(browser=FakeBrowser(login_site(), clock))
    ctx = make_gui_context(peripherals, clock)
    registry = scout_build_registry(ctx)

    assert name not in {definition.name for definition in registry.definitions()}

    assignment = make_assignment(clock=clock)
    invocation = ToolInvocation(ctx=ctx, assignment=assignment)
    call = make_tool_call(name=name, arguments={})

    output = await registry.execute(invocation, call)

    assert output.text == f"no tool named {name!r} is offered."


async def test_report_findings_validates_before_ending_the_loop() -> None:
    clock = FakeClock()
    ctx = make_gui_context(Peripherals(), clock)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(clock=clock))

    result = await report_findings(invocation, {"feasible": True, "summary": ""})

    assert isinstance(result, str)
    assert "summary" in result


async def test_report_findings_ends_the_loop_with_the_validated_report() -> None:
    clock = FakeClock()
    ctx = make_gui_context(Peripherals(), clock)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(clock=clock))

    with pytest.raises(LoopStoppedError) as exc_info:
        await report_findings(
            invocation, {"feasible": False, "summary": "Could not tell; the site never loaded."}
        )

    outcome = exc_info.value.outcome
    assert outcome.claimed is True
    assert outcome.scout_report is not None
    assert outcome.scout_report.feasible is False


async def test_scout_http_get_refuses_anything_but_get() -> None:
    clock = FakeClock()
    ctx = make_gui_context(Peripherals(), clock, capabilities=CapabilitySet.parse(*GUI_GRANTS))
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(clock=clock))

    result = await scout_http_get(invocation, {"method": "POST", "url": "https://fixture.test/x"})

    assert "only GET" in result
