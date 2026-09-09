"""Unit tests for hivemind.cell.session: ExecSpec validation, run(), resolve_scratch_path."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.cell.errors import PathNotAllowedError
from hivemind.cell.fake import FakeSession
from hivemind.cell.session import CompletedCommand, ExecSpec, resolve_scratch_path, run
from waggle.clock import FakeClock


def test_exec_spec_rejects_empty_argv() -> None:
    with pytest.raises(ValidationError):
        ExecSpec(argv=())


async def test_run_collects_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    session = FakeSession(
        tmp_path,
        FakeClock(),
        responder={
            "echo": CompletedCommand(exit_code=3, stdout=b"out", stderr=b"err", duration_s=0.1)
        },
    )

    completed = await run(session, ExecSpec(argv=("echo", "hi")))

    assert completed.exit_code == 3
    assert completed.stdout == b"out"
    assert completed.stderr == b"err"
    assert completed.duration_s == 0.1


def test_resolve_scratch_path_resolves_relative_path_under_scratch(tmp_path: Path) -> None:
    resolved = resolve_scratch_path(tmp_path, Path("sub/file.txt"))

    assert resolved == (tmp_path / "sub" / "file.txt").resolve(strict=False)


def test_resolve_scratch_path_rejects_path_outside_scratch(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"

    with pytest.raises(PathNotAllowedError):
        resolve_scratch_path(tmp_path, outside)


def test_resolve_scratch_path_accepts_path_under_an_allowed_root(tmp_path: Path) -> None:
    allowed_root = tmp_path.parent / "allowed"
    target = allowed_root / "file.txt"

    resolved = resolve_scratch_path(tmp_path, target, allowed_paths=(allowed_root,))

    assert resolved == target.resolve(strict=False)


def test_resolve_scratch_path_collapses_dotdot_before_checking(tmp_path: Path) -> None:
    sneaky = tmp_path / "sub" / ".." / ".." / "outside.txt"

    with pytest.raises(PathNotAllowedError):
        resolve_scratch_path(tmp_path, sneaky)
