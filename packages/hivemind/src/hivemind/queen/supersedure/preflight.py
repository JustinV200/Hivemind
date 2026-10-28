"""Test a Supersedure candidate: may the Hive Stand move onto this node (codingrules 8.16)?

The candidate must be an enrolled, online Swarm node that is colonized (Level 1) or a Nuc, with
`FULL` access, the same runtime version, free disk for the stores plus a quarter again, Forage that
covers the Royal Reserve, the Hive's secrets already in its own secret store (the same
`hive.ed25519`), and a clock within `MAX_CLOCK_SKEW_S` of the Hive Stand's. Whether every Swarm node
and Virtual Cell backend can reach its address is reported too, but nothing on the Hive Stand can
prove it, so a probe that cannot tell leaves it `UNVERIFIED` for the operator rather than failing.
`check_candidate` is pure: the door gathers the facts (the swarm registry's node, the candidate's
own probe) and this decides, reporting every check, passed or not, so `hive doctor --supersedure`
shows them all at once.

Fits into the Hive:
    Layer 6 (the kernel), inside `hivemind.queen.supersedure`. Called by `.door` (the preflight
    the human reads, and the gate in front of `supersede`). Calls into `hivemind.forage`,
    `hivemind.swarm` and waggle for types only.

Key invariants:
    - Pure: no I/O, no clock; the same facts always give the same report.
    - A fact the probe could not read fails its check, except reachability (`UNVERIFIED`).

See Also:
    - docs/adr/0065-supersedure-moving-the-hive-stand.md, "The candidate test".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hivemind.cell import AccessLevel
from hivemind.forage import RoyalReserve
from hivemind.swarm import NodeStatus, SwarmNode
from waggle.messages.swarm import RuntimeLevel

HEADROOM_FRACTION = 0.25  # Free disk must cover the stores plus a quarter again.
MAX_CLOCK_SKEW_S = 5.0  # Grace deadlines and trail instants must agree across the two machines.
_COLONIZED = (RuntimeLevel.COLONIZED, RuntimeLevel.NUC)

__all__ = [
    "HEADROOM_FRACTION",
    "MAX_CLOCK_SKEW_S",
    "CandidateFacts",
    "CheckOutcome",
    "HiveNeeds",
    "PreflightCheck",
    "PreflightReport",
    "ProbeReport",
    "check_candidate",
    "facts_for",
]


class CheckOutcome(Enum):
    """How one preflight check came out."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    UNVERIFIED = "UNVERIFIED"  # Nothing here can prove it; the operator checks by hand.


@dataclass(frozen=True, slots=True)
class ProbeReport:
    """What the candidate says about itself when asked over its session (None: unanswered).

    Attributes:
        secret_names: The names in its secret store, never a value.
        identity_key: The public half of its `hive.ed25519`, hex; None when it has none.
        clock_skew_s: Its clock minus the Hive Stand's, in seconds.
        reachable: Whether every Swarm node and backend can reach it; None when untested.
    """

    secret_names: tuple[str, ...] | None = None
    identity_key: str | None = None
    clock_skew_s: float | None = None
    reachable: bool | None = None


@dataclass(frozen=True, slots=True)
class CandidateFacts:
    """Everything the candidate test reads about one node."""

    node_id: str
    node: SwarmNode | None  # The swarm registry's record; None when no such node is enrolled.
    probe: ProbeReport | None  # None when the candidate could not be asked at all.


@dataclass(frozen=True, slots=True)
class HiveNeeds:
    """What the Hive being moved needs of its new Hive Stand."""

    runtime_version: str  # The Hive Stand's own runtime version.
    stores_bytes: int  # The database plus the Basket's blobs.
    secret_names: tuple[str, ...]  # Every secret the Hive's store holds, by name.
    identity_key: str | None  # The public half of the Hive's `hive.ed25519`.
    reserve: RoyalReserve  # `[forage] reserve`, held back on the Hive Stand for the Queen.


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """One check: its name, how it came out, and one line saying why."""

    name: str
    outcome: CheckOutcome
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Every check for one candidate, in a fixed order."""

    node_id: str
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        """Whether no check failed (an UNVERIFIED one does not block)."""
        return all(check.outcome is not CheckOutcome.FAILED for check in self.checks)


def facts_for(node_id: str, node: SwarmNode | None, probe: ProbeReport | None) -> CandidateFacts:
    """Bundle a candidate's facts; a thin constructor kept so callers never build half of one.

    Args:
        node_id: The node the human named.
        node: Its registry record, or None.
        probe: Its own answers, or None.

    Returns:
        The facts.
    """
    return CandidateFacts(node_id=node_id, node=node, probe=probe)


def check_candidate(facts: CandidateFacts, needs: HiveNeeds) -> PreflightReport:
    """Run every candidate check (module docstring).

    Args:
        facts: What is known about the candidate.
        needs: What the Hive being moved needs.

    Returns:
        The report: each check, PASSED, FAILED or UNVERIFIED, with a one-line reason.
    """
    node, probe = facts.node, facts.probe or ProbeReport()
    checks = [_enrolled(node)]
    if node is not None:
        checks.extend(_registry_checks(node, needs))
    checks.extend(_probe_checks(probe, needs))
    return PreflightReport(node_id=facts.node_id, checks=tuple(checks))


def _check(name: str, ok: bool, detail: str) -> PreflightCheck:
    """A PASSED or FAILED check."""
    return PreflightCheck(name, CheckOutcome.PASSED if ok else CheckOutcome.FAILED, detail)


def _enrolled(node: SwarmNode | None) -> PreflightCheck:
    """The node is enrolled and online right now."""
    if node is None:
        return _check("enrolled", False, "no Swarm node by that id")
    online = node.status is NodeStatus.ONLINE
    return _check("enrolled", online, f"status {node.status.value}")


def _registry_checks(node: SwarmNode, needs: HiveNeeds) -> list[PreflightCheck]:
    """What the swarm registry's record decides: level, access, version, disk, Forage."""
    host = node.capacity.host
    need_disk = int(needs.stores_bytes * (1 + HEADROOM_FRACTION))
    reserve = needs.reserve.memory_for(host.memory_bytes)
    return [
        _check("colonized", node.level in _COLONIZED, f"level {node.level.value}"),
        _check("full_access", node.access_level is AccessLevel.FULL, node.access_level.value),
        _check(
            "runtime_version",
            node.runtime_version == needs.runtime_version,
            f"{node.runtime_version} (Hive Stand {needs.runtime_version})",
        ),
        _check(
            "disk",
            host.disk_free_bytes >= need_disk,
            f"{host.disk_free_bytes} bytes free, {need_disk} needed",
        ),
        _check(
            "royal_reserve",
            host.memory_free_bytes >= reserve,
            f"{host.memory_free_bytes} bytes of memory free, {reserve} reserved for the Queen",
        ),
    ]


def _secrets_detail(missing: list[str] | None, same_key: bool) -> str:
    """Say what the `secrets` check found: unanswered, what is missing, or whose key it holds."""
    if missing is None:
        return "unanswered"
    if missing:
        return f"missing {', '.join(missing)}"
    return "every secret, same identity key" if same_key else "a different hive.ed25519 key"


def _probe_checks(probe: ProbeReport, needs: HiveNeeds) -> list[PreflightCheck]:
    """What only the candidate itself can say: its secrets, its key, its clock, its reach."""
    names = probe.secret_names
    missing = sorted(set(needs.secret_names) - set(names)) if names is not None else None
    same_key = probe.identity_key == needs.identity_key
    secrets = _check("secrets", missing == [] and same_key, _secrets_detail(missing, same_key))
    skew = probe.clock_skew_s
    clock = _check(
        "clock_skew",
        skew is not None and abs(skew) <= MAX_CLOCK_SKEW_S,
        "unanswered" if skew is None else f"{skew:+.1f} s (bound {MAX_CLOCK_SKEW_S} s)",
    )
    if probe.reachable is None:
        reach = PreflightCheck(
            "reachable", CheckOutcome.UNVERIFIED, "check every node can dial its address"
        )
    else:
        reach = _check("reachable", probe.reachable, "every node and backend can dial it")
    return [secrets, clock, reach]
