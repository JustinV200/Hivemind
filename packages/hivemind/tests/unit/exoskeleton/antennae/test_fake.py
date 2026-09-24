"""Unit tests for hivemind.exoskeleton.antennae.fake: FakeAntennae and InputEvent."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.antennae.fake import FakeAntennae, InputEvent, InputKind
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.geometry import Point, ScreenSize
from waggle.messages.capping import MouseButton


async def test_inputs_are_recorded_in_order_and_announced() -> None:
    heard: list[InputEvent] = []
    antennae = FakeAntennae(ScreenSize(100, 50), on_input=heard.append)

    await antennae.click(Point(5, 6), MouseButton.LEFT, 2)
    await antennae.type_text("hello")
    await antennae.press("Return")

    assert [event.kind for event in antennae.events] == [
        InputKind.CLICK,
        InputKind.TYPE,
        InputKind.PRESS,
    ]
    assert heard == list(antennae.events)
    assert await antennae.pointer() == Point(5, 6)


async def test_typed_text_never_appears_in_an_events_repr() -> None:
    antennae = FakeAntennae(ScreenSize(100, 50))

    await antennae.type_text("hunter2")

    assert "hunter2" not in repr(antennae.events[0])
    assert antennae.events[0].text == "hunter2"


async def test_the_fake_refuses_what_the_real_backend_refuses() -> None:
    antennae = FakeAntennae(ScreenSize(100, 50))

    with pytest.raises(PeripheralError, match="off the"):
        await antennae.move(Point(100, 1))
    with pytest.raises(PeripheralError, match="count"):
        await antennae.click(Point(1, 1), MouseButton.LEFT, 0)
    assert antennae.events == ()


async def test_fail_with_fails_exactly_the_next_call() -> None:
    antennae = FakeAntennae(ScreenSize(100, 50))
    antennae.fail_with("display gone")

    with pytest.raises(PeripheralError, match="display gone"):
        await antennae.press("a")
    await antennae.press("a")

    assert len(antennae.events) == 1
