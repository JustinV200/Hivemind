"""Unit tests for hivemind.exoskeleton.browser.targets: the one-element rule both browsers share."""

from __future__ import annotations

from hivemind.exoskeleton.browser.targets import PERIPHERAL, ambiguity, normalised
from hivemind.exoskeleton.errors import ElementNotFoundError, PeripheralError
from waggle.messages.capping import ElementTarget


def test_ambiguity_names_the_target_the_count_and_the_operation() -> None:
    target = ElementTarget(text="Log in")

    error = ambiguity(target, 2, "click")

    assert isinstance(error, PeripheralError)
    assert not isinstance(error, ElementNotFoundError)
    assert error.peripheral == PERIPHERAL
    assert error.operation == "click"
    assert "text='Log in' matches 2 elements" in str(error)


def test_normalised_collapses_whitespace_and_is_idempotent() -> None:
    once = normalised("  Log \n\t in  ")

    assert once == "Log in"
    assert normalised(once) == once
