"""Unit tests for hivemind.supervision.capping.postconditions: check_postcondition per kind."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.capping import make_postcondition
from builders.cells import make_cell

from hivemind.cell import CellKind, CompletedCommand, FakeSession
from hivemind.supervision.capping.commands import CommandAdmission
from hivemind.supervision.capping.postconditions import check_postcondition
from waggle.clock import FakeClock
from waggle.messages.labels import PostconditionKind

# A Virtual Cell is always FULL, so every command may run there (ADR-0060).
_RUNS = CommandAdmission(make_cell(CellKind.VIRTUAL))

_UNSUPPORTED_KINDS = [
    PostconditionKind.HTTP_STATUS,
    PostconditionKind.ELEMENT_TEXT,
    PostconditionKind.JUDGE_RUBRIC,
]


async def test_check_postcondition_file_exists_holds_when_the_file_is_there(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    await session.put_file(Path("note.txt"), b"hi")
    pc = make_postcondition(PostconditionKind.FILE_EXISTS, subject="note.txt")

    outcome = await check_postcondition(session, 0, pc, _RUNS)

    assert outcome.has_held
    assert outcome.index == 0
    assert outcome.kind is PostconditionKind.FILE_EXISTS


async def test_check_postcondition_file_exists_fails_when_the_file_is_missing(
    tmp_path: Path,
) -> None:
    session = FakeSession(tmp_path, FakeClock())
    pc = make_postcondition(PostconditionKind.FILE_EXISTS, subject="missing.txt")

    outcome = await check_postcondition(session, 1, pc, _RUNS)

    assert not outcome.has_held
    assert outcome.index == 1


async def test_check_postcondition_file_absent_holds_when_the_file_is_missing(
    tmp_path: Path,
) -> None:
    session = FakeSession(tmp_path, FakeClock())
    pc = make_postcondition(PostconditionKind.FILE_ABSENT, subject="gone.txt")

    outcome = await check_postcondition(session, 0, pc, _RUNS)

    assert outcome.has_held


async def test_check_postcondition_file_absent_fails_when_the_file_exists(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    await session.put_file(Path("still-here.txt"), b"x")
    pc = make_postcondition(PostconditionKind.FILE_ABSENT, subject="still-here.txt")

    outcome = await check_postcondition(session, 0, pc, _RUNS)

    assert not outcome.has_held


async def test_check_postcondition_command_exits_zero_holds_on_success(tmp_path: Path) -> None:
    session = FakeSession(
        tmp_path,
        FakeClock(),
        responder={"true": CompletedCommand(exit_code=0, stdout=b"", stderr=b"", duration_s=0.0)},
    )
    pc = make_postcondition(PostconditionKind.COMMAND_EXITS_ZERO, argv=("true",))

    outcome = await check_postcondition(session, 0, pc, _RUNS)

    assert outcome.has_held
    assert "exit code 0" in outcome.observed


async def test_check_postcondition_test_passes_fails_on_nonzero_exit(tmp_path: Path) -> None:
    session = FakeSession(
        tmp_path,
        FakeClock(),
        responder={"pytest": CompletedCommand(exit_code=1, stdout=b"", stderr=b"", duration_s=0.0)},
    )
    pc = make_postcondition(PostconditionKind.TEST_PASSES, argv=("pytest",))

    outcome = await check_postcondition(session, 0, pc, _RUNS)

    assert not outcome.has_held
    assert "exit code 1" in outcome.observed


@pytest.mark.parametrize("kind", _UNSUPPORTED_KINDS)
async def test_check_postcondition_reports_unsupported_kinds_honestly(
    kind: PostconditionKind, tmp_path: Path
) -> None:
    session = FakeSession(tmp_path, FakeClock())
    pc = make_postcondition(kind)

    outcome = await check_postcondition(session, 0, pc, _RUNS)

    assert not outcome.has_held
    assert outcome.observed == "unsupported in v0"


async def test_check_postcondition_runs_no_command_on_a_cell_that_may_not_run_one(
    tmp_path: Path,
) -> None:
    # A Real Cell at SCRATCH whose processes are not contained (make_cell's default): ADR-0060.
    session = FakeSession(
        tmp_path,
        FakeClock(),
        responder={"pytest": CompletedCommand(exit_code=0, stdout=b"", stderr=b"", duration_s=0.0)},
    )
    pc = make_postcondition(PostconditionKind.TEST_PASSES, argv=("pytest",))

    outcome = await check_postcondition(session, 2, pc, CommandAdmission(make_cell()))

    assert not outcome.has_held
    assert outcome.commands_refused
    assert outcome.observed.startswith("not run: ")
    assert "SCRATCH" in outcome.observed


async def test_check_postcondition_never_refuses_a_file_kind_on_such_a_cell(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    await session.put_file(Path("note.txt"), b"hi")
    pc = make_postcondition(PostconditionKind.FILE_EXISTS, subject="note.txt")

    outcome = await check_postcondition(session, 0, pc, CommandAdmission(make_cell()))

    assert outcome.has_held
    assert not outcome.commands_refused
