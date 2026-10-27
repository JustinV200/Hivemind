"""Define the read-only diagnostics a diagnostic Drone may run below FULL, as fixed data.

ADR-0060 lets a command run below FULL only on a Cell that contains its processes, so a borrowed
device at SCRATCH runs none. ADR-0056's diagnostic Drone, which a cut-off Warden sends to find out
why its link went, still needs to look at the machine it runs on. This module is the one exception:
a short, fixed list of commands (`DIAGNOSTIC_COMMANDS`) that only read local state (the routing
table, the interfaces, the sockets, the resolver's files, the clock and the disks). None writes,
none reaches the network, none goes through a shell, and each is matched by its whole argument list
exactly, so no argument the model chooses can change what it does.

Such a command is asked for as one capability, `exec:@diagnostic` (`DIAGNOSTIC_EXEC`), never as
`exec:<program>`. A program name never starts with `@` (`hivemind.workers.tools.session.
run_command` refuses one that does), so no other command can be asked for under it. The access
level admits it from SCRATCH up, and only for the Drone role (`admits_diagnostic`); the Drone's
`run_command` asks for it only when its task's goal ceiling names it exactly. An `exec:@...`
capability is reserved: every set someone outside the Hive submits or grants (a device approval, a
steward's grant, a goal's capability set) is parsed through `parse_submitted`, which refuses it.
So the only ceiling that can hold it is the one a cut-off Warden builds for its own diagnostic task
(`hivemind.wardens.offline.diagnose.assign.CEILING`), which never comes from outside. Every other
command still needs `exec:<program>`, and so FULL or a contained Cell.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by `hivemind.guard.access` (the
    ceiling and the access-level rule), `hivemind.workers.tools.session` and
    `hivemind.wardens.offline.diagnose`. Calls into `hivemind.cell.tiers` and
    `hivemind.guard.capabilities` only.

Key invariants:
    - The list is data: nothing adds to it at run time, and an argument list matches only when it
      equals an entry exactly.
    - No entry writes, opens a connection, or names a shell or a shell metacharacter.
    - Below FULL, `exec:@diagnostic` is admitted only to the Drone role, and never on READ_ONLY.
    - No submitted or granted set ever holds an `exec:@...` entry (`parse_submitted`).

See Also:
    - docs/adr/0060-commands-run-only-where-they-are-contained-or-fully-granted.md, its amendment
      "the read-only diagnostics", for the decision.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for the Drone that runs them.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.cell.tiers import AccessLevel
from hivemind.guard.capabilities import Capability, CapabilityFamily, CapabilitySet
from hivemind.guard.errors import InvalidCapabilityError

DIAGNOSTIC_SCOPE = "@diagnostic"  # Never a program name: run_command refuses argv[0] with an @.
DIAGNOSTIC_EXEC = Capability(family=CapabilityFamily.EXEC, scope=DIAGNOSTIC_SCOPE)
DIAGNOSTIC_ROLE = "drone"  # The [guard.roles] row of the only role it is admitted for.
# Each entry is a whole argument list, matched exactly. Read-only and local: no write, no network,
# no shell. POSIX first, then their Windows counterparts.
DIAGNOSTIC_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("ip", "address", "show"),
        ("ip", "link", "show"),
        ("ip", "route", "show"),
        ("ip", "-6", "route", "show"),
        ("ip", "neighbour", "show"),
        ("ss", "-tan"),
        ("ss", "-uan"),
        ("cat", "/etc/resolv.conf"),
        ("cat", "/etc/hosts"),
        ("hostname",),
        ("uname", "-a"),
        ("uptime",),
        ("date", "-u"),
        ("df", "-h"),
        ("free", "-m"),
        ("ipconfig", "/all"),
        ("route", "print"),
        ("netstat", "-an"),
    }
)

__all__ = [
    "DIAGNOSTIC_COMMANDS",
    "DIAGNOSTIC_EXEC",
    "DIAGNOSTIC_ROLE",
    "DIAGNOSTIC_SCOPE",
    "admits_diagnostic",
    "is_diagnostic",
    "is_reserved",
    "parse_submitted",
]


def is_diagnostic(argv: Sequence[str]) -> bool:
    """Say whether `argv` is one of the read-only diagnostics, exactly as listed.

    Args:
        argv: The whole argument list a command would run.

    Returns:
        True only when it equals an entry of `DIAGNOSTIC_COMMANDS`, argument for argument.
    """
    return tuple(argv) in DIAGNOSTIC_COMMANDS


def admits_diagnostic(level: AccessLevel, role: str) -> bool:
    """Decide whether the access level admits `exec:@diagnostic` for a principal in `role`.

    Args:
        level: The Cell's access level.
        role: The principal's policy role (`PrincipalRef.role`).

    Returns:
        True for the Drone role from SCRATCH up (FULL runs any command anyway); False for every
        other role, and on READ_ONLY, where nothing runs.
    """
    return role == DIAGNOSTIC_ROLE and level.rank >= AccessLevel.SCRATCH.rank


def is_reserved(capability: Capability) -> bool:
    """Say whether `capability` is in the `exec:@...` scope only the Hive's own task may hold."""
    return capability.family is CapabilityFamily.EXEC and capability.scope.startswith("@")


def parse_submitted(*strings: str) -> CapabilitySet:
    """Parse a capability set submitted or granted from outside the Hive, refusing a reserved one.

    Args:
        *strings: The capability strings a device approval, a steward or a goal's submitter named.

    Returns:
        The parsed set, once no entry is in the reserved `exec:@...` scope.

    Raises:
        InvalidCapabilityError: A string does not parse, or names a reserved scope; the error names
            the first such entry.
    """
    parsed = CapabilitySet.parse(*strings)
    for capability in sorted(parsed, key=str):
        if is_reserved(capability):
            raise InvalidCapabilityError(
                str(capability), "an exec:@ scope is reserved for the Hive's own diagnostic task"
            )
    return parsed
