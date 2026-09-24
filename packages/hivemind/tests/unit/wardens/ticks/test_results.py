"""Unit tests for hivemind.wardens.ticks.results: page criteria read the sub-bee's own browser.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/results.py (codingrules section 3). The whole accept path
    (acceptance, retire, report) runs through a real Warden in test_warden_spawn_and_accept.py;
    this module covers the one piece that needs an attached Exoskeleton: which check a URL_MATCHES
    or ELEMENT_TEXT criterion is routed to.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.acceptance for run_acceptance, which calls the check built here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from builders.capping import make_postcondition

from hivemind.exoskeleton.attach import Peripherals
from hivemind.exoskeleton.browser import Browser
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.ticks.results import _gui_check
from hivemind.wardens.warden import Warden
from waggle.clock import FakeClock
from waggle.messages.labels import PostconditionKind


class _Page:
    """The browser the Warden attached for the sub-bee, left on the page the task reached."""

    async def url(self) -> str:
        return "http://127.0.0.1:8000/home"


def _warden(clock: FakeClock) -> Warden:
    # Only the clock is read; a whole Warden is exercised in test_warden_spawn_and_accept.py.
    return cast(Warden, SimpleNamespace(_deps=SimpleNamespace(clock=clock)))


def _sub_bee(peripherals: Peripherals | None) -> SubBee:
    handle = None if peripherals is None else SimpleNamespace(peripherals=peripherals)
    return cast(SubBee, SimpleNamespace(exoskeleton=handle))


def test_a_sub_bee_with_no_exoskeleton_gets_no_page_check() -> None:
    assert _gui_check(_warden(FakeClock()), _sub_bee(None)) is None


async def test_the_page_check_reads_the_browser_attached_for_the_sub_bee() -> None:
    peripherals = Peripherals(browser=cast(Browser, _Page()))
    check = _gui_check(_warden(FakeClock()), _sub_bee(peripherals))
    arrived = make_postcondition(
        PostconditionKind.URL_MATCHES, subject="page", expected="http://127.0.0.1:8000/home"
    )

    assert check is not None
    outcome = await check(2, arrived)

    assert (outcome.index, outcome.has_held) == (2, True)
