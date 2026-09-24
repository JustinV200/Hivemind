"""Tests for hivemind.cli.honey.context: the group's options, the operator's reader, run_or_exit.

Fits into the Hive:
    Mirrors src/hivemind/cli/honey/context.py (codingrules section 3). `open_access` is exercised
    against a real fake-provider manifest (`harness`), and with its EMBEDDER on a kind that
    cannot embed.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.honey.context for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner
from unit.cli.honey.harness import bind_embedder, db_path, make_hive

from hivemind.cell import HoneyClearance
from hivemind.cli.app import app
from hivemind.cli.honey.context import (
    EXIT_BAD_INPUT,
    EXIT_REFUSED,
    HUMAN_ACTOR,
    HoneyCliContext,
    human_identity,
    open_access,
    reader_for,
    run_or_exit,
)
from hivemind.honey_store import LoweringInputError
from hivemind.honey_store.browse import BrowseNotFoundError, BrowsePathError
from hivemind.honey_store.scope import is_readable
from hivemind.manifest import load_manifest


def _context(tmp_path: Path, clearance: HoneyClearance = HoneyClearance.C2) -> HoneyCliContext:
    """Load a fake-provider Hive into the group's shared context."""
    manifest = make_hive(tmp_path)
    return HoneyCliContext(
        manifest=load_manifest(manifest, environ={}), db=db_path(manifest), clearance=clearance
    )


@pytest.mark.parametrize("command", ["query", "ls", "cat", "relabel", "reembed", "review"])
def test_a_subcommands_help_needs_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.chdir(tmp_path)  # No hive.toml here for the default --manifest to find.

    result = CliRunner().invoke(app, ["honey", command, "--help"])

    assert result.exit_code == 0, result.output
    assert "Usage" in result.stdout


def test_a_subcommand_without_a_manifest_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["honey", "stats"])

    assert result.exit_code == 2
    assert "hive.toml" in result.stderr


def test_reader_for_reads_every_scope_up_to_the_groups_clearance(tmp_path: Path) -> None:
    cli_ctx = _context(tmp_path, HoneyClearance.C1)

    reader = reader_for(cli_ctx)

    assert (reader.requester, reader.ceiling) == (cli_ctx.manifest.hive.id, HoneyClearance.C1)
    assert is_readable("task:task_01ARZ3NDEKTSV4RRFFQ69G5FAV", reader.capabilities)


def test_human_identity_names_the_human(tmp_path: Path) -> None:
    cli_ctx = _context(tmp_path)

    identity = human_identity(cli_ctx.manifest)

    assert (identity.hive_id, identity.actor) == (cli_ctx.manifest.hive.id, HUMAN_ACTOR)


def test_open_access_reports_both_slots_bound(tmp_path: Path) -> None:
    opened = open_access(_context(tmp_path))

    assert opened.bindings.embedder.model == "test-model"
    assert opened.bindings.ripener.model == "test-model"
    assert opened.bindings.embedder.reason is None


def test_open_access_reports_the_judge_slot_or_that_lowering_is_switched_off(
    tmp_path: Path,
) -> None:
    cli_ctx = _context(tmp_path)
    switched_off = cli_ctx.manifest.honey.lowering.model_copy(update={"enabled": False})
    honey = cli_ctx.manifest.honey.model_copy(update={"lowering": switched_off})
    off = HoneyCliContext(
        manifest=cli_ctx.manifest.model_copy(update={"honey": honey}),
        db=cli_ctx.db,
        clearance=cli_ctx.clearance,
    )

    bound = open_access(cli_ctx)
    unused = open_access(off)

    assert (bound.bindings.judge.model, bound.bindings.judge.reason) == ("test-model", None)
    assert unused.bindings.judge.model is None
    assert unused.bindings.judge.reason == "[honey.lowering] enabled = false"
    assert unused.access.judge is None  # build_honey_access built no judge either.


def test_open_access_reports_why_a_slot_cannot_serve(tmp_path: Path) -> None:
    cli_ctx = _context(tmp_path)
    bind_embedder(tmp_path / "hive.toml", hosted=True)
    rebound = HoneyCliContext(
        manifest=load_manifest(tmp_path / "hive.toml", environ={}),
        db=cli_ctx.db,
        clearance=cli_ctx.clearance,
    )

    opened = open_access(rebound)

    assert opened.bindings.embedder.model is None
    assert opened.bindings.embedder.reason is not None
    assert "embedding_unsupported" in opened.bindings.embedder.reason


async def _raise(error: Exception) -> None:
    """A command's async work that fails with `error`."""
    raise error


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (BrowsePathError("/x", "no such folder"), EXIT_BAD_INPUT),
        (LoweringInputError("it is empty"), EXIT_BAD_INPUT),
        (BrowseNotFoundError("/hive/x"), EXIT_REFUSED),
    ],
)
def test_run_or_exit_turns_a_typed_error_into_an_exit_code(error: Exception, code: int) -> None:
    with pytest.raises(typer.Exit) as exited:
        run_or_exit(_raise(error))

    assert exited.value.exit_code == code


def test_run_or_exit_returns_the_works_result() -> None:
    async def work() -> int:
        return 7

    assert run_or_exit(work()) == 7
