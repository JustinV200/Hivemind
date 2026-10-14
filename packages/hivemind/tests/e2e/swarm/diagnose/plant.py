"""Plant a fault on a container device, and reset the links that would have shown it.

The reversible case of phase 11's last exit criterion needs a problem that a few commands name and
fix, within reach of the device's access level, and that stops the link without the container
knowing anything else is wrong. The device dials the Hive Stand by the host name `hive.test`, which
only the container's own `/etc/hosts` maps (`e2e.swarm.enrolled`), so the fault is that file's line
for the name, replaced with an address nothing answers on: a stale entry, the commonest wrong
resolver setting there is. A connection that is already open does not look the name up again, so
`sever` ends the packet's link and the Warden's from the Hive Stand's end, as a reset would, and
the next dial of each is the first to meet the fault. Nothing here fixes anything: the Drone does,
and `address_of` is how a scenario reads the name the way the Drone's `getent` does.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the phase 11 diagnosis
    scenarios; builds on `e2e.swarm.container` for the container and on the Hive's swarm gate for
    the links, which a test of the Queen's own end has no public way to close.

Key invariants:
    - The wrong line replaces the right one in place (`cat >`, never a rename): `/etc/hosts` is a
      bind mount in a container, which a rename cannot replace.
    - The wrong address is a loopback one with no listener, so a dial that meets it is refused at
      once instead of waiting out a timeout, and the redial schedule is the only clock in play.
    - Severing closes both links and nothing else: the Hive's registry, the Queen's tables and the
      container's network are left as they were.

See Also:
    - e2e/swarm/enrolled.py for `HIVE_HOSTNAME` and the `--add-host` line the container starts with.
    - tests/e2e/test_phase11_exit_diagnose.py for the scenario that plants and severs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from e2e.swarm.container import Container
from e2e.swarm.enrolled import HIVE_HOSTNAME

from hivemind.cli.compose import Hive
from hivemind.swarm import SwarmNode
from waggle.clock import Clock

WRONG_ADDRESS = "127.0.0.9"  # Loopback, so a dial is refused at once; nothing listens on it.
HOSTS_FILE = "/etc/hosts"  # What the container resolves a name through before it asks anyone.
POLL_S = 1.0  # How often the name is looked up while a scenario waits for it to come right.

__all__ = [
    "HOSTS_FILE",
    "POLL_S",
    "WRONG_ADDRESS",
    "address_of",
    "hosts_text",
    "plant_wrong_entry",
    "planting_script",
    "sever",
    "wait_for_address",
]


def planting_script(hosts_file: str = HOSTS_FILE) -> str:
    """Return the shell script that makes `hosts_file` map the Hive's name to the wrong address.

    Args:
        hosts_file: The file to change; the container's `/etc/hosts` unless a test points it at
            a copy, which is how the script itself is tested without a container.

    Returns:
        A script for `sh -c`: every line but the Hive's is kept, then the wrong one is added.
    """
    # Written back through the same file: a bind mount cannot be renamed over.
    return (
        f"grep -v {HIVE_HOSTNAME} {hosts_file} > /tmp/hosts.planted; "
        f"printf '%s\\t%s\\n' {WRONG_ADDRESS} {HIVE_HOSTNAME} >> /tmp/hosts.planted; "
        f"cat /tmp/hosts.planted > {hosts_file}; rm -f /tmp/hosts.planted"
    )


def plant_wrong_entry(container: Container) -> None:
    """Make `/etc/hosts` map the Hive's name to an address nothing answers on.

    Args:
        container: The device. Its other lines (loopback, its own address) are kept as they were.
    """
    container.exec(["sh", "-c", planting_script()])


def hosts_text(container: Container) -> str:
    """Return the container's `/etc/hosts` as it is now."""
    return container.exec(["cat", HOSTS_FILE]).stdout


def address_of(container: Container, name: str = HIVE_HOSTNAME) -> str | None:
    """Return the address the container resolves `name` to, or None when it does not.

    Args:
        container: The device.
        name: The host name to look up; the Hive's by default.

    Returns:
        The first address `getent hosts` prints, which is what the Drone's own check sees.
    """
    shown = container.exec(["getent", "hosts", name], check=False)
    words = shown.stdout.split()
    return words[0] if shown.exit_code == 0 and words else None


async def sever(hive: Hive, node: SwarmNode) -> None:
    """Close the device's packet link and its Warden's link from the Hive Stand's end.

    Args:
        hive: The running Hive.
        node: The device, whose packet and Warden both dial the Hive Stand.
    """
    assert hive.swarm is not None
    await hive.swarm.listener.disconnect(node.device_id)
    link = hive.swarm.gate.wardens.link_of(node.warden_id)
    assert link is not None, "the device's Warden is not attached, so there is nothing to sever"
    await link.transport.close()


async def wait_for_address(
    container: Container, clock: Clock, wanted: str, timeout_s: float
) -> datetime:
    """Wait for the container to resolve the Hive's name to `wanted`; say when it first did.

    Args:
        container: The device.
        clock: The Hive's clock, for the moment the name came right.
        wanted: The address the name should come to resolve to.
        timeout_s: The longest to wait, in real seconds.

    Returns:
        The moment of the first lookup that returned `wanted`: when the fix had landed, to within
        `POLL_S`.

    Raises:
        AssertionError: The name did not come right in time, saying what it resolved to instead.
    """
    seen: str | None = None
    try:
        async with asyncio.timeout(timeout_s):  # A scenario's own bound; the Drone has its own.
            while True:
                seen = await asyncio.to_thread(address_of, container)
                if seen == wanted:
                    return clock.now()
                await asyncio.sleep(POLL_S)
    except TimeoutError as expired:
        message = f"The name did not resolve to {wanted} in {timeout_s:.0f} s; it gives {seen}."
        raise AssertionError(message) from expired
