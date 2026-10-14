"""Give a real Cell container a real tunnel, and open a session on it, for the Propolis proof.

The Propolis checks run fixed, read-only commands (`cat /proc/net/route`, `cat /etc/resolv.conf`,
`ps -eo comm=`, `cat /proc/net/tcp`) and judge what they print. A unit test hands the judges text
written by hand; this module lets a test hand them what a real kernel prints in the real Cell
image, with and without a tunnel. `CellJanitor.start_cell` starts a container from the Cell image
with its entrypoint replaced by `sleep`, so the image is as shipped (its user, its files, its
`PATH`) and nothing is installed in it. `give_tunnel` then does what a VPN client does, from
outside: the Cell image has no `ip` and the Cell needs no capability, so a short-lived helper
container joins the Cell's network namespace with `NET_ADMIN`, creates a `tun0` device, gives it an
address and routes the whole internet into it as two halves, the way OpenVPN's `redirect-gateway
def1` does. `session_on` is a `CellSession` whose commands run in the container with `docker exec`:
the Queen-side path to a Virtual Cell's session does not exist yet, and this is what stands in for
it, so the probe under test is the shipped one and only the transport is a test's.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by
    tests/integration/test_docker_propolis.py; builds on `e2e.swarm.container` for the docker
    command line and the cleanup, and needs a docker daemon and a host with `/dev/net/tun`, which
    the test checks for before it uses this module.

Key invariants:
    - The Cell container is the image as shipped: only its entrypoint and resolver are chosen.
    - Everything a test starts is labelled and removed by `CellJanitor.close`, which also runs at
      exit if the test did not get there; the tunnel helper removes itself.
    - Every wait is a bounded poll of a real process or socket in the container, never a sleep.

See Also:
    - tests/e2e/swarm/container.py for `Janitor`, `Container` and `docker`.
    - hivemind.hive.propolis for the probe these helpers are used to try.
"""

from __future__ import annotations

import functools
import hashlib
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from e2e.swarm.container import (
    LABEL,
    LABEL_VALUE,
    START_TIMEOUT_S,
    CommandError,
    Container,
    Janitor,
    docker,
    run,
)

from hivemind.cell import CompletedCommand, ExecSpec, FakeSession
from waggle.clock import FakeClock

HOST_ALIAS = "host.docker.internal"  # How a container names its host: where a Queen listens.
WARDEN_LOG = "warden.log"  # In the image's working directory, which the Cell's user can write.
TUNNEL_ADDRESS = "10.8.0.2/24"  # tun0's own address, in the private network a VPN gives a client.
TUNNEL_RESOLVER = "10.8.0.1"  # The VPN's own resolver: an address inside that network.
BUILD_TIMEOUT_S = 900.0  # An apt layer on a cold mirror; seconds when warm.
SETTLE_S = 20.0  # How long a started program may take to show in the container.
POLL_S = 0.2
# The one thing the Cell image lacks that a tunnel needs: the Cell image ships no `ip`, and adding
# it there would change the image under test, so a helper image carries it.
TOOLS_DOCKERFILE = """\
FROM ubuntu:24.04
RUN apt-get update \\
    && apt-get install -y --no-install-recommends iproute2 \\
    && rm -rf /var/lib/apt/lists/*
"""
# What a VPN client does to a network namespace, minus the encryption: a tun device with an address
# in the VPN's network, and every destination routed into it as two halves, each more specific than
# the default route the container started with, so that route is left in place and shadowed.
TUNNEL_SCRIPT = (
    "ip tuntap add dev tun0 mode tun"
    f" && ip addr add {TUNNEL_ADDRESS} dev tun0"
    " && ip link set tun0 up"
    " && ip route add 0.0.0.0/1 dev tun0"
    " && ip route add 128.0.0.0/1 dev tun0"
)

__all__ = [
    "SETTLE_S",
    "TUNNEL_RESOLVER",
    "CellJanitor",
    "boot_warden",
    "give_tunnel",
    "poll",
    "run_in_background",
    "session_on",
    "tools_image",
    "tun_device_usable",
    "warden_log",
]


class CellJanitor(Janitor):
    """A Janitor that also starts Cells: containers of one Cell image, with a resolver chosen."""

    def __init__(self, image: str) -> None:
        """Start a run whose Cells are containers of `image`, as shipped."""
        super().__init__()
        self._image = image

    def start_cell(self, resolver: str, *, reach_host: bool = False) -> Container:
        """Start a container from the Cell image that does nothing but stay up.

        Args:
            resolver: The nameserver written to the container's `/etc/resolv.conf`.
            reach_host: Whether the container can name its host as `host.docker.internal`, which
                is how a Cell dials a Queen that listens on the machine the test runs on.

        Returns:
            The running container.

        Raises:
            CommandError: The container did not start.
        """
        self._started += 1
        name = f"hivemind-test-{self.run_id}-{self._started}"
        args = ["run", "--detach", "--init", "--name", name, *self._labels()]
        args += ["--add-host", f"{HOST_ALIAS}:host-gateway"] if reach_host else []
        args += ["--dns", resolver, "--entrypoint", "sleep", self._image, "infinity"]
        docker(*args, timeout_s=START_TIMEOUT_S)
        return Container(name)


@functools.cache
def tools_image() -> str:
    """Return the tag of a small image that has `ip`, building it on first use.

    The tag is a hash of the Dockerfile, so a change to it builds a new image and nothing else does.

    Raises:
        CommandError: The image did not build.
    """
    digest = hashlib.sha256(TOOLS_DOCKERFILE.encode()).hexdigest()[:12]
    tag = f"hivemind-test-ip-tools:{digest}"
    if docker("image", "inspect", tag, check=False).exit_code == 0:
        return tag
    # The Dockerfile arrives on standard input, so there is no build context to send.
    build = ("docker", "build", "--tag", tag, "--label", f"{LABEL}={LABEL_VALUE}", "-")
    run(build, BUILD_TIMEOUT_S, stdin=TOOLS_DOCKERFILE).require()
    return tag


def tun_device_usable() -> bool:
    """Whether a container on this daemon can be given `/dev/net/tun`, which a tunnel needs."""
    args = ["run", "--rm", "--device", "/dev/net/tun", "--entrypoint", "true", tools_image()]
    return docker(*args, timeout_s=START_TIMEOUT_S, check=False).exit_code == 0


def give_tunnel(cell: Container) -> None:
    """Give `cell` a `tun0` with the whole internet routed into it, from outside the Cell.

    Args:
        cell: A running container.

    Raises:
        CommandError: The helper could not make the device or the routes.
    """
    helper = ["run", "--rm", "--network", f"container:{cell.name}", "--cap-add", "NET_ADMIN"]
    helper += ["--device", "/dev/net/tun", "--entrypoint", "sh", tools_image()]
    docker(*helper, "-eu", "-c", TUNNEL_SCRIPT, timeout_s=START_TIMEOUT_S)


def boot_warden(cell: Container, environment: Mapping[str, str]) -> None:
    """Start the image's own Warden in `cell`, as its entrypoint would, and leave it running.

    The container's main process is `sleep` so that the tunnel can be made before the Warden
    boots; this runs what the image's entrypoint runs, with `environment` as its bootstrap, and
    keeps its output in `WARDEN_LOG` for `warden_log` to read if it never connects.

    Args:
        cell: A running container of the Cell image.
        environment: The `HIVEMIND_*` bootstrap the Warden reads.

    Raises:
        CommandError: The command could not be started.
    """
    args = ["exec", "--detach"]
    for name, value in environment.items():
        args += ["--env", f"{name}={value}"]
    docker(*args, cell.name, "sh", "-c", f"exec hivemind-in-cell > {WARDEN_LOG} 2>&1")


def warden_log(cell: Container) -> str:
    """Return what the Warden `boot_warden` started has written, for a failure to show."""
    return cell.exec(["cat", WARDEN_LOG], check=False).stdout


def run_in_background(cell: Container, argv: Sequence[str]) -> None:
    """Start a program in `cell` and leave it running, as `docker exec --detach` does."""
    docker("exec", "--detach", cell.name, *argv)


def poll(condition: Callable[[], bool], what: str, timeout_s: float = SETTLE_S) -> None:
    """Wait for `condition` to hold, checking it every `POLL_S` seconds.

    Args:
        condition: Asked again and again until it is true.
        what: What is being waited for, for the error.
        timeout_s: Seconds to wait before giving up.

    Raises:
        CommandError: The condition did not hold in time.
    """
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            raise CommandError(f"{what} did not happen within {timeout_s:g} s")
        time.sleep(POLL_S)


def session_on(cell: Container) -> FakeSession:
    """Return a CellSession whose commands run in `cell` with `docker exec`.

    The session's answers are the container's own: its exit code and everything it wrote. Its
    files stay in the fake's memory and never reach the container, because the probe under test
    only runs commands.
    """

    def respond(spec: ExecSpec) -> CompletedCommand:
        done = cell.exec(spec.argv, timeout_s=spec.timeout_s, check=False)
        return CompletedCommand(
            exit_code=done.exit_code,
            stdout=done.stdout.encode(),
            stderr=done.stderr.encode(),
            duration_s=0.0,
        )

    # The scratch directory is never touched: no file is read or written through this session.
    return FakeSession(Path("/scratch"), FakeClock(), responder=respond)
