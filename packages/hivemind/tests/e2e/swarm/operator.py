"""Run the operator's `hive` commands against a Hive's database, and read the rows they print.

An operator looks at the Swarm with `hive cells list` and `hive swarm list`: each reads the Hive's
manifest and its SQLite file, never the running process, so a command run while the Hive serves
sees exactly what the Hive has written so far. A scenario that wants to prove what the operator
would be shown runs the command through Typer's runner, as the CLI scenarios do, rather than
reading the registry itself. The commands start event loops of their own, which a worker thread has
and the scenario's thread (running the Hive) does not, so `operator` runs the command off the loop.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 end-to-end
    scenarios; calls `hivemind.cli.app` through Typer's `CliRunner`.

Key invariants:
    - Structlog's lines are captured, so a command's standard output holds only what it prints and
      `--json` output parses as it is.
    - A command that fails fails the test at once with its own words, not with a parse error.

See Also:
    - tests/e2e/test_swarm_cli.py for the full command group exercised on a device.
    - hivemind.cli.readback.cells for the listing the rows come from.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from structlog.testing import capture_logs
from typer.testing import CliRunner, Result

from hivemind.cli.app import app

__all__ = ["cell_rows", "device_rows", "operator", "rows_of", "swarm"]


async def operator(*command: str) -> Result:
    """Run one `hive` command as the operator types it, off the loop the Hive runs on.

    Args:
        *command: The command line after `hive`.

    Returns:
        The runner's result: exit code, standard output and standard error.
    """

    def invoke() -> Result:
        with capture_logs():
            return CliRunner().invoke(app, list(command))

    return await asyncio.to_thread(invoke)


def rows_of(result: Result) -> list[dict[str, Any]]:
    """Return the rows a `--json` listing printed; the command must have succeeded."""
    assert result.exit_code == 0, result.stderr
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return rows


async def cell_rows(manifest: Path) -> list[dict[str, Any]]:
    """The rows of `hive cells list --json`: the Hive Stand's Cell, then each device's."""
    return rows_of(await operator("cells", "list", "--manifest", str(manifest), "--json"))


async def swarm(manifest: Path, *arguments: str) -> Result:
    """Run `hive swarm --manifest M ARGUMENTS`: invite, list, access, revoke, run."""
    return await operator("swarm", "--manifest", str(manifest), *arguments)


async def device_rows(manifest: Path) -> list[dict[str, Any]]:
    """The rows of `hive swarm list --json`: every enrolled device, revoked ones included."""
    return rows_of(await swarm(manifest, "list", "--json"))
