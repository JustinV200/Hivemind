"""Demonstrate Waggle across two processes: the phase 1 exit criterion, run end to end.

Waggle is the Hive's bee-to-bee wire protocol. Roadmap phase 1 exits when a server and a client
in separate processes exchange ``Ping``/``Pong`` and a signed ``TaskAssign``, reject a tampered
envelope, and replay an outbox after the server is restarted; this script is that demonstration.
Run with no arguments it is the orchestrator: it generates one Ed25519 keypair per node into a
fresh temporary directory (private keys reach the children as files, never as arguments or
output), picks a free loopback port, starts the server role as a child process, waits for its
``READY`` line, starts the client role, and then reads the children's stdout, one ``EVENT``
line per milestone, asserting the exact sequence the scenario requires with a timeout on every
wait: it kills the server when the client says it is waiting for the outage, restarts it on the
same port once the client has queued its outbox, and stops it by closing its stdin at the end.
Run with ``--role server`` or ``--role client`` it plays that role (scripts/waggle_echo_server.py,
scripts/waggle_echo_client.py); the orchestrator launches this same file in both roles.

Fits into the Hive:
    Layer: none (a demo, not shipped code). Run by a developer and by
    scripts/tests/test_waggle_echo.py; launches itself through scripts/waggle_echo_child.py and
    calls into scripts/waggle_echo_keys.py for the key files. Depends on the waggle package
    being installed in the interpreter that runs it, which the children inherit.

Key invariants:
    - Exits 0 only when every milestone happened in order and both children exited 0; exits 1
      with the offending event on stderr otherwise, and never leaves a child running.
    - No wait is unbounded: every read of a child's output and every wait for its exit has a
      named timeout.

See Also:
    - .claude/roadmap.md, phase 1 exit criteria, for what this demonstrates.
    - scripts/waggle_echo_server.py, scripts/waggle_echo_client.py, scripts/waggle_echo_keys.py
      and scripts/waggle_echo_child.py for the roles, the key layout and the process plumbing.
"""

from __future__ import annotations

import argparse
import socket
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from waggle_echo_child import EXIT_TIMEOUT_S, Child, DemoError
from waggle_echo_client import QUEUED_PINGS, run_client
from waggle_echo_keys import CLIENT_ROLE, SERVER_ROLE, write_keys
from waggle_echo_server import READY_LINE, run_server

from waggle import InvalidSignatureError, SystemClock
from waggle.transport import CLOSE_POLICY_VIOLATION, DEFAULT_HOST

SCRIPT_PATH = Path(__file__).resolve()  # What each child runs: this file, in one role.
START_TIMEOUT_S = 30.0  # A cold interpreter importing waggle and pydantic; generous for CI.
EVENT_TIMEOUT_S = 20.0  # Any one milestone on loopback takes milliseconds; this is a bound.
RECONNECT_TIMEOUT_S = 90.0  # The client's capped backoff can wait 61.5 s across its dials.
OUTBOX_FILE = "client.outbox"  # The client's outbox log, inside the demo's temporary directory.

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Orchestrate the demo, or play one role of it when ``--role`` is given.

    Args:
        argv: Command-line arguments, excluding the program name. ``None`` means use
            ``sys.argv[1:]`` (the normal case when run as a script).

    Returns:
        0 when the scenario completed (or the role finished cleanly), 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run the Waggle phase 1 demo: a server and a client in separate processes exchange "
            "Ping/Pong and a signed TaskAssign, refuse a tampered envelope, and replay an outbox "
            "after the server restarts. With --role, play one side (the orchestrator does this)."
        )
    )
    parser.add_argument("--role", choices=[SERVER_ROLE, CLIENT_ROLE], help="Play one role only.")
    parser.add_argument("--port", type=int, help="The loopback port (with --role).")
    parser.add_argument("--keys", type=Path, help="The keys directory (with --role).")
    parser.add_argument("--outbox", type=Path, help="The outbox log path (with --role client).")
    args = parser.parse_args(argv)
    if args.role is None:
        return _orchestrate()
    # A role is only ever launched by the orchestrator, which passes everything; a human who
    # tries one by hand gets argparse's usage line rather than a traceback from a missing path.
    if args.port is None or args.keys is None:
        parser.error("--role needs --port and --keys")
    if args.role == SERVER_ROLE:
        return run_server(args.port, args.keys)
    if args.outbox is None:
        parser.error("--role client needs --outbox")
    return run_client(args.port, args.keys, args.outbox)


@dataclass(frozen=True, slots=True)
class Milestone:
    """One row of the summary: which role reached what, and when since the demo began."""

    role: str
    event: str
    at_s: float


@dataclass(slots=True)
class Run:
    """One demo run: where it lives, which port it uses, and the milestones reached so far."""

    port: int
    keys_dir: Path
    children: list[Child] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def expect(self, child: Child, line: str, timeout_s: float = EVENT_TIMEOUT_S) -> None:
        """Require ``line`` from ``child`` within ``timeout_s`` and record it as a milestone."""
        child.expect(line, timeout_s)
        self.note(child.role, line.removeprefix("EVENT ").lower())

    def expect_exit(self, child: Child, code: int) -> None:
        """Require ``child`` to exit with ``code`` and record it as a milestone."""
        actual = child.wait(EXIT_TIMEOUT_S)
        if actual != code:
            raise DemoError(f"{child.role}: exited {actual}, expected {code}")
        self.note(child.role, f"exit {actual}")

    def note(self, role: str, event: str) -> None:
        """Record a milestone with the time since the run began."""
        self.milestones.append(Milestone(role, event, time.monotonic() - self.started))


def _orchestrate() -> int:
    """Set the stage, play the scenario, and print the summary or the failure."""
    # ignore_cleanup_errors: on Windows a file a just-exited child held can lag its removal
    # by a moment, and a leftover temp directory must never turn a passed demo into a failure.
    with tempfile.TemporaryDirectory(prefix="waggle_echo_", ignore_cleanup_errors=True) as tmp:
        keys_dir = Path(tmp)
        write_keys(keys_dir, SystemClock())
        run = Run(port=_free_port(), keys_dir=keys_dir)
        try:
            _play_exchange(run)
            _play_outage(run)
        except DemoError as exc:
            print(f"waggle_echo: FAILED: {exc}", file=sys.stderr)
            return 1
        finally:
            # Whatever happened, no child outlives the orchestrator (an exception above included).
            for child in run.children:
                child.finish()
    _print_summary(run.milestones)
    return 0


def _play_exchange(run: Run) -> None:
    """Acts 1 and 2: start both roles, see the two exchanges succeed and the tampering refused."""
    server = _start(run, SERVER_ROLE)
    run.expect(server, READY_LINE, START_TIMEOUT_S)
    client = _start(run, CLIENT_ROLE)
    run.expect(client, "EVENT pong", START_TIMEOUT_S)
    run.expect(client, "EVENT task_accepted")
    # The server names the fault by its stable code; the client sees the mapped close code.
    run.expect(server, f"EVENT rejected {InvalidSignatureError.code}")
    run.expect(client, f"EVENT tamper_rejected {CLOSE_POLICY_VIOLATION}")


def _play_outage(run: Run) -> None:
    """Acts 3 to 5: kill the server under the client, restart it, and see the outbox replayed."""
    server, client = run.children[0], run.children[1]
    run.expect(client, "EVENT waiting_for_outage")
    server.terminate_abruptly()
    run.note(SERVER_ROLE, "killed")
    run.expect(client, "EVENT link_lost")
    run.expect(client, f"EVENT queued {QUEUED_PINGS}")
    # Same port and the same keys: the client's reconnect and its trust in the server survive.
    server = _start(run, SERVER_ROLE)
    run.expect(server, READY_LINE, START_TIMEOUT_S)
    run.expect(client, "EVENT reconnected", RECONNECT_TIMEOUT_S)
    run.expect(client, f"EVENT replayed {QUEUED_PINGS}")
    run.expect(client, f"EVENT replay_answered {QUEUED_PINGS}")
    run.expect_exit(client, 0)
    # The server counts the replay on its side once the client's clean close ends the connection.
    run.expect(server, f"EVENT replayed {QUEUED_PINGS}")
    server.request_stop()
    run.expect_exit(server, 0)


def _start(run: Run, role: str) -> Child:
    """Launch this script in ``role`` against the run's port and keys, and track the child."""
    argv = [sys.executable, "-u", str(SCRIPT_PATH), "--role", role]
    argv += ["--port", str(run.port), "--keys", str(run.keys_dir)]
    if role == CLIENT_ROLE:
        argv += ["--outbox", str(run.keys_dir / OUTBOX_FILE)]
    child = Child(role, argv)
    run.children.append(child)
    return child


def _free_port() -> int:
    """A loopback port nothing listens on: bound for an instant, then released for the server."""
    # The instant between release and the server's bind is a race in theory; on a developer
    # machine or a CI runner nothing else is grabbing ephemeral ports at that moment.
    with socket.socket() as probe:
        probe.bind((DEFAULT_HOST, 0))
        port: int = probe.getsockname()[1]
    return port


def _print_summary(milestones: Sequence[Milestone]) -> None:
    """Print the milestone table: when each role reached what, in the order it happened."""
    total_s = milestones[-1].at_s if milestones else 0.0
    print(f"waggle_echo: every milestone reached ({len(milestones)} in {total_s:.1f} s)")
    print("  at (s)  role    milestone")
    for milestone in milestones:
        print(f"  {milestone.at_s:6.2f}  {milestone.role:<6}  {milestone.event}")


if __name__ == "__main__":
    raise SystemExit(main())
