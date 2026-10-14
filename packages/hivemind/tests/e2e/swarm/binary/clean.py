"""Run a Pollen Packet binary on a device that has nothing but Debian 12's base system.

Raspberry Pi OS is Debian 12 with a desktop and a few tools on top, so a plain `debian:bookworm`
container is the x86-64 stand-in for a device that was never given Python, pip or a compiler: the
only thing on it that is not Debian's is the file an operator copied there. `CleanDevice` is that
container and the handful of things an operator does to it with the binary, in the order the
runbook (docs/runbooks/enrolling-a-device.md) gives them: copy the file over and `install` it onto
the path as root, then, as the account the operator logs in with (`pi`, never root), run its
commands with no `--data-dir`, start the packet by hand where there is no service manager and stop
it the way one would. It also snapshots every file on the device, so a scenario can show that
what the packet put there is gone once the operator has removed it.

The packet is started under a one-line supervisor that records its process id, writes its output
to a log and writes its exit status when it ends; all three live in `HARNESS_DIR`, which the
snapshot leaves out, so what the harness itself keeps on the device is never mistaken for what the
packet left.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Used by the phase 11 packaging
    scenario; builds on `e2e.swarm.container` for the janitor, the containers and the file
    snapshot format, and calls `docker` through it.

Key invariants:
    - Every call has a timeout and every wait is a bounded poll; a packet that does not stop in
      time raises with the name of the limit.
    - Calls are synchronous and block for the length of one docker command or one poll interval,
      so a scenario whose Hive Stand shares the thread's event loop calls them through
      `asyncio.to_thread`; a command that dials the Stand (enrolment) would otherwise wait for a
      loop that it is blocking.
    - A command runs as the operator unless it says `user=ROOT`; only installing the binary,
      listing processes and taking a snapshot are done as root, as the runbook's `sudo` is.
    - A snapshot lists files and links only, never directories, and never `HARNESS_DIR`.

See Also:
    - e2e/swarm/binary/build.py for the file this runs.
    - e2e/swarm/container.py for the `Container` this wraps.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from e2e.swarm.container import (
    BRIDGE,
    CommandError,
    CommandResult,
    Container,
    ContainerSpec,
    FileEntry,
    Janitor,
    Network,
    docker,
    file_entries,
    run,
)

CLEAN_IMAGE = "debian:bookworm"  # Debian 12: Raspberry Pi OS's base, glibc 2.36, no Python.
INSTALL_PATH = "/usr/local/bin/pollen"  # Where the runbook puts the binary on a device.
COPIED_PATH = "/tmp/pollen"  # noqa: S108  # Where `docker cp` leaves it first: inside the device.
OPERATOR = "pi"  # The account an operator works as on a Raspberry Pi OS: not root.
ROOT = "root"  # What `sudo` is in the runbook: installing the binary, and the harness's own looks.
HOME = f"/home/{OPERATOR}"
POLLEN_DIR = f"{HOME}/.local/share/pollen"  # `pollen`'s default data directory (XDG).
UNIT_PATH = f"{HOME}/.config/systemd/user/pollen.service"  # What `pollen install` writes.
HARNESS_DIR = "/run/harness"  # The harness's own files on the device; not part of a snapshot.
LOG_FILE = f"{HARNESS_DIR}/packet.log"  # Everything the packet writes to either stream.
PID_FILE = f"{HARNESS_DIR}/packet.pid"  # The packet's process id, as the supervisor saw it start.
EXIT_FILE = f"{HARNESS_DIR}/packet.exit"  # Its exit status, written when it ends.
# The supervisor: run the command given as arguments in the background (so its pid is known), wait
# for it and keep its status. The shell, not the packet, outlives a stop long enough to write it.
SUPERVISOR = f'"$@" >{LOG_FILE} 2>&1 & echo $! >{PID_FILE}; wait $!; echo $? >{EXIT_FILE}'
# The process table the way `ps` reads it, because a clean Debian has no `ps`: /proc, one line each.
LIST_PROCESSES = (
    'for p in /proc/[0-9]*; do echo "${p#/proc/} $(cat $p/comm 2>/dev/null)'
    ' $(tr "\\0" " " < $p/cmdline 2>/dev/null)"; done'
)
EXEC_TIMEOUT_S = 120.0  # One command on the device: a binary unpacking itself, a TLS handshake.
COPY_TIMEOUT_S = 120.0  # `docker cp` of a 40 MB file.
APPEAR_TIMEOUT_S = 30.0  # From the supervisor starting to its first file existing.
STOP_TIMEOUT_S = 60.0  # A packet that was asked to stop: its own limit is twenty seconds.
POLL_S = 0.25  # How often the device is looked at while a file is awaited.

__all__ = [
    "CLEAN_IMAGE",
    "EXIT_FILE",
    "HARNESS_DIR",
    "INSTALL_PATH",
    "LOG_FILE",
    "OPERATOR",
    "PID_FILE",
    "POLLEN_DIR",
    "ROOT",
    "UNIT_PATH",
    "CleanDevice",
    "start_clean",
]


def start_clean(
    janitor: Janitor, hosts: Mapping[str, str], network: Network = BRIDGE
) -> CleanDevice:
    """Start a clean Debian 12 container that the janitor removes.

    Args:
        janitor: Owns the container, which it removes at its `close`.
        hosts: Host names to map to addresses inside the container (`--add-host`), such as the
            name an invite tells the packet to dial.
        network: The network to start on; docker's default bridge when omitted.

    Returns:
        The running device.

    Raises:
        CommandError: The container did not start.
    """
    spec = ContainerSpec(
        image=CLEAN_IMAGE,
        command=("sleep", "infinity"),  # A booted machine with nothing running on it.
        hosts=hosts,
        network=None if network is BRIDGE else network.name,
    )
    container = janitor.start(spec)
    # The operator's account, as the first user of a Raspberry Pi OS has one, and the directory
    # the harness keeps its supervisor files in, which that account has to be able to write.
    container.exec(["useradd", "--create-home", "--shell", "/bin/sh", OPERATOR])
    container.exec(["mkdir", "--mode", "1777", HARNESS_DIR])
    return CleanDevice(container)


class CleanDevice:
    """A container with Debian 12's base system, and what an operator does with a binary on it."""

    def __init__(self, container: Container) -> None:
        """Wrap a running container; `start_clean` is the way to get one."""
        self.container = container

    @property
    def name(self) -> str:
        """The container's name, for a failure message."""
        return self.container.name

    def exec(
        self,
        argv: Sequence[str],
        *,
        stdin: str | None = None,
        timeout_s: float = EXEC_TIMEOUT_S,
        check: bool = True,
        user: str = OPERATOR,
    ) -> CommandResult:
        """Run a command on the device and wait for it.

        Args:
            argv: The command, never run through a shell unless it names one.
            stdin: Text to feed it, as a person pipes in an invite; none when None.
            timeout_s: Seconds the command may take.
            check: Whether a non-zero exit raises `CommandError`.
            user: The account to run it as: the operator's, or `ROOT` for what `sudo` does.

        Returns:
            Its exit code and output.
        """
        flags = ["--user", user, *(["-i"] if stdin is not None else [])]
        result = run(("docker", "exec", *flags, self.name, *argv), timeout_s, stdin=stdin)
        return result.require() if check else result

    def install(self, binary: Path) -> None:
        """Put `binary` on the path the way the runbook does: copy it over, then `install` it.

        Args:
            binary: The single-file packet on the host.
        """
        # A copy keeps the file's mode bits, but an operator's `scp` or a zip does not, so the
        # mode is set by `install`, as the runbook says to.
        docker("cp", str(binary), f"{self.name}:{COPIED_PATH}", timeout_s=COPY_TIMEOUT_S)
        self.exec(["install", "-m", "0755", COPIED_PATH, INSTALL_PATH], user=ROOT)
        self.exec(["rm", COPIED_PATH], user=ROOT)

    def start(self, argv: Sequence[str]) -> None:
        """Start the packet in the background, as an operator runs it by hand in a terminal.

        Args:
            argv: The command, such as the one `pollen install` printed.

        Raises:
            CommandError: The supervisor did not start within `APPEAR_TIMEOUT_S`.
        """
        docker(
            "exec", "--detach", "--user", OPERATOR, self.name, "sh", "-c", SUPERVISOR, "sh", *argv
        )
        self._wait_for(PID_FILE, APPEAR_TIMEOUT_S)

    def stop(self, timeout_s: float = STOP_TIMEOUT_S) -> int:
        """Stop the packet with SIGTERM, as a service manager does, and return its exit status.

        Args:
            timeout_s: Seconds the packet may take to end.

        Returns:
            The packet's exit status; 0 for a clean stop.

        Raises:
            CommandError: The packet had not ended within `timeout_s`.
        """
        # A packet that ended by itself has nothing to signal, and says why in its log.
        if not self.container.exists(EXIT_FILE):
            kill = 'kill -TERM "$(cat "$1")"'
            self.exec(["sh", "-c", kill, "sh", PID_FILE], check=False)
        self._wait_for(EXIT_FILE, timeout_s)
        return int(self.exec(["cat", EXIT_FILE]).stdout.strip())

    def log(self) -> str:
        """Return everything the packet has written so far, for a failure message."""
        return self.exec(["cat", LOG_FILE], check=False).stdout

    def processes(self) -> str:
        """Return one line per process on the device, for a failure message: pid, name, command."""
        return self.exec(["sh", "-c", LIST_PROCESSES], check=False, user=ROOT).stdout

    def files(self) -> dict[str, FileEntry]:
        """Snapshot every file and link on the device's own filesystem.

        Returns:
            Absolute path to kind, mode and size. Directories are left out: one the packet made
            on the way (a parent of its service unit) is the operator's to keep, and an empty
            one holds nothing of the packet's. `HARNESS_DIR` is left out too.
        """
        # -xdev: /proc, /sys and /dev are not files of the device. Modification times are not
        # printed: "left as found" is about what is there, not about when it was touched.
        find = ["find", "/", "-xdev", "-path", HARNESS_DIR, "-prune", "-o"]
        find += ["!", "-type", "d", "-printf", "%y %m %s %p\\n"]
        return file_entries(self.exec(find, user=ROOT).stdout)

    def _wait_for(self, path: str, timeout_s: float) -> None:
        """Poll until `path` exists on the device, or raise once `timeout_s` has passed."""
        deadline = time.monotonic() + timeout_s
        while not self.container.exists(path):
            if time.monotonic() > deadline:
                raise CommandError(f"{path} did not appear on {self.name} in {timeout_s:g} s.")
            time.sleep(POLL_S)  # A local docker call per poll: the wait costs what it says.
