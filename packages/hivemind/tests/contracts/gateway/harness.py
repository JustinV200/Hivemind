"""Start a gateway under test as a subprocess and bring it online against a live Hive Stand.

The gateway conformance suite (roadmap step 11.7a) plays the Hive Stand against a gateway it did
not write, so it cannot import it: it runs it. The command that starts the gateway is a template
in the environment variable `HIVEMIND_GATEWAY_COMMAND` (or `DEFAULT_COMMAND`, the Pollen Packet
of this workspace), and this module turns that template into running processes. A template is a
command line, split into words the way a POSIX shell would split it and run without a shell. Three
placeholders are filled into its words: `{python}`, the interpreter running the suite; `{invite}`,
the `pollen1.` invite string the gateway enrols with; and `{data_dir}`, an empty directory the
gateway keeps its state in. A word that is exactly `&&` ends a step: every step but the last runs
to completion first (a gateway that enrols with one command and runs with another puts the enrol
first), and the last is the gateway, which runs until the suite stops it. The module docstring of
`contracts.test_gateway_contract` carries the same rules for people writing a template.

`start_gateway` is the one door: it mints an invite for a fresh label, runs the template, waits
for the Hive's registry to hold the node the gateway enrolled as, waits for the gateway's link and
its first frames, and returns a `Gateway` that knows its ids and its process. `stop` ends it
(SIGTERM, then SIGKILL) and kills any program it started that outlived it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the gateway conformance
    suite; builds on `builders.swarm.live` for the Hive Stand and `contracts.gateway.driver` for
    the requests sent to the gateway.

Key invariants:
    - A step that fails, or a gateway that exits before it is online, raises `GatewayStartError`
      with the gateway's own output, never a bare timeout.
    - Every subprocess call has a timeout, and `stop` leaves no process of the gateway's behind.
    - The gateway runs on this machine: the suite looks at this machine's disk and process table
      where a clause is about the device's.

See Also:
    - docs/waggle/gateway-contract.md section 2 for how the suite is run.
    - builders.swarm.live for the Hive Stand the gateway dials.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import sys
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import psutil
from builders.swarm.live import LiveHive
from contracts.gateway.driver import Driver

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm.enrolment import MintedInvite, parse_invite_string
from hivemind.swarm.models import SwarmNode
from waggle.messages.forage.hosting import CapacityReport
from waggle.messages.swarm import DeviceHeartbeat

COMMAND_VARIABLE = "HIVEMIND_GATEWAY_COMMAND"  # Where a person points the suite at their gateway.
# The Pollen Packet of this workspace: enrol with the invite, then run until stopped.
DEFAULT_COMMAND = (
    "{python} -m pollen enrol --data-dir {data_dir} {invite} && "
    "{python} -m pollen run --data-dir {data_dir}"
)
STEP_SEPARATOR = "&&"  # A word of its own in a template: what comes before it runs to completion.
PLACEHOLDER = re.compile(r"\{(\w+)\}")
STEP_TIMEOUT_S = 120.0  # A setup step (an enrolment) over loopback takes about a second.
ONLINE_TIMEOUT_S = 60.0  # From a started gateway to its first frames; a cold import is the cost.
STOP_TIMEOUT_S = 20.0  # A gateway stopped by SIGTERM releases its leases, then exits.
POLL_S = 0.05  # How often a wait looks at the registry, the hub or the process.

__all__ = [
    "COMMAND_VARIABLE",
    "DEFAULT_COMMAND",
    "Gateway",
    "GatewayProcess",
    "GatewayStartError",
    "command_template",
    "parse_command",
    "running_gateway",
    "start_gateway",
]


class GatewayStartError(Exception):
    """The gateway under test did not come online; the message carries its own output."""


def command_template() -> str:
    """Return the command template under test: the environment's, else the default."""
    return os.environ.get(COMMAND_VARIABLE) or DEFAULT_COMMAND


def parse_command(template: str, values: Mapping[str, str]) -> tuple[tuple[str, ...], ...]:
    """Split a command template into its steps and fill the placeholders of every word.

    Args:
        template: The command line, steps separated by a word `&&`.
        values: What each placeholder (`python`, `invite`, `data_dir`) stands for.

    Returns:
        The steps, each an argument list for a program; the last is the gateway.

    Raises:
        ValueError: A step is empty, or a word names a placeholder the suite does not fill.
    """
    steps: list[list[str]] = [[]]
    for word in shlex.split(template):
        if word == STEP_SEPARATOR:
            steps.append([])
        else:
            steps[-1].append(PLACEHOLDER.sub(lambda match: _value(match, values), word))
    if not all(steps):
        raise ValueError(f"{COMMAND_VARIABLE} has an empty step: {template!r}")
    return tuple(tuple(step) for step in steps)


def _value(match: re.Match[str], values: Mapping[str, str]) -> str:
    """Return what one placeholder stands for, or raise for a name the suite does not fill."""
    name = match.group(1)
    if name not in values:
        raise ValueError(f"{COMMAND_VARIABLE} names the placeholder {{{name}}}, which is unknown.")
    return values[name]


class GatewayProcess:
    """The gateway under test as an operating-system process, and its output."""

    def __init__(self, steps: tuple[tuple[str, ...], ...], log_path: Path) -> None:
        """Prepare the process; nothing runs until `start`.

        Args:
            steps: The parsed template; all but the last are run to completion first.
            log_path: Where every step's standard output and error are written.
        """
        self._steps = steps
        self._log_path = log_path
        self._log: IO[bytes] | None = None
        self.process: asyncio.subprocess.Process | None = None

    @property
    def pid(self) -> int:
        """The gateway's process id; valid once started."""
        assert self.process is not None, "the gateway has not started"
        return self.process.pid

    def output(self) -> str:
        """Everything the gateway and its setup steps have written so far."""
        return self._log_path.read_text(encoding="utf-8", errors="replace")

    async def start(self) -> None:
        """Run the setup steps to completion, then start the gateway itself.

        Raises:
            GatewayStartError: A setup step failed or did not finish in time.
        """
        # Opened once for append: every step writes its output after the last one's.
        self._log = _open_log(self._log_path)
        *setup, run = self._steps
        for step in setup:
            await self._run_to_end(step)
        self.process = await self._spawn(run)

    async def stop(self) -> None:
        """End the gateway, SIGTERM first and SIGKILL after `STOP_TIMEOUT_S`, and its children."""
        process = self.process
        try:
            if process is not None and process.returncode is None:
                children = _descendants(process.pid)
                process.terminate()
                try:
                    async with asyncio.timeout(STOP_TIMEOUT_S):  # The gateway's shutdown sweep.
                        await process.wait()
                except TimeoutError:
                    process.kill()
                    await process.wait()
                _kill_all(children)
        finally:
            if self._log is not None:
                self._log.close()
                self._log = None

    async def _spawn(self, argv: tuple[str, ...]) -> asyncio.subprocess.Process:
        """Start one command with its output going to the log."""
        # SAFETY: argv is the suite operator's own command template with the invite, a directory
        # and the interpreter filled in; it runs as an argument list, never through a shell.
        return await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=self._log, stderr=self._log
        )

    async def _run_to_end(self, argv: tuple[str, ...]) -> None:
        """Run a setup step and require it to exit 0 within `STEP_TIMEOUT_S`."""
        process = await self._spawn(argv)
        try:
            async with asyncio.timeout(STEP_TIMEOUT_S):  # A local process; bounds a hang.
                code = await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise GatewayStartError(self._failure("did not finish", argv)) from None
        if code != 0:
            raise GatewayStartError(self._failure(f"exited {code}", argv))

    def _failure(self, what: str, argv: tuple[str, ...]) -> str:
        """Describe a failed step by its program (never its arguments: one is the invite)."""
        return f"The setup step {Path(argv[0]).name} {what}. Its output:\n{self.output()}"


def _open_log(path: Path) -> IO[bytes]:
    """Create the log's directory if need be and open the log for appending."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("ab")


def _descendants(pid: int) -> list[psutil.Process]:
    """Return every process the given one has started, however deep, while it is alive."""
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def _kill_all(processes: list[psutil.Process]) -> None:
    """Kill whatever of `processes` is still running; a gateway's own exit may have done it."""
    for process in processes:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.kill()


@dataclass
class Gateway:
    """A gateway under test that is enrolled, connected and has sent its first frames."""

    hive: LiveHive
    process: GatewayProcess
    node: SwarmNode
    minted: MintedInvite
    driver: Driver

    @property
    def invite(self) -> str:
        """The `pollen1.` invite string the gateway enrolled with."""
        return self.minted.invite_string

    @property
    def token(self) -> str:
        """The single-use token inside the invite string: the secret the gateway must not leak."""
        return parse_invite_string(self.minted.invite_string).token

    async def stop(self) -> None:
        """Stop the gateway process."""
        await self.process.stop()


async def start_gateway(
    hive: LiveHive,
    work_dir: Path,
    access: AccessLevel = AccessLevel.FULL,
    invite: MintedInvite | None = None,
) -> Gateway:
    """Enrol the gateway under test on `hive` and wait until it is online.

    Args:
        hive: The running Hive Stand to enrol with.
        work_dir: An empty directory for the gateway's state and its output.
        access: The cap on the invite; the gateway asks for FULL and receives the lower.
        invite: An invite already minted (a spent one, to see the gateway refused); a fresh one
            for `access` when omitted.

    Returns:
        The online gateway; the caller awaits `stop()` (`running_gateway` does).

    Raises:
        GatewayStartError: It did not enrol, exited, or did not come online in time.
    """
    fresh_label = f"conformance-{uuid.uuid4().hex[:12]}"
    minted = invite or await hive.mint(access, CombShieldLevel.MEADOW, fresh_label)
    label = minted.label
    data_dir = work_dir / "gateway-data"
    values = {"python": sys.executable, "invite": minted.invite_string, "data_dir": str(data_dir)}
    steps = parse_command(command_template(), values)
    process = GatewayProcess(steps, work_dir / "gateway.log")
    try:
        await process.start()
        node = await _enrolled_node(hive, label, process)
        driver = Driver(hive, node)
        await _online(driver, process)
    except BaseException:
        await process.stop()
        raise
    return Gateway(hive, process, node, minted, driver)


@asynccontextmanager
async def running_gateway(
    hive: LiveHive, work_dir: Path, access: AccessLevel = AccessLevel.FULL
) -> AsyncIterator[Gateway]:
    """Start a gateway for the duration of a block and stop it after, whatever the block did.

    Args:
        hive: The Hive Stand to enrol with.
        work_dir: An empty directory for the gateway's state and its output.
        access: The cap on the invite.

    Yields:
        The online gateway.
    """
    gateway = await start_gateway(hive, work_dir, access)
    try:
        yield gateway
    finally:
        await gateway.stop()


async def _enrolled_node(hive: LiveHive, label: str | None, process: GatewayProcess) -> SwarmNode:
    """Wait for the registry to hold the node enrolled under `label`, or the gateway to exit."""
    async with asyncio.timeout(ONLINE_TIMEOUT_S):  # In-process poll of the registry.
        while True:
            for node in await hive.registry.nodes():
                if node.label == label:
                    return node
            _require_running(process)
            await asyncio.sleep(POLL_S)


async def _online(driver: Driver, process: GatewayProcess) -> None:
    """Wait for the node's link and for its heartbeat and capacity report to have arrived."""
    hive, device_id = driver.hive, driver.node.device_id
    async with asyncio.timeout(ONLINE_TIMEOUT_S):  # The gateway dials as soon as it runs.
        while True:
            seen = driver.frames_of(DeviceHeartbeat) and driver.frames_of(CapacityReport)
            if hive.hub.link(device_id) is not None and seen:
                return
            _require_running(process)
            await asyncio.sleep(POLL_S)


def _require_running(process: GatewayProcess) -> None:
    """Raise `GatewayStartError` with the gateway's output if it has already exited."""
    running = process.process
    if running is not None and running.returncode is not None:
        raise GatewayStartError(
            f"The gateway exited {running.returncode} before it was online. Its output:\n"
            f"{process.output()}"
        )
