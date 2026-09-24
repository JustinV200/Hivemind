"""Serve a Hive with ``hive serve``'s own composition and drive the ``hive`` CLI against it.

A test of a ``hive entrance`` command, or of ``hive remote``/``run --remote``/``inbox --remote``,
needs what an operator has: a Hive Stand whose operator password is set, ``hive serve`` running
(the real composition: a real Queen over the Hive's SQLite file, the Entrance on a loopback port
the system picks, publishing its serve record), and a terminal to type ``hive ...`` into.
``stand_manifest`` writes the manifest; ``set_password`` runs ``hive entrance operator password``
before anything serves; ``serving_stand`` runs ``serve_hive`` in the test's event loop; and
``Terminal.hive`` runs one ``hive`` command through ``CliRunner`` in a worker thread, because every
command runs its own event loop (``asyncio.run``), which cannot nest inside the test's. A laptop is
another ``Terminal`` whose environment points the user's config directory elsewhere.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/cli/entrance and .../cli/remote and by the end-to-end test of
    phase 10's second exit criterion.

Key invariants:
    - Every command goes through the real ``hive`` application, as a shell would run it.
    - The password is only ever handed over on stdin (``--password-stdin``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from builders.cli import fake_manifest
from builders.entrance.auth import PASSWORD
from typer.testing import CliRunner, Result

from hivemind.cli.app import app
from hivemind.cli.compose.entrance import build_served_hive, serve_hive
from hivemind.cli.entrance import read_serve_record
from hivemind.cli.landing import DeviceKey, LandingClient, entrance_address, open_http
from hivemind.entrance.enrol import DeviceDescription
from hivemind.entrance.runtime import HiveEntrance
from hivemind.llm import LLMRequest, LLMResponse, Responder, text_response
from hivemind.manifest import HiveManifest, load_manifest
from waggle.clock import SystemClock
from waggle.signing import Ed25519Signer

_DRONE_CPU = "[forage.roles.drone]\ncpu_cores = 0.5\n"  # fake_manifest's own Drone footprint.
_NO_CPU = "[forage.roles.drone]\ncpu_cores = 0.0\n"  # A footprint no machine load can refuse.
# The loopback listener on a port the system picks, and limits a scripted operator never meets.
ENTRANCE = (
    '\n[entrance]\nbind = "127.0.0.1:0"\nrate_limit_per_address = 1000\n'
    "rate_limit_per_device = 1000\n"
)

__all__ = [
    "ENTRANCE",
    "PASSWORD",
    "Stand",
    "Terminal",
    "json_of",
    "landing_client",
    "laptop_terminal",
    "redeem_invite",
    "serving_stand",
    "set_password",
    "stand_manifest",
]


def json_of(result: Result) -> Any:  # noqa: ANN401 -- one parsed JSON document, any shape.
    """Parse the one indented JSON document a ``--json`` command printed.

    The Hive served in the test's own process logs to standard output (structlog's default), and
    a line it logs while a command runs lands in that command's output too; the document itself is
    the block from a line that is exactly ``{`` to the last line that is exactly ``}``.

    Args:
        result: A ``--json`` command's result.

    Returns:
        The parsed document.
    """
    lines = result.stdout.splitlines()
    start = lines.index("{")
    end = len(lines) - 1 - lines[::-1].index("}")
    return json.loads("\n".join(lines[start : end + 1]))


def _silent(request: LLMRequest) -> LLMResponse:
    """Answer every model call with an empty object: a stand no test gives work to."""
    del request
    return text_response("{}")


@dataclass(frozen=True, slots=True)
class Terminal:
    """One shell on one machine: runs ``hive ...`` as that machine's user would.

    Attributes:
        env: Environment variables set for each command (a laptop's config directory).
        runner: The CliRunner every command goes through.
    """

    env: Mapping[str, str] | None = None
    runner: CliRunner = field(default_factory=CliRunner)

    async def hive(self, *args: str, stdin: str | None = None) -> Result:
        """Run ``hive ARGS`` in a worker thread, with ``stdin`` as its standard input.

        Args:
            *args: The command line after ``hive``.
            stdin: What the command reads from standard input (passwords, a yes).

        Returns:
            The command's result: exit code and output.
        """

        def invoke() -> Result:
            """Run the command on this worker thread."""
            return self.runner.invoke(app, list(args), input=stdin, env=self.env)

        return await asyncio.to_thread(invoke)


@dataclass(frozen=True, slots=True)
class Stand:
    """The Hive Stand: its manifest, and a terminal on it.

    Attributes:
        manifest_path: The Hive Manifest on disk.
        manifest: The same manifest, loaded.
        terminal: The Hive Stand's own shell.
    """

    manifest_path: Path
    manifest: HiveManifest
    terminal: Terminal = field(default_factory=Terminal)

    @property
    def db(self) -> Path:
        """The Hive's database file, as the manifest names it."""
        return self.manifest.resolve_path(self.manifest.hive.db)

    async def entrance(self, *args: str, stdin: str = f"{PASSWORD}\n") -> Result:
        """Run ``hive entrance ARGS --manifest ... --password-stdin`` with ``stdin``.

        Args:
            *args: The command line after ``hive entrance``.
            stdin: The operator password's line, then anything else the command reads.

        Returns:
            The command's result.
        """
        manifest = str(self.manifest_path)
        return await self.terminal.hive(
            "entrance", *args, "--manifest", manifest, "--password-stdin", stdin=stdin
        )


def stand_manifest(root: Path, extra: str = "") -> Path:
    """Write the Hive Stand's manifest under ``root``: the fake provider, a loopback Entrance.

    Args:
        root: The Hive Stand's directory.
        extra: More ``[entrance]`` lines (``lockout_attempts = 2``, say).

    Returns:
        The manifest's path.
    """
    path = fake_manifest(root)
    text = path.read_text(encoding="utf-8")
    # A Drone's CPU footprint weighs free cores, which a loaded test machine (other suites
    # running beside this one) can drive to zero, and a goal would then get no sub-bee at all.
    # A zero footprint takes the machine's load out of whether a stand's goals can run.
    text = text.replace(_DRONE_CPU, _NO_CPU)
    path.write_text(text + ENTRANCE + extra, encoding="utf-8")
    return path


async def set_password(manifest_path: Path, password: str = PASSWORD) -> Result:
    """Run ``hive entrance operator password`` the first time, as the operator does at install.

    Args:
        manifest_path: The Hive Stand's manifest.
        password: The operator password.

    Returns:
        The command's result.
    """
    args = ("entrance", "operator", "password", "--manifest", str(manifest_path))
    return await Terminal().hive(*args, "--password-stdin", stdin=f"{password}\n")


def laptop_terminal(config_home: Path) -> Terminal:
    """A laptop's shell: its user's config directory is ``config_home`` on every platform.

    Args:
        config_home: Where the laptop keeps its profiles and its device key.

    Returns:
        The terminal.
    """
    home = str(config_home)
    # XDG_CONFIG_HOME on Linux, APPDATA on Windows, HOME for macOS's Application Support.
    return Terminal(env={"XDG_CONFIG_HOME": home, "APPDATA": home, "HOME": home})


@asynccontextmanager
async def landing_client(stand: Stand) -> AsyncIterator[LandingClient]:
    """The CLI's own Landing Board client on the running serve's published loopback listener.

    Args:
        stand: The serving Hive Stand.

    Yields:
        The client; closed when the block exits.
    """
    record = read_serve_record(stand.db)
    assert record is not None, "hive serve must be running"
    address = entrance_address(record.origin)
    async with open_http(address) as http:
        yield LandingClient(http, address, stand.manifest.hive.id, SystemClock())


async def redeem_invite(stand: Stand, code: str, name: str = "laptop") -> DeviceKey:
    """Redeem an invite on the stand's loopback listener with a fresh key, as a program does.

    Args:
        stand: The serving Hive Stand.
        code: The invite code ``hive entrance invite`` printed.
        name: What the device calls itself.

    Returns:
        The device's id and key; the device is PENDING.
    """
    signer = Ed25519Signer.generate()
    description = DeviceDescription(name=name, platform="Linux", user_agent="garden-bot/1.0")
    async with landing_client(stand) as client:
        redemption = await client.redeem(code, signer, description)
    return DeviceKey(redemption.device_id, signer)


@asynccontextmanager
async def serving_stand(
    manifest_path: Path, responder: Responder | None = None
) -> AsyncIterator[tuple[Stand, HiveEntrance]]:
    """Run ``hive serve``'s own composition until the block exits.

    Args:
        manifest_path: The Hive Stand's manifest (its operator password already set).
        responder: Scripts every model call; a silent one when omitted.

    Yields:
        The stand and its running Entrance (its serve record published).
    """
    manifest = load_manifest(manifest_path, {})
    # build_served_hive runs its own event loops (the stores), so never inside the test's.
    served = await asyncio.to_thread(
        build_served_hive,
        manifest,
        environ={},
        clock=SystemClock(),
        responders={"fake": responder or _silent},
    )
    async with serve_hive(served) as entrance:
        yield Stand(manifest_path, manifest), entrance
