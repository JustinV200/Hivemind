"""Look into a device container and say whether it was left as found: processes and files.

A Real Cell is borrowed and left exactly as found, and "exactly" is something to measure, not to
believe. This module takes the measurements the phase 11 scenarios compare before and after: the
process table (`Container.processes`, polled until it settles, because a killed process leaves the
table a moment after the kill), a directory's files, and what the container's writable layer holds
(`docker diff`, the complete answer to "what was written here", from which `changes_outside`
names whatever is not the packet's own directory). Every call blocks for one docker command, so a
scenario that runs a Hive on its loop calls them through `asyncio.to_thread`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the phase 11 end-to-end
    scenarios; builds on `e2e.swarm.container`.

Key invariants:
    - A snapshot of an untouched container is equal to another of it: the probe that takes one is
      left out of what it measures.
    - File snapshots compare names, kinds, modes, sizes and the modification times of regular
      files, never of directories, since adding a file to a directory touches the directory.
    - Waits are bounded polls of the container, never bare sleeps.

See Also:
    - e2e/swarm/container.py for `Container.processes` and `Container.file_tree`.
    - tests/integration/test_pollen_in_container.py for the same checks on a leased session.
"""

from __future__ import annotations

import asyncio
import time
from typing import NamedTuple

from e2e.swarm.container import Container, FileEntry, docker

SETTLE_S = 10.0  # How long a killed program may take to leave the container's process table.
POLL_S = 0.25  # How often a settling process table is looked at.
# What `docker run --init` adds to a container: the janitor's doing, not a program's.
INIT_BINARY = "/usr/sbin/docker-init"
DEVICE_TMP = "/tmp"  # noqa: S108  # SAFETY: a path inside the device, never one on this host.
DEVICE_VAR_TMP = "/var/tmp"  # noqa: S108  # SAFETY: as above.
# Every directory of the device that belongs to its operator. The packet's data directory is under
# /var/lib, outside the list; its install, a virtualenv under /opt, is left out by name.
OPERATOR_DIRS = ("/home", "/root", "/etc", "/opt", "/srv", DEVICE_TMP, "/usr/local", DEVICE_VAR_TMP)
PACKET_INSTALL = "/opt/pollen"

__all__ = [
    "DEVICE_TMP",
    "DEVICE_VAR_TMP",
    "INIT_BINARY",
    "OPERATOR_DIRS",
    "PACKET_INSTALL",
    "SETTLE_S",
    "WrittenFile",
    "changed_paths",
    "changes_outside",
    "files_under",
    "operator_files",
    "process_args",
    "settled_to",
    "unexplained",
    "written_files",
]


class WrittenFile(NamedTuple):
    """A regular file as a snapshot sees it: its size and when it was last written."""

    size: int
    mtime_ns: int


async def settled_to(
    container: Container, expected: dict[int, str], timeout_s: float = SETTLE_S
) -> dict[int, str]:
    """Poll the container's process table until it equals `expected`, or `timeout_s` has passed.

    Args:
        container: The device.
        expected: The table to wait for, usually a snapshot taken before the lease.
        timeout_s: Real seconds to wait.

    Returns:
        The last table read: `expected` if it settled, else what was still there.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        table = await asyncio.to_thread(container.processes)
        if table == expected or time.monotonic() > deadline:
            return table
        await asyncio.sleep(POLL_S)


def process_args(container: Container) -> dict[int, str]:
    """Snapshot the process table with each process's full command line, for finding one by it."""
    listing = container.exec(["ps", "-eo", "pid=,args="]).stdout
    table: dict[int, str] = {}
    for row in listing.splitlines():
        pid, _, args = row.strip().partition(" ")
        table[int(pid)] = args.strip()
    return table


def files_under(container: Container, path: str) -> dict[str, FileEntry]:
    """Snapshot a directory, or nothing if it is not there.

    The packet makes its `leases` directory when the first lease opens, so before that there is
    none; no directory and an empty one both mean the lease left nothing behind.
    """
    return container.file_tree(path) if container.exists(path) else {}


def written_files(container: Container, *roots: str) -> dict[str, WrittenFile | str]:
    """Snapshot everything under `roots`: each regular file's size and mtime, other entries' kinds.

    Unlike `Container.file_tree`, a regular file carries its modification time, so a file rewritten
    in place to the same length is a difference. A directory carries only that it is one, since
    its own time moves whenever an entry in it is added or removed.

    Args:
        container: The device.
        *roots: Directories in it; one that does not exist contributes nothing.

    Returns:
        Absolute path to a `WrittenFile`, or to the `find` type letter of anything else.
    """
    printf = "%y %s %T@ %p\\n"
    listing = container.exec(["find", *roots, "-mindepth", "1", "-printf", printf], check=False)
    tree: dict[str, WrittenFile | str] = {}
    for row in listing.stdout.splitlines():
        kind, size, mtime, path = row.split(" ", 3)
        tree[path] = WrittenFile(int(size), int(float(mtime) * 1e9)) if kind == "f" else kind
    return tree


def operator_files(container: Container) -> dict[str, WrittenFile | str]:
    """Snapshot the directories the device's operator owns, less the packet's own install.

    Args:
        container: The device.

    Returns:
        What `written_files` reports for `OPERATOR_DIRS`, without anything under `PACKET_INSTALL`.
    """
    tree = written_files(container, *OPERATOR_DIRS)
    return {
        path: entry
        for path, entry in tree.items()
        if path != PACKET_INSTALL and not path.startswith(f"{PACKET_INSTALL}/")
    }


def changed_paths(container: Container) -> list[str]:
    """List every path the container's writable layer holds, as `docker diff` prints them.

    The layer starts empty on every container, so this is the complete list of what has been
    added, changed or deleted on the device since it was started: the init process Docker adds
    (`--init`) and whatever the packet and the Hive did.

    Args:
        container: The device.

    Returns:
        One entry per path, a letter (`A` added, `C` changed, `D` deleted) and the path.
    """
    return docker("diff", container.name).stdout.splitlines()


def unexplained(entries: list[str], *allowed: str) -> list[str]:
    """Pick out the `docker diff` entries that are not under the `allowed` directories.

    The init binary the janitor adds is explained, and so is a directory that only changed
    because something under an allowed path was added to it (`/var` and `/var/lib`, for an
    allowed `/var/lib/pollen`). Anything else is something some program wrote on the device.

    Args:
        entries: Lines of `docker diff`: a letter and a path.
        *allowed: Directories whose contents are expected to change, such as the packet's own.

    Returns:
        The entries that nothing explains.
    """
    roots = (*allowed, INIT_BINARY)
    left: list[str] = []
    for entry in entries:
        letter, _, path = entry.partition(" ")
        under = any(path == root or path.startswith(f"{root}/") for root in roots)
        parent = letter == "C" and any(root.startswith(f"{path}/") for root in roots)
        if not (under or parent):
            left.append(entry)
    return left


def changes_outside(container: Container, *allowed: str) -> list[str]:
    """List what the container's writable layer holds that is not under the `allowed` directories.

    Args:
        container: The device.
        *allowed: Directories whose contents are expected to change, such as the packet's own.

    Returns:
        The `docker diff` entries that nothing explains (see `unexplained`); empty when the
        device holds no writes but the allowed ones.
    """
    return unexplained(changed_paths(container), *allowed)
