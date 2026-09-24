"""Unit tests for hivemind.workers.tools.exoskeleton.expect: `expect` as postconditions.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/exoskeleton/expect.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.exoskeleton.expect for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import JsonValue

from hivemind.exoskeleton import Peripherals, ScreenSize
from hivemind.exoskeleton.browser.fake import FakeBrowser, login_site
from hivemind.exoskeleton.compound_eye import FakeCompoundEye, FakeScreen
from hivemind.workers.tools.exoskeleton import GuiArgumentError, expectations
from waggle.clock import FakeClock
from waggle.messages.capping import ElementTarget
from waggle.messages.labels import Postcondition, PostconditionKind

_CLOCK = FakeClock()
_EVERYTHING = Peripherals(
    compound_eye=FakeCompoundEye(FakeScreen(ScreenSize(200, 100)), _CLOCK),
    browser=FakeBrowser(login_site(), _CLOCK),
)


def test_no_expectation_declares_no_postcondition() -> None:
    assert expectations(None, _EVERYTHING) == ()


def test_a_url_is_url_matches_on_the_page() -> None:
    (postcondition,) = expectations({"url": "https://fixture.test/welcome*"}, _EVERYTHING)

    assert postcondition == Postcondition(
        kind=PostconditionKind.URL_MATCHES,
        subject="page",
        argv=(),
        expected="https://fixture.test/welcome*",
    )


def test_an_element_with_text_is_element_text_on_the_targets_subject() -> None:
    expect: JsonValue = {"element": {"role": "button", "name": "Log in"}, "text": "Log"}

    (postcondition,) = expectations(expect, _EVERYTHING)

    assert postcondition.kind is PostconditionKind.ELEMENT_TEXT
    assert postcondition.subject == "role=button;name=Log in"
    assert ElementTarget.from_subject(postcondition.subject) == ElementTarget(
        role="button", name="Log in"
    )
    assert postcondition.expected == "Log"


def test_a_region_is_region_changed_on_that_rectangle() -> None:
    (postcondition,) = expectations({"region": "10,10,40,20"}, _EVERYTHING)

    assert (postcondition.kind, postcondition.subject) == (
        PostconditionKind.REGION_CHANGED,
        "10,10,40,20",
    )
    assert postcondition.expected is None


@pytest.mark.parametrize(
    ("expect", "fragment"),
    [
        ("welcome", "must be an object"),
        ({}, "exactly one of"),
        ({"url": "about:blank", "region": "0,0,1,1"}, "exactly one of"),
        ({"title": "Welcome"}, "not ['title']"),
        ({"url": "about:blank", "text": "x"}, "text goes with expect.element only"),
        ({"element": {"role": "heading"}}, "needs expect.text"),
        ({"element": {"role": "heading", "label": "x"}, "text": "y"}, "exactly one way"),
        ({"region": "10,10,400,20"}, "does not fit the 200x100 screen"),
        ({"region": "ten,10,40,20"}, "'x,y,width,height'"),
        ({"url": ""}, "non-empty string"),
    ],
)
def test_a_malformed_expectation_is_refused_naming_what_is_wrong(
    expect: JsonValue, fragment: str
) -> None:
    with pytest.raises(GuiArgumentError) as raised:
        expectations(expect, _EVERYTHING)

    assert fragment in str(raised.value)


@pytest.mark.parametrize(
    ("expect", "peripherals", "missing"),
    [
        ({"url": "about:blank"}, Peripherals(compound_eye=_EVERYTHING.compound_eye), "browser"),
        ({"element": {"text": "x"}, "text": "x"}, Peripherals(), "browser"),
        ({"region": "0,0,1,1"}, Peripherals(browser=_EVERYTHING.browser), "display"),
    ],
)
def test_an_expectation_nothing_attached_could_check_is_refused(
    expect: JsonValue, peripherals: Peripherals, missing: str
) -> None:
    with pytest.raises(GuiArgumentError, match=missing):
        expectations(expect, peripherals)
