"""Unit tests for hivemind.workers.tools.exoskeleton.offer: which tools a Worker is offered.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/exoskeleton/offer.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.exoskeleton.offer for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.llm import make_bound
from builders.workers import make_context, make_gui_context

from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton import Peripherals, ScreenSize
from hivemind.exoskeleton.antennae import FakeAntennae
from hivemind.exoskeleton.browser.fake import FakeBrowser, login_site
from hivemind.exoskeleton.buzz import FakeBuzz
from hivemind.exoskeleton.compound_eye import FakeCompoundEye, FakeScreen
from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    BoundTranscriber,
    DirectTranscriptionGate,
    Ears,
    FakeLLMProvider,
    FakeTranscription,
    ProviderCapabilities,
)
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.exoskeleton import (
    ACTION_TOOL_NAMES,
    READ_TOOL_NAMES,
    exoskeleton_specs,
)
from waggle.clock import FakeClock

_SIZE = ScreenSize(200, 100)
_DESKTOP = {"click", "move", "type", "press", "scroll"}
_BROWSER = {
    "browser_navigate",
    "browser_click",
    "browser_fill",
    "browser_press",
    "browser_snapshot",
    "browser_read",
}
_EARS = Ears(
    gate=DirectTranscriptionGate(),
    bound=BoundTranscriber(
        slot=ModelSlot.TRANSCRIBER, binding="t", provider=FakeTranscription(), model="speech"
    ),
)


def _offered(parts: str, *, vision: bool = True, audio: bool = True, ears: bool = True) -> set[str]:
    """Name the tools offered for the peripherals in `parts` (eye, hands, browser, buzz)."""
    clock = FakeClock()
    session = FakeSession(scratch_dir=Path("scratch"), clock=clock)
    peripherals = Peripherals(
        compound_eye=FakeCompoundEye(FakeScreen(_SIZE), clock) if "eye" in parts else None,
        antennae=FakeAntennae(_SIZE) if "hands" in parts else None,
        browser=FakeBrowser(login_site(), clock) if "browser" in parts else None,
        buzz=FakeBuzz(session) if "buzz" in parts else None,
    )
    capabilities = ProviderCapabilities.full().model_copy(update={"vision": vision, "audio": audio})
    ctx: WorkerContext = make_gui_context(
        peripherals,
        clock,
        session=session,
        bound=make_bound(provider=FakeLLMProvider(capabilities=capabilities)),
        ears=_EARS if ears else None,
    )
    return {spec.definition.name for spec in exoskeleton_specs(ctx)}


def test_a_terminal_only_task_is_offered_no_exoskeleton_tool() -> None:
    assert exoskeleton_specs(make_context()) == ()


@pytest.mark.parametrize(
    ("parts", "vision", "expected"),
    [
        ("hands", True, _DESKTOP),
        ("eye hands", True, _DESKTOP | {"see"}),
        ("eye hands", False, _DESKTOP),
        ("browser", False, _BROWSER),
        ("browser", True, _BROWSER | {"browser_screenshot"}),
    ],
)
def test_sight_and_touch_tools_follow_the_peripherals_and_the_models_vision(
    parts: str, vision: bool, expected: set[str]
) -> None:
    assert _offered(parts, vision=vision) == expected


@pytest.mark.parametrize(
    ("audio", "ears", "expected"),
    [
        (False, True, {"listen", "say"}),  # The transcriber hears for a deaf model.
        (True, False, {"listen", "say"}),  # The model hears the recording itself.
        (False, False, {"say"}),  # Nothing could hear what listen records.
    ],
)
def test_listen_is_offered_only_when_something_can_hear_and_say_whenever_buzz_is_there(
    audio: bool, ears: bool, expected: set[str]
) -> None:
    assert _offered("buzz", audio=audio, ears=ears) == expected


def test_media_tools_wait_for_a_fallback_that_could_take_their_media_too() -> None:
    clock = FakeClock()
    peripherals = Peripherals(
        compound_eye=FakeCompoundEye(FakeScreen(_SIZE), clock),
        browser=FakeBrowser(login_site(), clock),
        buzz=FakeBuzz(FakeSession(scratch_dir=Path("scratch"), clock=clock)),
    )
    full = ProviderCapabilities.full().model_copy(update={"audio": True})
    blind = full.model_copy(update={"vision": False, "audio": False})
    bound = make_bound(
        provider=FakeLLMProvider(capabilities=full),
        fallback=make_bound(provider=FakeLLMProvider(capabilities=blind)),
    )
    ctx = make_gui_context(peripherals, clock, bound=bound, ears=None)

    offered = {spec.definition.name for spec in exoskeleton_specs(ctx)}

    # No screenshot and no raw audio: the fallback would refuse either, mid-task.
    assert {"see", "browser_screenshot", "listen"}.isdisjoint(offered)
    assert {"browser_snapshot", "say"} <= offered


def test_every_offered_tool_either_acts_or_reads_never_both() -> None:
    everything = _offered("eye hands browser buzz")

    assert ACTION_TOOL_NAMES.isdisjoint(READ_TOOL_NAMES)
    assert everything == ACTION_TOOL_NAMES | READ_TOOL_NAMES
    assert len(everything) == 15  # 5 desktop, see, 4 browser actions, 3 page reads, listen, say.
