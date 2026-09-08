"""Wrap one child process of the waggle_echo demo: start it, read its lines, stop or kill it.

The orchestrator (scripts/waggle_echo.py) drives its two roles by what they print: each role
writes one ``EVENT`` line per milestone to stdout and nothing else there, and the orchestrator
asserts those lines in order with a timeout on every wait. This module is the process plumbing
that makes that portable: a ``Child`` starts one role under the interpreter that runs the
orchestrator, pumps the role's stdout through a daemon thread into a queue (a pipe cannot be
polled portably; ``select`` on Windows is sockets-only), and offers the three ways a role's
life ends in the scenario: a stop request (closing its stdin, which both roles and both
platforms understand), an abrupt kill (``terminate`` then ``kill``, so the peer sees a lost
link rather than a clean close), and ``finish``, the unconditional cleanup that leaves no
process and no open pipe behind. ``DemoError`` is what every broken expectation raises.

Fits into the Hive:
    Layer: none (a demo helper, not shipped code). Used by scripts/waggle_echo.py only; calls
    into subprocess, threading and queue from the standard library.

Key invariants:
    - Every wait has a timeout: a line that never comes or a child that never exits raises
      DemoError rather than blocking.
    - finish() leaves the process exited and every pipe closed, however the child got there.

See Also:
    - scripts/waggle_echo.py for the scenario this plumbing serves.
    - .claude/codingrules.md section 15 for the subprocess rule the SAFETY comment answers.
"""

from __future__ import annotations

import queue
import subprocess
import threading
from collections.abc import Sequence

KILL_GRACE_S = 2.0  # After terminate(), before kill(): the outage must be abrupt, not slow.
EXIT_TIMEOUT_S = 15.0  # A child that was asked to stop, or killed, is gone well within this.

__all__ = ["EXIT_TIMEOUT_S", "KILL_GRACE_S", "Child", "DemoError"]


class DemoError(Exception):
    """A milestone did not happen as the scenario requires; the message names it."""


class Child:
    """One role process: its Popen, a reader thread, and the queue of stdout lines it produced."""

    def __init__(self, role: str, argv: Sequence[str]) -> None:
        """Start ``argv`` and begin pumping its stdout lines into a queue.

        Args:
            role: The role's name, for the summary and for failure messages.
            argv: The full command, interpreter first; nothing in it came from outside the demo.
        """
        self.role = role
        # SAFETY: argv is [sys.executable, "-u", the demo script, --role ...] built by the
        # orchestrator from values it minted itself (a port number, a temp directory); no shell,
        # no user input. stdin is a pipe because closing it is the stop request; stderr is
        # inherited so a child's traceback lands on the operator's terminal unfiltered.
        self._proc: subprocess.Popen[bytes] = subprocess.Popen(  # noqa: S603
            list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        # A daemon thread so a child that never closes its stdout cannot keep the orchestrator
        # alive; the queue's timed get is what bounds every wait on it.
        self._reader = threading.Thread(target=self._pump, name=f"{role}-stdout", daemon=True)
        self._reader.start()

    def expect(self, line: str, timeout_s: float) -> None:
        """Require the child's next stdout line to be exactly ``line`` within ``timeout_s``.

        Args:
            line: The exact line, without its newline.
            timeout_s: Real seconds to wait for it.

        Raises:
            DemoError: The line differs, the child's stdout ended, or nothing arrived in time.
        """
        try:
            got = self._lines.get(timeout=timeout_s)
        except queue.Empty:
            raise DemoError(
                f"{self.role}: expected {line!r} but nothing arrived within {timeout_s} s"
            ) from None
        if got is None:
            raise DemoError(f"{self.role}: expected {line!r} but its output ended")
        if got != line:
            raise DemoError(f"{self.role}: expected {line!r}, got {got!r}")

    def request_stop(self) -> None:
        """Ask the child to stop by closing its stdin, the cross-platform stop request."""
        if self._proc.stdin is not None:
            self._proc.stdin.close()

    def terminate_abruptly(self) -> None:
        """Kill the child without a close handshake, so its peer sees a lost link, not a close."""
        # terminate() is TerminateProcess on Windows and SIGTERM (uncaught, so fatal) on POSIX;
        # both drop the socket without a WebSocket close frame. kill() is the backstop.
        self._proc.terminate()
        try:
            self._proc.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=KILL_GRACE_S)

    def wait(self, timeout_s: float) -> int:
        """Wait for the child to exit and return its exit code.

        Args:
            timeout_s: Real seconds to wait for the exit.

        Returns:
            The child's exit code.

        Raises:
            DemoError: The child is still running after ``timeout_s``.
        """
        try:
            return self._proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise DemoError(
                f"{self.role}: still running {timeout_s} s after it should have exited"
            ) from None

    def finish(self) -> None:
        """Make sure the child is gone and release its pipes; safe to call more than once."""
        # Killing an exited process is a no-op and waiting on it again just returns the code;
        # the reader thread ends on the pipe's EOF, which the exit guarantees.
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait(timeout=EXIT_TIMEOUT_S)
        self._reader.join(timeout=EXIT_TIMEOUT_S)
        for stream in (self._proc.stdin, self._proc.stdout):
            if stream is not None:
                stream.close()

    def _pump(self) -> None:
        """Move every stdout line into the queue, then a None to mark the end of the stream."""
        stream = self._proc.stdout
        if stream is None:
            raise RuntimeError("Child was started without a stdout pipe.")
        # Binary readline returns as soon as a newline arrives, so a milestone is never held
        # back by read-ahead buffering; the decode is lenient because a traceback is not ours.
        for raw in iter(stream.readline, b""):
            self._lines.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        self._lines.put(None)
