"""Define HiveState: the Hive's own state paths and addresses, which no bee may touch or reach.

ADR-0033, "Bees never touch the Hive's own state": every bee principal (a Warden or a Worker) is
refused `fs:read` and `fs:write` on the Hive's state paths, `exec` of the Hive's own entry points
and `net` to loopback or to the Hive Stand's own addresses, whatever its role set says (ADR-0031's
floors). The entry points and the loopback forms are fixed; the paths and the addresses depend on
the manifest and the machine, so a composition root states them once in a `HiveState` and the
`GuardPolicy` carries it to every floor decision. Inside a Virtual Cell the Hive Stand is also a
name (its host-gateway alias, or its onion service from a Night Veil Cell, which is never looked
up there), so the names a Cell reaches it by are state too, refused before any lookup. The files
are the `[hive] db` SQLite file with its `-wal`, `-shm` and `-journal` siblings (a read of the WAL
is a read of the database), and the manifest itself; the directories are the `[hive] secrets_dir`
secret store, everything under it included. Every path is kept in the one spelling the floor
compares (`comparable_path`): POSIX separators, `..` and `.` collapsed, and case folded, so a path
differing only in case or in its separators is still refused (refusing a harmless look-alike costs
nothing; missing the database does).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy`. Built
    by the composition roots (`hivemind.cli.compose.guard` from the manifest,
    `hivemind.cli.in_cell.deps` for a Virtual Cell); carried on `GuardPolicy.hive_state`; read by
    `hivemind.guard.policy.floors.hive_state` and, for the addresses, by
    `hivemind.workers.tools.http`. Calls into `hivemind.guard.net` (address types) and the
    standard library.

Key invariants:
    - Frozen: the state paths and addresses are fixed when the Hive starts.
    - Every stored path is already in `comparable_path` form, and every address is plain
      (`hivemind.guard.net.plain_address`), so the floor compares without normalising again.
    - `HiveState()` (nothing named) still leaves the fixed parts of the floor in force: the entry
      points and every loopback form are refused with no data at all.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the decision.
    - hivemind.guard.policy.floors.hive_state for the floor that reads this.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePath

from hivemind.guard.net import IPAddress, normalise_host, plain_address

# The SQLite files that hold the database's content beside the file itself: WAL and its shared
# memory index in WAL mode, the rollback journal otherwise. Each is the database, for a reader.
SQLITE_SIBLING_SUFFIXES = ("-wal", "-shm", "-journal")

__all__ = ["SQLITE_SIBLING_SUFFIXES", "HiveState", "comparable_path"]


def comparable_path(path: str | PurePath) -> str:
    """Return the one spelling the Hive-state floor compares paths in.

    Args:
        path: A filesystem path or a glob scope, POSIX or Windows style.

    Returns:
        The path with backslashes turned to slashes, `.` and `..` collapsed (lexically, never
        through the filesystem) and case folded; a trailing slash is dropped.
    """
    text = path.as_posix() if isinstance(path, PurePath) else path.replace("\\", "/")
    # normpath keeps a leading "//" (POSIX leaves it implementation-defined); one slash is enough.
    normalised = posixpath.normpath(text) if text else text
    if normalised.startswith("//"):
        normalised = "/" + normalised.lstrip("/")
    return normalised.casefold()


@dataclass(frozen=True, slots=True)
class HiveState:
    """The Hive's own state on this machine: files, directories and addresses no bee may reach.

    Attributes:
        files: Every state file in `comparable_path` form: the database, its SQLite siblings and
            the manifest.
        directories: Every state directory in `comparable_path` form: the secret store; every
            path at or under one is state too.
        own_addresses: The Hive Stand's own addresses, plain: every address a bee could reach it
            at that is not already a loopback form.
        own_host_names: The names a bee's Cell reaches the Hive Stand by, normalised (a
            host-gateway alias, or the Hive Stand's onion service from a Night Veil Cell, which
            is never resolved there): refused by name, before any lookup.
    """

    files: frozenset[str] = frozenset()
    directories: frozenset[str] = frozenset()
    own_addresses: frozenset[IPAddress] = frozenset()
    own_host_names: frozenset[str] = frozenset()

    @classmethod
    def of(
        cls,
        *,
        db: PurePath | None = None,
        secrets_dir: PurePath | None = None,
        manifest: PurePath | None = None,
        own_addresses: Iterable[IPAddress] = (),
        own_host_names: Iterable[str] = (),
    ) -> HiveState:
        """Build the state a composition root knows, from already-resolved paths.

        Args:
            db: The resolved `[hive] db` file; its SQLite siblings are added with it.
            secrets_dir: The resolved `[hive] secrets_dir`; everything under it is state.
            manifest: The manifest file the Hive was loaded from, when there is one.
            own_addresses: Every address the Hive Stand answers on.
            own_host_names: Every name the Hive Stand is reached by from where the bees run.

        Returns:
            A HiveState with every path in `comparable_path` form and every address plain.

        Example:
            >>> HiveState.of(db=PurePosixPath("/h/hive.db")).files  # doctest: +SKIP
            frozenset({'/h/hive.db', '/h/hive.db-wal', '/h/hive.db-shm', '/h/hive.db-journal'})
        """
        files: set[str] = set()
        # The database is its file plus whichever siblings SQLite keeps its pages in.
        if db is not None:
            files.add(comparable_path(db))
            files.update(
                comparable_path(f"{db.as_posix()}{end}") for end in SQLITE_SIBLING_SUFFIXES
            )
        if manifest is not None:
            files.add(comparable_path(manifest))
        directories = {comparable_path(secrets_dir)} if secrets_dir is not None else set()
        return cls(
            files=frozenset(files),
            directories=frozenset(directories),
            own_addresses=frozenset(plain_address(address) for address in own_addresses),
            own_host_names=frozenset(normalise_host(name) for name in own_host_names),
        )
