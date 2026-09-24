"""Unit tests for hivemind.workers.tools.exoskeleton.desktop: click, move, type, press, scroll.

Every test runs the tool through `ToolRegistry.execute` against a real CappingGate whose GUI
surface is a real ExoskeletonSurface over the fake display and input (builders.workers.
make_gui_context), so what is asserted is what the gate proposed, applied, verified or rolled
back, and what reached the fake peripherals.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/exoskeleton/desktop.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.exoskeleton.desktop for the module under test.
"""

from __future__ import annotations

import pytest
from builders.workers import make_gui_context, proposals_of, run_tool

from hivemind.exoskeleton import DisplaySource, Peripherals, Region, ScreenSize
from hivemind.exoskeleton.antennae import FakeAntennae, InputEvent, InputKind
from hivemind.exoskeleton.compound_eye import FakeCompoundEye, FakeScreen
from hivemind.llm import JsonObject
from hivemind.supervision.capping import Proposal, ProposalState, RiskTier
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.registry import ToolOutput
from waggle.clock import FakeClock
from waggle.messages.capping import GuiOp, GuiStep, MouseButton
from waggle.messages.labels import PostconditionKind
from waggle.messages.supervision import AlarmKind

_SIZE = ScreenSize(200, 100)
_BUTTON = Region(x=10, y=10, width=40, height=20)  # Turns green when a click lands on it.
_SECRET = "correct-horse-battery"  # noqa: S105 -- a probe value, never a real credential.


class _Desk:
    """A fake display whose button turns green when a click lands on it, and a context over it."""

    def __init__(self, display: DisplaySource = DisplaySource.LEASE) -> None:
        self.clock = FakeClock()
        self.screen = FakeScreen(_SIZE)
        self.antennae = FakeAntennae(_SIZE, on_input=self._react)
        peripherals = Peripherals(
            compound_eye=FakeCompoundEye(self.screen, self.clock), antennae=self.antennae
        )
        self.ctx: WorkerContext = make_gui_context(peripherals, self.clock, display=display)

    def _react(self, event: InputEvent) -> None:
        """Paint the button green when a click lands inside it, as an application would."""
        point = event.point
        inside = point is not None and 10 <= point.x < 50 and 10 <= point.y < 30
        if event.kind is InputKind.CLICK and inside:
            self.screen.paint(_BUTTON, (0, 200, 0))

    async def call(self, name: str, arguments: JsonObject) -> ToolOutput:
        """Run one tool call through this context's registry, as a Drone would."""
        return await run_tool(self.ctx, name, arguments)

    async def only_proposal(self) -> Proposal:
        """The one proposal the gate has seen."""
        (proposal,) = await proposals_of(self.ctx)
        return proposal


async def test_click_proposes_one_click_step_and_the_antennae_click_there() -> None:
    desk = _Desk()

    result = await desk.call("click", {"x": 20, "y": 15})

    assert result.text.startswith("state=VERIFIED")
    proposal = await desk.only_proposal()
    assert proposal.action.gui == (GuiStep(op=GuiOp.CLICK, x=20, y=15),)
    assert proposal.risk_tier is RiskTier.SCRATCH_WRITE
    assert [event.kind for event in desk.antennae.events] == [InputKind.CLICK]


async def test_click_on_the_operators_running_display_is_a_device_command() -> None:
    desk = _Desk(DisplaySource.RUNNING)

    await desk.call("click", {"x": 20, "y": 15})

    assert (await desk.only_proposal()).risk_tier is RiskTier.DEVICE_COMMAND


async def test_a_double_right_click_carries_its_button_and_op() -> None:
    desk = _Desk()

    await desk.call("click", {"x": 20, "y": 15, "button": "right", "double": True})

    step = (await desk.only_proposal()).action.gui[0]
    assert (step.op, step.button) == (GuiOp.DOUBLE_CLICK, MouseButton.RIGHT)
    assert desk.antennae.events[0].count == 2


async def test_irreversible_raises_the_tier_whatever_the_display() -> None:
    desk = _Desk()

    await desk.call("click", {"x": 20, "y": 15, "irreversible": True})

    assert (await desk.only_proposal()).risk_tier is RiskTier.IRREVERSIBLE


async def test_a_region_expectation_becomes_region_changed_and_holds_when_it_changes() -> None:
    desk = _Desk()

    result = await desk.call("click", {"x": 20, "y": 15, "expect": {"region": _BUTTON.spec()}})

    (postcondition,) = (await desk.only_proposal()).postconditions
    assert postcondition.kind is PostconditionKind.REGION_CHANGED
    assert postcondition.subject == _BUTTON.spec()
    assert result.text.startswith("state=VERIFIED")
    assert desk.ctx.telemetry.take_pending_alarms() == ()


async def test_a_failed_expectation_rolls_back_and_raises_an_alarm_at_once() -> None:
    desk = _Desk()

    result = await desk.call("click", {"x": 150, "y": 80, "expect": {"region": _BUTTON.spec()}})

    assert result.is_error
    assert result.text.startswith("state=ROLLED_BACK")
    assert (await desk.only_proposal()).state is ProposalState.ROLLED_BACK
    (alarm,) = desk.ctx.telemetry.take_pending_alarms()  # After ONE rollback, not three.
    assert alarm.kind is AlarmKind.POSTCONDITION_FAILED
    assert "REGION_CHANGED" in alarm.detail


async def test_a_point_off_the_screen_is_refused_before_anything_is_proposed() -> None:
    desk = _Desk()

    result = await desk.call("click", {"x": 500, "y": 15})

    assert "off the 200x100 screen" in result.text
    assert await proposals_of(desk.ctx) == []
    assert desk.ctx.telemetry.take_pending_alarms() == ()


async def test_move_proposes_one_move_step() -> None:
    desk = _Desk()

    await desk.call("move", {"x": 5, "y": 6})

    assert (await desk.only_proposal()).action.gui == (GuiStep(op=GuiOp.MOVE, x=5, y=6),)


async def test_type_marks_a_secret_and_never_echoes_it() -> None:
    desk = _Desk()

    result = await desk.call("type", {"text": _SECRET, "secret": True})

    step = (await desk.only_proposal()).action.gui[0]
    assert (step.op, step.secret) == (GuiOp.TYPE, True)
    assert _SECRET not in result.text
    assert _SECRET not in (await desk.only_proposal()).action.summary
    assert desk.antennae.events[0].text == _SECRET  # The step still types it.


async def test_a_secret_that_breaks_a_bound_is_refused_without_quoting_it() -> None:
    desk = _Desk()
    too_long = _SECRET * 400  # Past GuiStep's MAX_GUI_TEXT_CHARS.

    result = await desk.call("type", {"text": too_long, "secret": True})

    assert "text" in result.text and "at most" in result.text
    assert _SECRET not in result.text
    assert await proposals_of(desk.ctx) == []


async def test_press_proposes_the_chord_and_refuses_one_outside_the_key_pattern() -> None:
    desk = _Desk()

    await desk.call("press", {"keys": "ctrl+s"})
    refused = await desk.call("press", {"keys": "ctrl+$(reboot)"})

    assert (await desk.only_proposal()).action.gui == (GuiStep(op=GuiOp.PRESS, keys="ctrl+s"),)
    assert "keys" in refused.text and "pattern" in refused.text


async def test_scroll_drops_a_zero_axis_and_positions_the_pointer_when_asked() -> None:
    desk = _Desk()

    await desk.call("scroll", {"dx": 0, "dy": 3, "x": 40, "y": 50})

    assert (await desk.only_proposal()).action.gui == (GuiStep(op=GuiOp.SCROLL, dy=3, x=40, y=50),)


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"dx": 0, "dy": 0}, "non-zero dx or dy"),
        ({"dy": 1, "x": 5}, "both x and y"),
    ],
)
async def test_scroll_refuses_a_scroll_that_moves_nowhere_or_half_a_point(
    arguments: JsonObject, fragment: str
) -> None:
    desk = _Desk()

    result = await desk.call("scroll", arguments)

    assert fragment in result.text
    assert await proposals_of(desk.ctx) == []
