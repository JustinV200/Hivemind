"""Unit tests for hivemind.exoskeleton.surface.verify: check_structural, page acceptance."""

from __future__ import annotations

from datetime import timedelta

from builders.capping import make_postcondition
from builders.exoskeleton import drive

from hivemind.exoskeleton.attach import Peripherals
from hivemind.exoskeleton.surface import ACCEPTANCE_SETTLE_S, check_structural
from waggle.clock import FakeClock
from waggle.messages.capping import ElementTarget
from waggle.messages.labels import PostconditionKind

_HOME = "http://127.0.0.1:8000/home"


class _Page:
    """A page that lands on /home `redirect_s` seconds after the check starts, greeting alice."""

    def __init__(self, clock: FakeClock, redirect_s: float) -> None:
        self._clock, self._at = clock, clock.now() + timedelta(seconds=redirect_s)

    async def url(self) -> str:
        return _HOME + "?session=abc123" if self._clock.now() >= self._at else _HOME[:-4] + "login"

    async def element_text(self, target: ElementTarget) -> str | None:
        return "Welcome, alice" if target.role == "heading" else None


def _peripherals(clock: FakeClock, redirect_s: float = 0.0) -> Peripherals:
    return Peripherals(browser=_Page(clock, redirect_s))  # type: ignore[arg-type]  # A stand-in.


async def test_a_late_redirect_still_holds_within_the_settle_time() -> None:
    clock = FakeClock()
    arrived = make_postcondition(
        PostconditionKind.URL_MATCHES, subject="page", expected=_HOME + "*"
    )

    outcome = await drive(clock, check_structural(_peripherals(clock, 1.0), clock, 3, arrived))

    assert (outcome.index, outcome.has_held) == (3, True)
    assert "abc123" not in outcome.observed  # The session parameter is masked, as recorded.


async def test_a_page_that_never_arrives_fails_once_the_settle_time_is_spent() -> None:
    clock = FakeClock()
    start = clock.now()
    arrived = make_postcondition(PostconditionKind.URL_MATCHES, subject="page", expected=_HOME)

    outcome = await drive(clock, check_structural(_peripherals(clock, 60.0), clock, 0, arrived))

    assert outcome.has_held is False
    assert outcome.observed == "http://127.0.0.1:8000/login"
    assert clock.now() - start >= timedelta(seconds=ACCEPTANCE_SETTLE_S)


async def test_element_text_holds_at_once() -> None:
    clock = FakeClock()
    greeted = make_postcondition(
        PostconditionKind.ELEMENT_TEXT, subject="role=heading", expected="alice"
    )

    outcome = await check_structural(_peripherals(clock), clock, 0, greeted)

    assert outcome.has_held is True


async def test_a_region_criterion_never_holds_without_a_before_digest() -> None:
    clock = FakeClock()
    changed = make_postcondition(PostconditionKind.REGION_CHANGED, subject="0,0,10,10")

    outcome = await drive(clock, check_structural(Peripherals(), clock, 0, changed))

    assert outcome.has_held is False
