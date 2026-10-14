"""Command line of the Pollen Packet: enrol, run, install, uninstall, status and version.

``pollen`` is the program an operator installs on a device the Hive will borrow. ``pollen enrol
INVITE`` turns a ``pollen1.`` invite string into an enrolment (use ``-`` to read the string from
standard input so it never appears in a process list or shell history); ``pollen run`` is the
packet itself, the loop the service manager keeps alive; ``pollen install`` sets it up to start at
login and records, for the colonization step, the operator's chosen source for the Hive
runtime (``--runtime-wheelhouse`` or ``--runtime-index``, never both), the interpreter to build it
with (``--runtime-python``) and the device's local Warden manifest; ``pollen uninstall`` removes
the service unit that install wrote and stops the Warden the packet started, if one runs; ``pollen
status`` says what the packet is doing and on which rung of the runtime ladder the device is;
``pollen version`` prints the versions. The data directory is ``--data-dir``, else
``POLLEN_DATA_DIR``, else the platform's default, and this module is the only place that reads that
variable. It reads no ``HIVEMIND_*`` variable. This is the one module in pollen allowed to print:
the output is for the operator at a terminal and carries ids, counts and sentences, never a key, a
token or the invite.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. The entry point named in the
    package's ``[project.scripts]`` and in ``python -m pollen``; calls into pollen.build, the
    enrolment ceremony, the platform shims and the state files.

Key invariants:
    - Exit status 0 means done; 1 means failed; 78 (``EXIT_NO_RESTART``) means the packet stopped
      for a reason a restart cannot fix, so the service manager must not restart it.
    - Nothing here prints or logs the invite string, the token, the key or an argument value.
    - ``uninstall`` removes the service unit and ends the packet's own Warden; nothing else of
      the operator's is touched, and the data directory stays.

See Also:
    - docs/waggle/enrolment.md for the ceremony ``enrol`` runs.
    - docs/waggle/gateway-contract.md section 3 for what the platform shims provide.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import signal
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import FrameType

import psutil

from pollen.agent import PollenPacket, StatusDocument, read_status
from pollen.bootstrap import stop_recorded
from pollen.build import build_packet
from pollen.enrol import EnrolPlan, enrol_device, parse_invite
from pollen.errors import NotEnrolledError, PollenError, ProtocolMismatchError
from pollen.executors.info import runtime_version
from pollen.platform import (
    EXIT_NO_RESTART,
    PlatformShim,
    ProcessRef,
    ServicePlan,
    launch_command,
    select_shim,
)
from pollen.state import DataLayout, InstallSettings, PacketState, load_state, save_state
from waggle.clock import SystemClock
from waggle.envelope import PROTOCOL_VERSION
from waggle.messages.swarm import RuntimeLevel

SHUTDOWN_WAIT_S = 20.0  # How long a stop waits for the packet to finish before cancelling it.
STATUS_FRESH_S = 120.0  # A status document older than this was left by a packet no longer running.
DATA_DIR_VARIABLE = "POLLEN_DATA_DIR"  # The one environment variable pollen reads.
INDEX_PATTERN = re.compile(r"https?://\S+")  # A package index URL: http(s), no whitespace.

type SignalHandler = (
    signal.Handlers | Callable[[int, FrameType | None], object] | int | None
)  # Whatever ``signal.getsignal`` returns, so it can be put back.

__all__ = ["data_dir_for", "main", "status_lines"]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: The arguments after the program name; ``sys.argv[1:]`` when None.

    Returns:
        The process exit status: 0 done, 1 failed, 78 stopped for a reason a restart cannot fix.
    """
    args = _parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except ProtocolMismatchError as error:
        print(f"pollen: {error}", file=sys.stderr)
        return EXIT_NO_RESTART
    except NotEnrolledError as error:
        print(f"pollen: {error}", file=sys.stderr)
        return EXIT_NO_RESTART if args.command == "run" else 1
    except PollenError as error:
        print(f"pollen: {error}", file=sys.stderr)
        return 1


def data_dir_for(option: str | None, shim: PlatformShim, env: Mapping[str, str]) -> Path:
    """Choose the data directory: the option, else ``POLLEN_DATA_DIR``, else the platform default.

    Args:
        option: The ``--data-dir`` argument, if given.
        shim: The platform shim, which knows the conventional directory.
        env: The environment to read ``POLLEN_DATA_DIR`` from.

    Returns:
        An absolute path.
    """
    chosen = option or env.get(DATA_DIR_VARIABLE)
    return (Path(chosen).expanduser() if chosen else shim.default_data_dir()).resolve()


def status_lines(
    state: PacketState,
    status: StatusDocument | None,
    now: datetime,
    *,
    warden_runs: bool = False,
) -> list[str]:
    """Render what ``pollen status`` prints: identity, grants and what the packet reports.

    Args:
        state: The persisted enrolment.
        status: The packet's status document, or None if it never wrote one.
        now: The current time.
        warden_runs: Whether the Warden the state records is alive (the caller asks the
            platform, which is not this function's business).

    Returns:
        The lines to print; ids, counts and sentences only.
    """
    lines = [
        f"device      {state.device_id}",
        f"cell        {state.cell_id}",
        f"access      {state.access_level.value} (tier {state.comb_shield.value})",
        f"queen       {state.queen_url}",
        _level_line(state, warden_runs=warden_runs),
    ]
    if status is None or (now - status.updated_at).total_seconds() > STATUS_FRESH_S:
        return [*lines, "state       not running (no recent status)"]
    if not psutil.pid_exists(status.pid):
        return [*lines, "state       not running (the process has gone)"]
    link = "connected" if status.is_connected else "not connected"
    lines.append(f"state       running, {link}")
    if status.last_failure is not None and not status.is_connected:
        lines.append(f"last error  {status.last_failure}")
    lines.append(f"leases      {status.open_leases} open, {status.outbox_pending} frames queued")
    lines.append(f"silence     {int(status.silence_s)} s since the Queen was last heard")
    if status.is_clock_suspect:
        lines.append(
            "warning     the Queen's frames look stale or from the future: check the clock"
        )
    return lines


def _level_line(state: PacketState, *, warden_runs: bool) -> str:
    """Say which rung of the runtime ladder the device is on, and its Warden's health, if any."""
    level = state.level.value.lower()
    if state.warden is None:
        return f"level       {level} (no Warden on this device)"
    health = "running" if warden_runs else "not running"
    return (
        f"level       {level}, Warden {health} (pid {state.warden.pid}, "
        f"hivemind {state.warden.runtime_version})"
    )


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser: one sub-command per verb."""
    parser = argparse.ArgumentParser(prog="pollen", description="The HiveMind device gateway.")
    verbs = parser.add_subparsers(dest="command", required=True)
    verbs.add_parser("version", help=_VERBS["version"])
    for name in ("enrol", "run", "install", "uninstall", "status"):
        verb = verbs.add_parser(name, help=_VERBS[name], description=_VERBS[name])
        verb.add_argument("--data-dir", help="Where the packet keeps its files.")
        extra = _EXTRA_ARGUMENTS.get(name)
        if extra is not None:
            extra(verb)
    return parser


def _enrol_arguments(verb: argparse.ArgumentParser) -> None:
    """Add the invite and ``--force`` to ``pollen enrol``."""
    verb.add_argument("invite", help="The pollen1 invite string, or - to read it from stdin.")
    verb.add_argument("--force", action="store_true", help="Enrol again over an enrolment.")


def _install_arguments(verb: argparse.ArgumentParser) -> None:
    """Add the runtime source and the Warden manifest to ``pollen install``."""
    source = verb.add_mutually_exclusive_group()
    source.add_argument("--runtime-wheelhouse", type=_existing_dir, help="A directory of wheels.")
    source.add_argument("--runtime-index", type=_index_url, help="A package index URL.")
    verb.add_argument("--warden-manifest", type=_existing_file, help="The device's local manifest.")
    verb.add_argument(
        "--runtime-python",
        type=_existing_file,
        help="A Python 3.12 or newer to build the runtime with (else one is searched for).",
    )


_VERBS = {
    "enrol": "Enrol this device in the Swarm with an invite.",
    "run": "Run the packet in the foreground.",
    "install": "Set the packet up to start at login.",
    "uninstall": "Remove the service unit and stop the Warden it ran; the data is kept.",
    "status": "Show what the packet is doing.",
    "version": "Print the versions.",
}
_EXTRA_ARGUMENTS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "enrol": _enrol_arguments,
    "install": _install_arguments,
}


def _existing_dir(text: str) -> str:
    """Argparse type: an existing directory, made absolute."""
    path = Path(text).expanduser().resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError("not an existing directory")
    return str(path)


def _existing_file(text: str) -> str:
    """Argparse type: an existing file, made absolute."""
    path = Path(text).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError("not an existing file")
    return str(path)


def _index_url(text: str) -> str:
    """Argparse type: an http(s) URL."""
    if INDEX_PATTERN.fullmatch(text) is None:
        raise argparse.ArgumentTypeError("not an http(s) URL")
    return text


def _context(args: argparse.Namespace) -> tuple[DataLayout, PlatformShim]:
    """Choose the platform shim and the data directory a command works on."""
    shim = select_shim()
    return DataLayout(data_dir_for(args.data_dir, shim, os.environ)), shim


def _enrol(args: argparse.Namespace) -> int:
    """``pollen enrol``: read the invite and run the ceremony."""
    layout, shim = _context(args)
    clock = SystemClock()
    text = sys.stdin.read() if args.invite == "-" else args.invite
    invite = parse_invite(text, clock.now())
    state = asyncio.run(enrol_device(EnrolPlan(invite, layout, shim, clock, is_forced=args.force)))
    print(
        f"Enrolled as {state.device_id} on Cell {state.cell_id}, access {state.access_level.value}."
    )
    print("Next: pollen install  (or pollen run, to try it in the foreground).")
    return 0


def _run(args: argparse.Namespace) -> int:
    """``pollen run``: the packet, until a signal stops it."""
    layout, shim = _context(args)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    packet = build_packet(layout, shim, SystemClock())
    asyncio.run(_run_until_signalled(packet))
    return 0


async def _run_until_signalled(packet: PollenPacket) -> None:
    """Run the packet until it ends or SIGINT/SIGTERM asks it to stop, then wait for it."""
    stopping = asyncio.Event()
    previous = _watch_signals(asyncio.get_running_loop(), stopping)
    try:
        task = asyncio.create_task(packet.run(), name="packet")
        waiter = asyncio.create_task(stopping.wait(), name="signal")
        done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task not in done:
            packet.stop()
            done, _ = await asyncio.wait({task}, timeout=SHUTDOWN_WAIT_S)
            if task not in done:
                task.cancel()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        with contextlib.suppress(asyncio.CancelledError):
            await task  # Re-raises what ended the packet (a protocol mismatch) for ``main``.
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _watch_signals(
    loop: asyncio.AbstractEventLoop, stopping: asyncio.Event
) -> dict[int, SignalHandler]:
    """Set ``stopping`` on SIGINT or SIGTERM; return the handlers they replaced."""
    previous: dict[int, SignalHandler] = {}
    for name in ("SIGINT", "SIGTERM"):
        number = getattr(signal, name, None)
        if number is not None:
            previous[number] = signal.getsignal(number)
            # A plain handler (not the loop's) so Windows, which has no add_signal_handler, works.
            signal.signal(number, lambda *_: loop.call_soon_threadsafe(stopping.set))
    return previous


def _install(args: argparse.Namespace) -> int:
    """``pollen install``: record the operator's settings and set the service up."""
    layout, shim = _context(args)
    settings = InstallSettings(
        runtime_wheelhouse=args.runtime_wheelhouse,
        runtime_index=args.runtime_index,
        warden_manifest=args.warden_manifest,
        runtime_python=args.runtime_python,
    )
    if settings != InstallSettings():
        state = load_state(layout.state)  # Settings live in the enrolment: enrol first.
        save_state(layout.state, state.model_copy(update={"install": settings}))
    result = shim.install_service(ServicePlan(launch_command(layout.root), layout.root))
    print(result.detail)
    if result.manual_command is not None:
        print(f"  {result.manual_command}")
    if not layout.state.exists():
        print("This device is not enrolled yet: run pollen enrol with an invite first.")
    return 0


def _uninstall(args: argparse.Namespace) -> int:
    """``pollen uninstall``: remove the unit and the Warden it ran; leave the data directory."""
    layout, shim = _context(args)
    result = shim.uninstall_service()
    print(result.detail)
    status = asyncio.run(_withdraw_warden(layout, shim))
    print("The data directory was left in place; delete it yourself to forget the enrolment.")
    return status


async def _withdraw_warden(layout: DataLayout, shim: PlatformShim) -> int:
    """Stop the Warden the state records, if any, so uninstalling leaves nothing running.

    Returns:
        0 when no Warden is left running, 1 when one is and could not be stopped.
    """
    try:
        state = load_state(layout.state)
    except PollenError:
        return 0  # Not enrolled, or an unreadable state: there is no Warden to know of.
    record = state.warden
    if record is None:
        return 0
    # Uninstalling removes the unit that kept the packet, and with it the Warden's watcher, alive:
    # a Warden left running would have nobody to supervise it.
    if not await stop_recorded(record, shim, SystemClock()):
        print(f"pollen: the Warden (pid {record.pid}) could not be stopped.", file=sys.stderr)
        return 1  # Its record stays, so a packet that is started again still knows of it.
    fields = {**state.model_dump(), "level": RuntimeLevel.GATEWAY, "warden": None}
    save_state(layout.state, PacketState.model_validate(fields))
    print("The Warden that ran on this device is no longer running.")
    return 0


def _status(args: argparse.Namespace) -> int:
    """``pollen status``: the enrolment and what the packet reports about itself."""
    layout, shim = _context(args)
    state = load_state(layout.state)
    record = state.warden
    runs = record is not None and shim.group_alive(ProcessRef(record.pid, record.created))
    for line in status_lines(
        state, read_status(layout.status), SystemClock().now(), warden_runs=runs
    ):
        print(line)
    return 0


def _version(_args: argparse.Namespace) -> int:
    """``pollen version``: the packet's version and the Waggle protocol it speaks."""
    print(f"pollen {runtime_version()} (waggle {PROTOCOL_VERSION})")
    return 0


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "enrol": _enrol,
    "run": _run,
    "install": _install,
    "uninstall": _uninstall,
    "status": _status,
    "version": _version,
}
