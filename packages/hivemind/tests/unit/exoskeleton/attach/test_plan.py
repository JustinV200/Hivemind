"""Unit tests for hivemind.exoskeleton.attach.plan: plan_attach's rules."""

from __future__ import annotations

import pytest
from builders.cells import make_capabilities

from hivemind.exoskeleton.attach.plan import AttachPlan, DisplaySource, plan_attach
from hivemind.exoskeleton.errors import AttachError
from hivemind.guard import CapabilitySet
from waggle.messages.task import ExoskeletonNeed

_DESKTOP = ExoskeletonNeed()
_FULL = CapabilitySet.parse("exoskeleton:display", "exoskeleton:audio", "exoskeleton:browser")


def test_a_cell_that_can_start_a_display_gets_a_lease_display() -> None:
    capabilities = make_capabilities(can_start_display=True)

    plan = plan_attach(_DESKTOP, capabilities, _FULL)

    assert plan == AttachPlan(display=DisplaySource.LEASE, audio=False, browser=False)
    assert plan.peripherals() == ("compound_eye", "antennae")


def test_the_operators_display_is_used_only_when_allowed_and_granted() -> None:
    capabilities = make_capabilities(
        has_display=True, real_display_allowed=True, can_start_display=True
    )
    granted = CapabilitySet.parse("exoskeleton:display", "exoskeleton:real_display")

    assert plan_attach(_DESKTOP, capabilities, granted).display is DisplaySource.RUNNING
    assert plan_attach(_DESKTOP, capabilities, _FULL).display is DisplaySource.LEASE


def test_a_running_display_the_operator_never_allowed_is_never_used() -> None:
    capabilities = make_capabilities(has_display=True, real_display_allowed=False)
    granted = CapabilitySet.parse("exoskeleton:display", "exoskeleton:real_display")

    with pytest.raises(AttachError, match="neither start a display nor lend"):
        plan_attach(_DESKTOP, capabilities, granted)


def test_a_desktop_without_the_display_capability_is_refused() -> None:
    capabilities = make_capabilities(can_start_display=True)

    with pytest.raises(AttachError, match="exoskeleton:display"):
        plan_attach(_DESKTOP, capabilities, CapabilitySet.parse("exoskeleton:browser"))


def test_audio_needs_the_tools_and_the_grant() -> None:
    need = ExoskeletonNeed(audio=True)
    no_tools = make_capabilities(can_start_display=True)
    tools = make_capabilities(can_start_display=True, has_audio=True)

    with pytest.raises(AttachError, match="no sound server tools"):
        plan_attach(need, no_tools, _FULL)
    with pytest.raises(AttachError, match="exoskeleton:audio"):
        plan_attach(need, tools, CapabilitySet.parse("exoskeleton:display"))
    assert plan_attach(need, tools, _FULL).audio


def test_a_browser_only_need_starts_the_browser_alone() -> None:
    capabilities = make_capabilities(has_browser=True, can_start_display=True, has_audio=True)

    plan = plan_attach(ExoskeletonNeed(browser_only=True), capabilities, _FULL)

    assert plan == AttachPlan(display=DisplaySource.NONE, audio=False, browser=True)
    assert plan.peripherals() == ("browser",)


def test_a_browser_only_need_is_refused_without_a_browser_or_its_grant() -> None:
    need = ExoskeletonNeed(browser_only=True)

    with pytest.raises(AttachError, match="no browser"):
        plan_attach(need, make_capabilities(), _FULL)
    with pytest.raises(AttachError, match="exoskeleton:browser"):
        plan_attach(need, make_capabilities(has_browser=True), CapabilitySet.parse("fs:read:**"))


def test_a_desktop_gets_the_browser_too_when_there_is_one_to_drive() -> None:
    capabilities = make_capabilities(can_start_display=True, has_browser=True)

    assert plan_attach(_DESKTOP, capabilities, _FULL).browser
    assert not plan_attach(
        _DESKTOP, capabilities, CapabilitySet.parse("exoskeleton:display")
    ).browser
