"""Wrap the docker command line for the swarm tests' device containers, and clean up after them.

A test that runs a Pollen Packet on "a second machine" needs a container with a filesystem, a
process table and a network namespace of its own, and needs to look into it: run a command, read
its output, list its processes, list a directory's files, cut it off the network and put it back.
This module is that, over the `docker` command line (no SDK: the tests describe what a person at a
terminal would do). `Janitor` starts containers and networks and owns their lifetimes: every one
carries a label naming the janitor's run, `close` removes everything with that label (so a
resource whose creation timed out half way is still found), and a finalizer registered with
`atexit` calls it again if the test process dies without having. Every call has a timeout.

Calls are synchronous and block for the length of one docker command, normally well under a
second; a test that cannot afford that calls them through `asyncio.to_thread`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by `e2e.swarm.image` (which
    builds the image with `run`), `e2e.swarm.enrolled` and the swarm integration and end-to-end
    tests; needs a docker daemon, which the tests check for before they use this module.

Key invariants:
    - Nothing a janitor started outlives `close`: containers are removed before the networks they
      were on, and a resource that cannot be removed is reported, not ignored.
    - Every call to a program has a timeout and a failure names the program, never its
      arguments, because an argument may be a secret (an invite).
    - A process or file snapshot excludes the probe that took it, so two snapshots of an
      untouched container are equal.

See Also:
    - e2e/swarm/device/Dockerfile for what runs inside the containers this starts.
    - tests/integration/docker_helpers.py for `daemon_reachable`, the skip probe.
"""

from __future__ import annotations

import atexit
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

LABEL = "hivemind.test"  # Marks every container and network the swarm tests start.
LABEL_VALUE = "e2e-swarm"
RUN_LABEL = "hivemind.test.run"  # Names the janitor that started one, so `close` finds its own.
DOCKER_TIMEOUT_S = 60.0  # One docker command against a local daemon takes well under a second.
START_TIMEOUT_S = 120.0  # A `docker run` of a local image, including a slow first start.
REMOVE_TIMEOUT_S = 120.0  # Removing containers waits for each to be killed.

__all__ = [
    "BRIDGE",
    "DOCKER_TIMEOUT_S",
    "CommandError",
    "CommandResult",
    "Container",
    "ContainerSpec",
    "FileEntry",
    "Janitor",
    "Network",
    "daemon_answers",
    "docker",
    "file_entries",
    "process_table",
    "run",
]


class CommandError(Exception):
    """A program could not be run, did not finish in time, or failed where success was needed."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a finished program left: its exit code and everything it wrote."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str

    def require(self) -> CommandResult:
        """Return this result if the program succeeded, else raise `CommandError` saying why."""
        if self.exit_code != 0:
            said = self.stderr.strip() or "(no output)"
            raise CommandError(f"{_name_of(self.argv)} exited {self.exit_code}: {said}")
        return self


def _name_of(argv: Sequence[str]) -> str:
    """Name a command for an error message: the program and its first words, no secrets."""
    return " ".join(argv[:3])


def run(
    argv: Sequence[str], timeout_s: float, *, cwd: Path | None = None, stdin: str | None = None
) -> CommandResult:
    """Run a program to completion and collect its output.

    Args:
        argv: The program and its arguments; never run through a shell.
        timeout_s: Seconds the program may take before it is killed.
        cwd: The directory to run it in; the current one when None.
        stdin: Text to feed to it, or None for none.

    Returns:
        The exit code and the decoded output; a non-zero exit is not an error here.

    Raises:
        CommandError: The program could not be started or did not finish in time.
    """
    try:
        # SAFETY: argv is a list built by the swarm tests (docker, uv), never a shell string, and
        # the run is bounded by `timeout_s`.
        done = subprocess.run(  # noqa: S603
            list(argv),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=cwd,
            input=stdin,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        raise CommandError(f"{_name_of(argv)} did not finish in {timeout_s:g} s") from expired
    except OSError as failure:
        raise CommandError(f"{_name_of(argv)} could not be run: {failure}") from failure
    return CommandResult(tuple(argv), done.returncode, done.stdout, done.stderr)


def docker(*args: str, timeout_s: float = DOCKER_TIMEOUT_S, check: bool = True) -> CommandResult:
    """Run `docker` with `args`.

    Args:
        *args: The docker subcommand and its arguments.
        timeout_s: Seconds the command may take.
        check: Whether a non-zero exit raises `CommandError`.

    Returns:
        The result of the command.

    Raises:
        CommandError: The command failed to run or time out, or exited non-zero with `check`.
    """
    result = run(("docker", *args), timeout_s)
    return result.require() if check else result


def daemon_answers() -> bool:
    """Whether a docker daemon answers the command line; the swarm tests skip when it does not.

    The skip probe of `integration.docker_helpers` asks the optional docker SDK, which a default
    environment does not install, so it would skip tests whose harness needs only the command
    line; this asks the command line itself.
    """
    try:
        shown = docker("version", "--format", "{{.Server.Version}}", timeout_s=15, check=False)
    except CommandError:  # No docker program at all, or one that hangs.
        return False
    return shown.exit_code == 0


class FileEntry(NamedTuple):
    """One file in a snapshot of a directory: what kind it is, its permissions and its size."""

    kind: str  # `find`'s type letter: f file, d directory, l link.
    mode: str  # Octal permissions.
    size: int


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    """How to start a container; every field but the image is optional."""

    image: str
    command: tuple[str, ...] = ()  # Replaces the image's own command when not empty.
    hosts: Mapping[str, str] = field(default_factory=dict)  # Name to address, for `--add-host`.
    environment: Mapping[str, str] = field(default_factory=dict)
    network: str | None = None  # A network to join instead of the default bridge.
    capabilities: tuple[str, ...] = ()  # Linux capabilities to add, such as NET_ADMIN.
    # Files on this machine copied into the container before its command starts, by the absolute
    # path they take there (the directory must exist in the image): what the command reads first.
    files: Mapping[str, Path] = field(default_factory=dict)


class Container:
    """One running container, looked into with the docker command line."""

    def __init__(self, name: str) -> None:
        """Wrap the container called `name`; `Janitor.start` is the way to get one."""
        self.name = name

    def exec(
        self, argv: Sequence[str], *, timeout_s: float = DOCKER_TIMEOUT_S, check: bool = True
    ) -> CommandResult:
        """Run a command inside the container and wait for it.

        Args:
            argv: The command, never run through a shell unless it names one.
            timeout_s: Seconds the command may take.
            check: Whether a non-zero exit raises `CommandError`.

        Returns:
            Its exit code and output.
        """
        return docker("exec", self.name, *argv, timeout_s=timeout_s, check=check)

    def logs(self) -> str:
        """Return everything the container's main process has written to either stream."""
        result = docker("logs", self.name)
        return result.stdout + result.stderr

    def is_running(self) -> bool:
        """Whether the container is up (it exits if its main process does)."""
        shown = docker("inspect", "--format", "{{.State.Running}}", self.name, check=False)
        return shown.stdout.strip() == "true"

    def exists(self, path: str) -> bool:
        """Whether a path exists inside the container."""
        return self.exec(["test", "-e", path], check=False).exit_code == 0

    def processes(self) -> dict[int, str]:
        """Snapshot the process table: every pid in the container and its command name.

        The shell prints its own pid and then becomes `ps`, so the one row that exists only
        because of this call is known and dropped; two snapshots of a quiet container are equal.
        """
        return process_table(self.exec(["sh", "-c", "echo $$; exec ps -eo pid=,comm="]).stdout)

    def file_tree(self, path: str) -> dict[str, FileEntry]:
        """Snapshot a directory: every file under it by relative path, with kind, mode and size.

        Modification times are left out on purpose: adding and removing a file touches its
        directory, and "left as found" is about what is there, not about when it was touched.

        Args:
            path: A directory inside the container.

        Returns:
            Relative path to entry; empty for an empty directory.
        """
        listing = self.exec(["find", path, "-mindepth", "1", "-printf", "%y %m %s %P\\n"])
        return file_entries(listing.stdout)

    def remove(self) -> None:
        """Kill the container and remove it with its volumes; a container already gone is fine."""
        docker("rm", "--force", "--volumes", self.name, timeout_s=REMOVE_TIMEOUT_S, check=False)


def process_table(output: str) -> dict[int, str]:
    """Parse the probe's output: its own pid on the first line, then `pid command` rows.

    Args:
        output: What `echo $$; exec ps -eo pid=,comm=` printed.

    Returns:
        Pid to command name, without the probe's own row.
    """
    lines = output.splitlines()
    probe, rows = int(lines[0]), lines[1:]
    table = {int(pid): name for pid, name in (row.split(None, 1) for row in rows)}
    table.pop(probe, None)
    return table


def file_entries(output: str) -> dict[str, FileEntry]:
    """Parse the rows `find -printf` printed, one file per row, into relative path to entry.

    Args:
        output: One row per file: kind, octal mode, size and path, the path being the rest of
            the row so that it may hold spaces.

    Returns:
        The entries by path.
    """
    tree: dict[str, FileEntry] = {}
    for row in output.splitlines():
        kind, mode, size, name = row.split(" ", 3)
        tree[name] = FileEntry(kind, mode, int(size))
    return tree


class Network:
    """A docker network containers can be cut off from and put back on."""

    def __init__(self, name: str) -> None:
        """Wrap the network called `name`; `Janitor.network` makes one, `BRIDGE` is the default."""
        self.name = name

    @property
    def gateway(self) -> str:
        """The host's address on this network: where a listener bound for its containers goes."""
        template = "{{(index .IPAM.Config 0).Gateway}}"
        return docker("network", "inspect", self.name, "--format", template).stdout.strip()

    def disconnect(self, container: Container) -> None:
        """Cut the container off this network, as unplugging a cable would."""
        docker("network", "disconnect", "--force", self.name, container.name)

    def connect(self, container: Container) -> None:
        """Put the container back on this network (it may get a different address)."""
        docker("network", "connect", self.name, container.name)


BRIDGE = Network("bridge")  # Docker's default network; containers started without one join it.


class Janitor:
    """Starts the containers and networks of one test and removes every one of them."""

    def __init__(self) -> None:
        """Start a run; `close` must be called, and is called at exit if it was not."""
        self.run_id = uuid.uuid4().hex[:10]
        self._started = 0
        atexit.register(self.close)

    def start(self, spec: ContainerSpec) -> Container:
        """Start a container from `spec`, detached, with an init process as its first.

        An init process (`docker run --init`) reaps orphaned children, as a real device's does;
        without one the packet would be pid 1 and every program a lease killed whose parent had
        gone would stay in the process table as a zombie.

        Args:
            spec: What to run.

        Returns:
            The running container.

        Raises:
            CommandError: The container did not start.
        """
        self._started += 1
        name = f"hivemind-test-{self.run_id}-{self._started}"
        args = ["--init", "--name", name, *self._labels()]
        args += ["--network", spec.network] if spec.network else []
        for host, address in spec.hosts.items():
            args += ["--add-host", f"{host}:{address}"]
        for variable, value in spec.environment.items():
            args += ["--env", f"{variable}={value}"]
        for capability in spec.capabilities:
            args += ["--cap-add", capability]
        if not spec.files:
            docker("run", "--detach", *args, spec.image, *spec.command, timeout_s=START_TIMEOUT_S)
            return Container(name)
        # Created, filled and then started: a file `docker cp` puts in a container that has
        # already run its command is too late for the command that reads it first.
        docker("create", *args, spec.image, *spec.command, timeout_s=START_TIMEOUT_S)
        for target, source in spec.files.items():
            docker("cp", str(source), f"{name}:{target}")
        docker("start", name, timeout_s=START_TIMEOUT_S)
        return Container(name)

    def network(self) -> Network:
        """Create a network of its own, for a test that cuts a container off from it."""
        name = f"hivemind-test-{self.run_id}-net{self._started}"
        self._started += 1
        docker("network", "create", *self._labels(), name)
        return Network(name)

    def close(self) -> None:
        """Remove every container, then every network, this janitor started; safe to repeat.

        Raises:
            CommandError: Something could not be removed; it is named, so the leak is seen.
        """
        selector = f"label={RUN_LABEL}={self.run_id}"
        containers = docker("ps", "--all", "--quiet", "--filter", selector).stdout.split()
        if containers:
            docker("rm", "--force", "--volumes", *containers, timeout_s=REMOVE_TIMEOUT_S)
        networks = docker("network", "ls", "--quiet", "--filter", selector).stdout.split()
        if networks:
            docker("network", "rm", *networks)
        atexit.unregister(self.close)

    def _labels(self) -> list[str]:
        """The labels that make a resource findable by this janitor and by a person."""
        return ["--label", f"{LABEL}={LABEL_VALUE}", "--label", f"{RUN_LABEL}={self.run_id}"]
