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
from hivemind.supervision.capping import PostconditionOutcome
from hivemind.wardens.acceptance import AcceptanceReport
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.ticks.results import _failure_cause, _gui_check
from hivemind.wardens.warden import Warden
from waggle.clock import FakeClock
from waggle.ids import IdKind, TaskId, new_id
from waggle.messages.labels import HoneyClearance, PostconditionKind
from waggle.messages.task import ResultCause, TaskOutcome, TaskResult


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


# ──────────────────────────────────────────────────────────────────────────────
# The typed cause a failed acceptance carries (ADR-0060)
# ──────────────────────────────────────────────────────────────────────────────


def _claim(cause: ResultCause | None = None) -> TaskResult:
    """Build a sub-bee's claim, with `cause` when it says its Cell refused a command."""
    return TaskResult(
        task_id=TaskId(new_id(IdKind.TASK, FakeClock())),
        attempt=1,
        outcome=TaskOutcome.CLAIMED,
        summary="Tried.",
        clearance=HoneyClearance.C1,
        artifacts=(),
        checked_by=None,
        handoff=None,
        spend=0.0,
        reason="The role completed its work.",
        cause=cause,
    )


def _report(*, commands_refused: bool) -> AcceptanceReport:
    """Build a failed report whose one criterion was a command, refused or simply failing."""
    failing = PostconditionOutcome(
        index=0,
        kind=PostconditionKind.TEST_PASSES,
        has_held=False,
        observed="not run" if commands_refused else "exit code 1",
        commands_refused=commands_refused,
    )
    return AcceptanceReport(outcomes=(failing,), passed=False, failing=(failing,))


def test_a_failed_acceptance_carries_the_claims_own_cause() -> None:
    claim = _claim(ResultCause.CELL_RUNS_NO_COMMANDS)

    assert _failure_cause(claim, _report(commands_refused=False)) is claim.cause


def test_a_refused_command_criterion_is_the_cause_when_the_claim_has_none() -> None:
    cause = _failure_cause(_claim(), _report(commands_refused=True))

    assert cause is ResultCause.CELL_RUNS_NO_COMMANDS


def test_an_ordinary_acceptance_failure_carries_no_cause() -> None:
    assert _failure_cause(_claim(), _report(commands_refused=False)) is None
