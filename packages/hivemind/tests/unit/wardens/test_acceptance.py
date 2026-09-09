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

from hivemind.cell.fake import FakeSession
from hivemind.wardens.acceptance import run_acceptance
from waggle.clock import FakeClock
from waggle.messages.labels import Postcondition, PostconditionKind


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
    report = await run_acceptance(_session(), ())

    assert report.passed is True
    assert report.outcomes == ()
    assert report.failing == ()


async def test_run_acceptance_passes_when_every_criterion_holds() -> None:
    session = _session()
    await session.put_file(Path("output.txt"), b"hello")

    report = await run_acceptance(session, (_postcondition(),))

    assert report.passed is True
    assert report.failing == ()
    assert report.outcomes[0].has_held is True


async def test_run_acceptance_fails_when_a_criterion_does_not_hold() -> None:
    session = _session()  # output.txt was never written

    report = await run_acceptance(session, (_postcondition(),))

    assert report.passed is False
    assert len(report.failing) == 1
    assert report.failing[0].kind is PostconditionKind.FILE_EXISTS
    assert report.failing[0].has_held is False


async def test_run_acceptance_fails_closed_for_an_unsupported_kind() -> None:
    session = _session()
    unsupported = _postcondition(
        kind=PostconditionKind.HTTP_STATUS, subject="https://example.org", expected="200"
    )

    report = await run_acceptance(session, (unsupported,))

    assert report.passed is False
    assert report.failing[0].observed == "unsupported in v0"


async def test_run_acceptance_reports_every_criterion_even_when_one_fails() -> None:
    session = _session()
    await session.put_file(Path("output.txt"), b"hello")
    missing = _postcondition(subject="missing.txt")

    report = await run_acceptance(session, (_postcondition(), missing))

    assert len(report.outcomes) == 2
    assert report.passed is False
    assert len(report.failing) == 1
    assert report.failing[0].index == 1
