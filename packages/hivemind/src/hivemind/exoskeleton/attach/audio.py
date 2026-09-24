"""Start a lease's own PulseAudio server: a speaker to listen to and a microphone to speak into.

Audio for a task never touches the operator's own sound setup (ADR-0031): attach starts a private
PulseAudio server with no default configuration (`-n`), its runtime, state and cookie inside the
lease's scratch, and a script that loads exactly four things: the native protocol on a socket in
scratch, a null sink as the speaker (applications play into it; `listen` records its monitor), a
second null sink as the microphone (`say` plays into it; its monitor is the default source), and
those two defaults. Both sinks load with rewinds disabled: a null sink's monitor otherwise holds
audio back by up to its two-second rewind window, and a short recording hears nothing (found on
PulseAudio 16.1 while building phase 6). The server refuses to be told to exit or to load more
modules once started, so nothing a model drives inside the lease can widen it. Readiness is the
server answering `pactl info` on that socket.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.attach`.
    Called by `attach.core`. Calls into `hivemind.cell` (CellSession, BackgroundSpec, ExecSpec,
    run), `hivemind.common.logging`, `hivemind.exoskeleton.buzz.pulseaudio`, `.commands`,
    `.errors`, `.scratch` and `attach.ready` only.

Key invariants:
    - The server is started only after its socket path is known to fit a Unix socket address
      and its script is known to quote it safely; otherwise AttachError, before anything runs.
    - AUDIO_PROGRAMS matches what `hivemind.cell.local.probe` requires before it reports
      `has_audio` (a test holds them together).

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the per-lease server.
    - hivemind.exoskeleton.buzz.pulseaudio for the Buzz that uses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import BackgroundProcess, BackgroundSpec, CellSession, ExecSpec, run
from hivemind.common.logging import get_logger
from hivemind.exoskeleton.attach.ready import Deadline, start_process, stop_all
from hivemind.exoskeleton.buzz.pulseaudio import PulseServer
from hivemind.exoskeleton.commands import sanitised
from hivemind.exoskeleton.errors import AttachError
from hivemind.exoskeleton.scratch import MAX_SOCKET_PATH_BYTES, ScratchLayout

AUDIO_PROGRAMS = ("pulseaudio", "pactl", "parec", "paplay")  # The server and its three clients.
SPEAKER_SINK = "hive_speaker"  # What applications on the Cell play into.
MICROPHONE_SINK = "hive_microphone"  # What `say` plays into; applications hear its monitor.
PROBE_TIMEOUT_S = 5.0  # One `pactl info`; a server that takes longer is not ready yet.
_UNQUOTABLE = frozenset('"\\\n')  # Characters a PulseAudio module argument cannot quote.

log = get_logger(__name__)

__all__ = [
    "AUDIO_PROGRAMS",
    "MICROPHONE_SINK",
    "PROBE_TIMEOUT_S",
    "SPEAKER_SINK",
    "StartedSoundServer",
    "server_script",
    "start_sound_server",
]


@dataclass(frozen=True, slots=True)
class StartedSoundServer:
    """The lease's sound server, and the one process attach started for it."""

    server: PulseServer
    processes: tuple[BackgroundProcess, ...]


def server_script(layout: ScratchLayout) -> str:
    """Return the server's startup script: the socket, the two sinks, and the two defaults.

    Args:
        layout: Where the socket goes.

    Returns:
        The script text, one PulseAudio command per line.

    Raises:
        AttachError: The socket path is too long for a Unix socket, or holds a character a
            module argument cannot quote.
    """
    socket = str(layout.pulse_socket)
    if not layout.socket_fits():
        raise AttachError(
            f"the sound server's socket path is {len(socket.encode())} bytes, over the "
            f"{MAX_SOCKET_PATH_BYTES} a Unix socket allows; configure a shorter scratch root"
        )
    if _UNQUOTABLE & set(socket):
        raise AttachError("the scratch path holds a character the sound server cannot quote")
    lines = (
        f'load-module module-native-protocol-unix socket="{socket}" auth-anonymous=1',
        f"load-module module-null-sink sink_name={SPEAKER_SINK} norewinds=1",
        f"load-module module-null-sink sink_name={MICROPHONE_SINK} norewinds=1",
        f"set-default-sink {SPEAKER_SINK}",
        f"set-default-source {MICROPHONE_SINK}.monitor",
    )
    return "\n".join(lines) + "\n"


async def start_sound_server(
    session: CellSession, layout: ScratchLayout, deadline: Deadline
) -> StartedSoundServer:
    """Start the lease's PulseAudio server and return once it answers on its socket.

    Args:
        session: The Cell's session; the server starts through it.
        layout: Where the socket, cookie, script, state and log go.
        deadline: The start-up's budget.

    Returns:
        The ready server and its process.

    Raises:
        AttachError: The script could not be written safely, the server could not start, exited
            early, or never answered within the deadline. The process is already stopped.
    """
    script = layout.pulse_dir / "hive.pa"
    await session.put_file(script, server_script(layout).encode())
    server = PulseServer(
        socket=layout.pulse_socket,
        cookie=layout.pulse_cookie,
        speaker=SPEAKER_SINK,
        microphone=MICROPHONE_SINK,
    )
    started: list[BackgroundProcess] = []
    try:
        started.append(await start_process(session, _server_spec(layout, script, server)))
        await _await_answer(session, started[0], server, layout, deadline)
    except BaseException:
        # Leave nothing behind whatever went wrong, cancellation included, then let it propagate.
        await stop_all(session, started)
        raise
    return StartedSoundServer(server=server, processes=tuple(started))


def _server_spec(layout: ScratchLayout, script: Path, server: PulseServer) -> BackgroundSpec:
    """Build the server's command: no default config, this script, locked down, state in scratch."""
    argv: tuple[str, ...] = ("pulseaudio", "-n", "-F", str(script), "--daemonize=no")
    argv += ("--use-pid-file=no",)
    # Never idle out, never exit or load a module on a client's say-so, no shared memory in
    # /dev/shm (outside scratch), and diagnostics to the log attach reads on failure.
    argv += ("--exit-idle-time=-1", "--disallow-exit", "--disallow-module-loading")
    argv += ("--disable-shm=yes", "--log-target=stderr")
    env = {
        **layout.home_environment(),
        **server.environment(),
        "PULSE_RUNTIME_PATH": str(layout.pulse_dir),
        "PULSE_STATE_PATH": str(layout.pulse_dir),
    }
    return BackgroundSpec(argv=argv, env=env, log_path=layout.pulse_dir / "pulseaudio.log")


async def _await_answer(
    session: CellSession,
    process: BackgroundProcess,
    server: PulseServer,
    layout: ScratchLayout,
    deadline: Deadline,
) -> None:
    """Wait until the server answers `pactl info` on its own socket."""
    probe = ExecSpec(argv=("pactl", "info"), env=server.environment(), timeout_s=PROBE_TIMEOUT_S)
    while True:
        answer = await run(session, probe)
        if answer.exit_code == 0:
            return
        if not await session.is_running(process):
            logged = await _read_log(session, layout)
            raise AttachError(f"the sound server exited before it was ready: {sanitised(logged)}")
        if deadline.expired():
            raise AttachError(f"the sound server did not answer within {deadline.seconds}s")
        log.debug("exoskeleton.sound_server_not_ready", exit_code=answer.exit_code)
        await deadline.pause()


async def _read_log(session: CellSession, layout: ScratchLayout) -> bytes:
    """Return the server's log, or nothing if it never wrote one."""
    try:
        return await session.get_file(layout.pulse_dir / "pulseaudio.log")
    except FileNotFoundError:
        return b""  # It died before its first write.
