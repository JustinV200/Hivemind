"""Test hivemind.cli.entrance.console: every way the console cannot act is one clear line.

The console acts only through a running serve's loopback listener, with a key the operator
password opens, as a console that is not locked; each refusal here happens before anything is
sent, so a serve record is published by hand where one is needed.

Fits into the Hive:
    Mirrors src/hivemind/cli/entrance/console.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from builders.entrance.auth import PASSWORD, WRONG_PASSWORD
from builders.entrance.stand import stand_manifest
from typer.testing import CliRunner, Result

from hivemind.cli.app import app
from hivemind.cli.entrance import ServeRecord, entrance_tables, publish_serve_record
from hivemind.entrance.enrol import DeviceStatus, trail_kind
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

runner = CliRunner()

# A listener nobody answers on: every refusal below comes before anything is sent to it.
_RECORD = ServeRecord(pid=1, host="127.0.0.1", port=9, started_at=datetime.now(UTC))


def _devices(path: Path, password: str = PASSWORD) -> Result:
    """Run ``hive entrance devices`` with ``password`` on stdin."""
    args = ["entrance", "devices", "--manifest", str(path), "--password-stdin"]
    return runner.invoke(app, args, input=f"{password}\n")


def _set_password(path: Path) -> None:
    """Set the operator password, as at install."""
    args = ["entrance", "operator", "password", "--manifest", str(path), "--password-stdin"]
    assert runner.invoke(app, args, input=f"{PASSWORD}\n").exit_code == 0


def _db(path: Path) -> Path:
    """The Hive's database file."""
    manifest = load_manifest(path, {})
    return manifest.resolve_path(manifest.hive.db)


def test_a_console_command_without_a_running_serve_says_to_start_it(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    _set_password(path)

    result = _devices(path)

    assert result.exit_code == 1
    assert "hive entrance devices refused: hive serve is not running" in result.output


def test_a_wrong_password_is_refused_before_anything_is_sent(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    _set_password(path)

    with publish_serve_record(_db(path), _RECORD):
        result = _devices(path, WRONG_PASSWORD)

    assert result.exit_code == 1
    assert "could not be opened with this password" in result.output
    assert WRONG_PASSWORD not in result.output


def test_before_any_operator_the_console_says_to_set_the_password(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)

    with publish_serve_record(_db(path), _RECORD):
        result = _devices(path)

    assert result.exit_code == 1
    assert "hive entrance operator password" in result.output


def test_a_locked_console_is_told_to_unlock_itself_offline(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    _set_password(path)
    asyncio.run(_lock_console(path))

    with publish_serve_record(_db(path), _RECORD):
        result = _devices(path)

    assert result.exit_code == 1
    assert "console is locked" in result.output and "unlock --console" in result.output


def test_no_password_on_stdin_is_refused_in_a_sentence(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)

    result = runner.invoke(
        app, ["entrance", "devices", "--manifest", str(path), "--password-stdin"], input=""
    )

    assert result.exit_code == 1
    assert "--password-stdin found no line for the operator password" in result.output


async def _lock_console(path: Path) -> None:
    """Lock the console in the tables, as a lockout would."""
    async with entrance_tables(load_manifest(path, {}), SystemClock()) as deps:
        console = next(
            device for device in await deps.store.list_devices() if device.loopback_bound
        )
        kind = trail_kind(DeviceStatus.APPROVED, DeviceStatus.LOCKED)
        event = deps.identity.event(deps.clock, kind, console.id, {"reason": "lockout"})
        await deps.store.update_device_status(
            console.id, DeviceStatus.APPROVED, DeviceStatus.LOCKED, event
        )
