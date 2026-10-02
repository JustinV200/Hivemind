"""Tests for hivemind.wardens.acceptance: the Warden-side half of a task's acceptance criteria.

Fits into the Hive:
    Mirrors src/hivemind/wardens/acceptance.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.acceptance for the module under test.
    - .claude/roadmap.md step 3.18 for the acceptance rule this module enforces.
"""

from __future__ import annotations

from pathlib import Path

from builders.cells import make_cell

from hivemind.cell import CellKind
from hivemind.cell.fake import FakeSession
from hivemind.supervision.capping import CommandAdmission, PostconditionOutcome
from hivemind.wardens.acceptance import run_acceptance
from waggle.clock import FakeClock
from waggle.messages.labels import Postcondition, PostconditionKind

# A Virtual Cell is always FULL, so a command criterion may run there (ADR-0060).
_RUNS = CommandAdmission(make_cell(CellKind.VIRTUAL))


def _session() -> FakeSession:
    return FakeSession(scratch_dir=Path("scratch"), clock=FakeClock())


def _postcondition(**overrides: object) -> Postcondition:
    fields: dict[str, object] = {
        "kind": PostconditionKind.FILE_EXISTS,
        "subject": "output.txt",
        "argv": (),
        "expected": None,
    }
    fields.update(overrides)
    return Postcondition(**fields)


async def test_run_acceptance_passes_vacuously_with_no_postconditions() -> None:
    report = await run_acceptance(_session(), (), _RUNS)

    assert report.passed is True
    assert report.outcomes == ()
    assert report.failing == ()


async def test_run_acceptance_passes_when_every_criterion_holds() -> None:
    session = _session()
    await session.put_file(Path("output.txt"), b"hello")

    report = await run_acceptance(session, (_postcondition(),), _RUNS)

    assert report.passed is True
    assert report.failing == ()
    assert report.outcomes[0].has_held is True


async def test_run_acceptance_fails_when_a_criterion_does_not_hold() -> None:
    session = _session()  # output.txt was never written

    report = await run_acceptance(session, (_postcondition(),), _RUNS)

    assert report.passed is False
    assert len(report.failing) == 1
    assert report.failing[0].kind is PostconditionKind.FILE_EXISTS
    assert report.failing[0].has_held is False


async def test_run_acceptance_fails_closed_for_an_unsupported_kind() -> None:
    session = _session()
    unsupported = _postcondition(
        kind=PostconditionKind.HTTP_STATUS, subject="https://example.org", expected="200"
    )

    report = await run_acceptance(session, (unsupported,), _RUNS)

    assert report.passed is False
    assert report.failing[0].observed == "unsupported in v0"


async def test_run_acceptance_reports_every_criterion_even_when_one_fails() -> None:
    session = _session()
    await session.put_file(Path("output.txt"), b"hello")
    missing = _postcondition(subject="missing.txt")

    report = await run_acceptance(session, (_postcondition(), missing), _RUNS)

    assert len(report.outcomes) == 2
    assert report.passed is False
    assert len(report.failing) == 1
    assert report.failing[0].index == 1


# ──────────────────────────────────────────────────────────────────────────────
# Page criteria: checked on the task's Exoskeleton (roadmap step 6.7)
# ──────────────────────────────────────────────────────────────────────────────

_ARRIVED = _postcondition(
    kind=PostconditionKind.URL_MATCHES, subject="page", expected="http://127.0.0.1:8000/home*"
)


async def test_a_page_criterion_goes_to_the_exoskeleton_and_the_rest_to_the_session() -> None:
    session = _session()
    await session.put_file(Path("output.txt"), b"hello")
    seen: list[tuple[int, PostconditionKind]] = []

    async def gui_check(index: int, criterion: Postcondition) -> PostconditionOutcome:
        seen.append((index, criterion.kind))
        return PostconditionOutcome(index=index, kind=criterion.kind, has_held=True, observed="ok")

    report = await run_acceptance(session, (_postcondition(), _ARRIVED), _RUNS, gui_check)

    assert report.passed is True
    assert seen == [(1, PostconditionKind.URL_MATCHES)]  # FILE_EXISTS never reached it.


async def test_a_page_criterion_with_no_exoskeleton_fails_closed() -> None:
    report = await run_acceptance(_session(), (_ARRIVED,), _RUNS)

    assert report.passed is False
    assert report.failing[0].observed == "no Exoskeleton is attached to check it on"


async def test_a_command_criterion_fails_unrun_on_a_cell_that_may_not_run_commands() -> None:
    # make_cell's default: a Real Cell at SCRATCH whose processes are not contained (ADR-0060).
    command = _postcondition(
        kind=PostconditionKind.COMMAND_EXITS_ZERO, subject="tests", argv=("pytest",)
    )

    report = await run_acceptance(_session(), (command,), CommandAdmission(make_cell()))

    assert report.passed is False
    assert report.failing[0].commands_refused is True
