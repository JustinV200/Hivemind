"""Supervise the tunnel client the Entrance runs in tunnel mode: start it, restart it, stop it.

In ``tunnel`` mode (ADR-0033) the remote listener binds loopback and a TCP-forwarding tunnel client
(cloudflared, frp, bore, an SSH reverse tunnel) carries connections to it from a relay, so TLS,
mutual TLS included, stays end to end: the client forwards bytes, never HTTP. The Entrance runs
that client as its child. ``TunnelSupervisor`` starts ``[entrance] tunnel_command`` as an argument
list (never through a shell), restarts it with capped exponential backoff whenever it exits, and
ends it on ``stop()``: SIGTERM, a grace period, then SIGKILL, sent to its whole process group on
POSIX so a wrapper script cannot leave its client behind. ``tunnel_environment`` builds the
child's environment: the Hive's own minus every ``HIVEMIND_*`` variable and every provider key
variable named otherwise (a third-party client has no business with the Hive's provider keys or
VAPID key), plus each
``HIVEMIND_ENTRANCE_TUNNEL_<NAME>`` variable handed over as ``<NAME>``, the name the client itself
reads; no shell expands variables in an argv, so that is the only way its token can reach it. This
is the one Entrance module codingrules 4 lets start a process.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Built by the
    Entrance's composition root from an ``ExposurePlan``'s ``tunnel_argv`` and the tunnel
    variables ``hivemind.manifest.env`` read; stopped by the Entrance Reducer and at shutdown.
    Calls into ``asyncio``'s subprocess support, the injected ``Clock`` and, on Windows,
    ``hivemind.entrance.expose.process_tree`` (every process the child started ends with it).

Key invariants:
    - The child never inherits a ``HIVEMIND_*`` variable or a withheld provider key variable,
      and nothing here logs its environment or its arguments: a log line names the program, a
      pid, an exit code, a delay.
    - The child's stdin, stdout and stderr are the null device: the Hive relays nothing a tunnel
      client prints (it may echo its relay URL or credentials), and a full pipe can never stall it.
    - After ``stop()`` returns no child is running and none will be started, and it returns in
      about a second (the Entrance Reducer's bound); a cancelled ``run()`` ends its child before
      the cancellation propagates.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, ``tunnel``.
    - hivemind.manifest.env for ``HIVEMIND_ENTRANCE_TUNNEL_*``.
    - hivemind.entrance.expose.plan for the checks ``tunnel_command`` passes first.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import Coroutine, Iterable, Mapping, Sequence

from pydantic import SecretStr

from hivemind.common.logging import get_logger
from hivemind.entrance.expose.errors import ExposureRefusedError
from hivemind.entrance.expose.process_tree import kill_process_tree
from hivemind.entrance.expose.rules import ExposureRule
from hivemind.manifest.schema import EntranceExposure
from waggle.clock import Clock

INITIAL_RESTART_DELAY_S = 1.0  # First pause after an exit: no busy loop, yet the door is back fast.
RESTART_BACKOFF_FACTOR = 2.0  # Each consecutive quick exit doubles the pause.
MAX_RESTART_DELAY_S = 60.0  # The pause never grows past a minute, so a mended network is used soon.
STABLE_RUN_S = 60.0  # A child that ran this long was healthy: its exit starts the backoff over.
# After SIGTERM, time for the client to close its relay session; then SIGKILL, and at most this
# long again for the kernel to reap it. Together they bound stop() to one second, the Entrance
# Reducer's bound for cutting the door (ADR-0033); a forwarder with nothing in flight exits in
# milliseconds, and one still draining requests is cut, which is what reducing means.
STOP_GRACE_S = 0.5
KILL_WAIT_S = 0.5
_HIVE_VARIABLE_PREFIX = "HIVEMIND_"  # The Hive's own settings and secrets; never passed on.

log = get_logger(__name__)

__all__ = [
    "INITIAL_RESTART_DELAY_S",
    "KILL_WAIT_S",
    "MAX_RESTART_DELAY_S",
    "RESTART_BACKOFF_FACTOR",
    "STABLE_RUN_S",
    "STOP_GRACE_S",
    "TunnelSupervisor",
    "tunnel_environment",
]


def tunnel_environment(
    hive_environ: Mapping[str, str],
    tunnel_variables: Mapping[str, SecretStr],
    withheld: Iterable[str] = (),
) -> dict[str, str]:
    """Build the tunnel child's environment: the Hive's, minus its own variables, plus the tunnel's.

    Args:
        hive_environ: The Hive process's environment (``os.environ`` at the composition root);
            PATH, HOME, SYSTEMROOT and the like are what let the client run at all.
        tunnel_variables: ``EnvOverrides.entrance_tunnel_environ``: each variable the child gets,
            keyed by the name it reads (``HIVEMIND_ENTRANCE_TUNNEL_`` already removed).
        withheld: More names the child must not inherit though they lack the ``HIVEMIND_``
            prefix: every provider's ``api_key_env`` (``ANTHROPIC_API_KEY``, say).

    Returns:
        A fresh mapping for the child. It holds secrets: hand it to ``TunnelSupervisor`` and
        nowhere else, and never log it.
    """
    # Upper-cased for the test because Windows variable names are case-insensitive.
    hidden = {name.upper() for name in withheld}
    environment = {
        name: value
        for name, value in hive_environ.items()
        if not name.upper().startswith(_HIVE_VARIABLE_PREFIX) and name.upper() not in hidden
    }
    # The tunnel's own variables go in last, so they win over an inherited one of the same name.
    environment.update(
        {name: secret.get_secret_value() for name, secret in tunnel_variables.items()}
    )
    return environment


class TunnelSupervisor:
    """Keep the tunnel client running as the Entrance's child until told to stop."""

    def __init__(self, argv: Sequence[str], env: Mapping[str, str], clock: Clock) -> None:
        """Build the supervisor; nothing starts until ``run``.

        Args:
            argv: The client's program and arguments (``ExposurePlan.tunnel_argv``).
            env: The child's whole environment, from ``tunnel_environment``.
            clock: Paces the restart backoff and the stop grace period.

        Raises:
            ExposureRefusedError: ``argv`` names no program.
        """
        if not argv or not argv[0]:
            detail = "The tunnel supervisor was given no program to run."
            raise ExposureRefusedError(
                EntranceExposure.TUNNEL, ExposureRule.TUNNEL_COMMAND_MISSING, detail
            )
        self._argv = tuple(argv)
        self._env = dict(env)
        self._clock = clock
        # Set once by stop(): no restart after it, and run() returns.
        self._stop = asyncio.Event()
        # Set while a child is running; wait_running() waits on it.
        self._running = asyncio.Event()
        # Clear from just before a spawn until that child has ended; stop() waits on it.
        self._idle = asyncio.Event()
        self._idle.set()
        self._process: asyncio.subprocess.Process | None = None
        self._restarts = 0

    @property
    def restarts(self) -> int:
        """How many times the child has been started again after exiting."""
        return self._restarts

    @property
    def pid(self) -> int | None:
        """The running child's process id, or None when none is running."""
        return self._process.pid if self._process is not None else None

    async def run(self) -> None:
        """Run the client until ``stop()``, starting it again with backoff whenever it exits.

        Returns:
            None, once ``stop()`` was called and the child has ended. A failure to start the
            program (missing, not executable) is logged and retried like an exit.
        """
        delay = INITIAL_RESTART_DELAY_S
        while not self._stop.is_set():
            ran_for = await self._run_child()
            if self._stop.is_set():
                break
            # A child that stayed up a while was healthy, so its exit starts the backoff over.
            if ran_for >= STABLE_RUN_S:
                delay = INITIAL_RESTART_DELAY_S
            log.warning("entrance.tunnel_restart_scheduled", program=self._argv[0], delay_s=delay)
            await self._first(self._stop.wait(), delay)
            delay = min(delay * RESTART_BACKOFF_FACTOR, MAX_RESTART_DELAY_S)
            if not self._stop.is_set():
                self._restarts += 1
        log.info("entrance.tunnel_stopped", program=self._argv[0], restarts=self._restarts)

    async def stop(self) -> None:
        """Stop supervising and end the running child: SIGTERM, a grace period, then SIGKILL.

        Idempotent; safe to call before, during or after ``run``. When it returns, no child is
        running and none will be started; it returns within about ``STOP_GRACE_S +
        KILL_WAIT_S``, plus the moment a child it raced takes to spawn.
        """
        self._stop.set()
        process = self._process
        if process is not None:
            await self._end(process)
        # A child may be starting at this very moment, before stop() could see it; run() ends it
        # the moment it exists, and this waits for that (as long as ending one may take), so no
        # child outlives stop() unless the kernel itself cannot end it.
        await self._first(self._idle.wait(), STOP_GRACE_S + KILL_WAIT_S)

    async def wait_running(self) -> None:
        """Return once a child is running (at once when one already is)."""
        await self._running.wait()

    def __repr__(self) -> str:
        """Name the program and the restart count only, never the arguments or environment."""
        return f"TunnelSupervisor(program={self._argv[0]!r}, restarts={self._restarts})"

    async def _run_child(self) -> float:
        """Start the child once and wait for it to exit; return how long it ran, in seconds."""
        started = self._clock.monotonic()
        self._idle.clear()
        try:
            process = await self._start()
            if process is not None:
                await self._supervise(process)
        finally:
            # Set however this ends (an exit, a failed start, a cancellation), or stop() hangs.
            self._idle.set()
        return self._clock.monotonic() - started

    async def _start(self) -> asyncio.subprocess.Process | None:
        """Spawn the child, or log why it could not start and return None."""
        try:
            return await self._spawn()
        except OSError as error:
            # Missing or not executable: retried with backoff like an exit, because a package
            # upgrade may bring the program back and the door must come back without a human.
            log.warning("entrance.tunnel_start_failed", program=self._argv[0], errno=error.errno)
            return None

    async def _supervise(self, process: asyncio.subprocess.Process) -> None:
        """Watch a started child until it exits; never leave it running behind a cancellation."""
        self._process = process
        try:
            await self._watch(process)
        finally:
            self._running.clear()
            self._process = None
            # Reached with the child alive only when run() is being cancelled: never orphan it.
            if process.returncode is None:
                await self._end(process)

    async def _watch(self, process: asyncio.subprocess.Process) -> None:
        """Wait for a started child to exit, ending it at once if stop() came while it started."""
        # stop() may have run during _spawn, when there was no process for it to end.
        if self._stop.is_set():
            await self._end(process)
            return
        self._running.set()
        log.info("entrance.tunnel_started", program=self._argv[0], pid=process.pid)
        # Latency: as long as the tunnel lives, normally hours; it ends when the child exits or
        # stop() ends it, so there is deliberately no timeout here.
        returncode = await process.wait()
        if not self._stop.is_set():
            log.warning("entrance.tunnel_exited", program=self._argv[0], returncode=returncode)

    async def _spawn(self) -> asyncio.subprocess.Process:
        """Start the child in its own process group, detached from the terminal's signals."""
        # SAFETY: an argument list (never a shell) from the validated tunnel_command, the
        # operator's own tunnel client; its environment is tunnel_environment's, and its stdio
        # is the null device. Its own process group keeps a Ctrl-C at the Hive's terminal from
        # killing it behind the supervisor's back (it would only be restarted).
        # Latency: one fork and exec, milliseconds; a program that cannot start raises OSError.
        if sys.platform == "win32":
            return await asyncio.create_subprocess_exec(
                *self._argv,
                env=self._env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        return await asyncio.create_subprocess_exec(
            *self._argv,
            env=self._env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )

    async def _end(self, process: asyncio.subprocess.Process) -> None:
        """End the child: SIGTERM, up to STOP_GRACE_S to exit, then SIGKILL."""
        _signal(process, force=False)
        if await self._first(process.wait(), STOP_GRACE_S):
            return
        log.warning("entrance.tunnel_killed", program=self._argv[0], grace_s=STOP_GRACE_S)
        _signal(process, force=True)
        # SIGKILL cannot be caught; the bound only survives a child stuck inside the kernel.
        await self._first(process.wait(), KILL_WAIT_S)

    async def _first(self, work: Coroutine[object, object, object], seconds: float) -> bool:
        """Await ``work`` for at most ``seconds`` of the injected clock; True when it finished."""
        # Both waits are owned by the group and the loser is cancelled, so neither outlives this.
        async with asyncio.TaskGroup() as group:
            job = group.create_task(work)
            timer = group.create_task(self._clock.sleep(seconds))
            await asyncio.wait((job, timer), return_when=asyncio.FIRST_COMPLETED)
            finished = job.done()
            job.cancel()
            timer.cancel()
        return finished


def _signal(process: asyncio.subprocess.Process, *, force: bool) -> None:
    """Send the child SIGTERM (or SIGKILL when ``force``); on POSIX, its whole process group."""
    # A reaped child's pid may already belong to someone else: never signal it.
    if process.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            # Windows has no process-group signals, and TerminateProcess ends only the child: kill
            # every process it started too, so no helper keeps the door open after a reduction.
            kill_process_tree(process.pid)
        else:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        # The child exited between the check and the signal: nothing is left to end.
        return
