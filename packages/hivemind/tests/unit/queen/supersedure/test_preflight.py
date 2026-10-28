"""Test the Supersedure candidate test: every check reported, and each failure named.

Codingrules 8.16 lists what a new Hive Stand must be: an enrolled, online, colonized (or Nuc)
node with FULL access, the same runtime version, disk for the stores plus headroom, memory for the
Royal Reserve, the Hive's secrets under the same identity key, and a clock within bound. A probe
that cannot say whether every node can reach it leaves that check UNVERIFIED, not failed.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests hivemind.queen.supersedure.preflight.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0065-supersedure-moving-the-hive-stand.md.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity, make_host_capacity
from builders.swarm.parties import make_swarm_node

from hivemind.cell import AccessLevel
from hivemind.forage import RoyalReserve
from hivemind.queen.supersedure import (
    CheckOutcome,
    HiveNeeds,
    ProbeReport,
    check_candidate,
    facts_for,
)
from hivemind.swarm import NodeStatus
from waggle.messages.swarm import RuntimeLevel

_KEY = "ab" * 32
_NEEDS = HiveNeeds(
    runtime_version="1.0.0",
    stores_bytes=1_000_000,
    secret_names=("hive.ed25519", "console.ed25519"),
    identity_key=_KEY,
    reserve=RoyalReserve(),
)
_PROBE = ProbeReport(secret_names=_NEEDS.secret_names, identity_key=_KEY, clock_skew_s=0.4)
_GOOD = {"level": RuntimeLevel.COLONIZED, "access_level": AccessLevel.FULL}


def _host(**changes: int) -> object:
    """A capacity report whose host figures differ from the default by `changes`."""
    return make_capacity(host=make_host_capacity(**changes))


def _outcomes(node: dict[str, object] | None, probe: ProbeReport | None) -> dict[str, str]:
    """Run the test on a node built with `node`'s overrides (None: not enrolled)."""
    built = make_swarm_node(None, None, **node) if node is not None else None
    report = check_candidate(facts_for("node_x", built, probe), _NEEDS)
    return {check.name: check.outcome.value for check in report.checks}


def test_a_good_candidate_passes_every_check_but_reachability_which_it_cannot_prove() -> None:
    report = check_candidate(facts_for("n", make_swarm_node(None, None, **_GOOD), _PROBE), _NEEDS)

    assert report.passed
    unverified = [c.name for c in report.checks if c.outcome is CheckOutcome.UNVERIFIED]
    assert unverified == ["reachable"]


@pytest.mark.parametrize(
    ("node", "probe", "failed"),
    [
        (None, _PROBE, "enrolled"),
        ({**_GOOD, "status": NodeStatus.UNREACHABLE}, _PROBE, "enrolled"),
        ({**_GOOD, "level": RuntimeLevel.GATEWAY}, _PROBE, "colonized"),
        ({**_GOOD, "access_level": AccessLevel.SCRATCH}, _PROBE, "full_access"),
        ({**_GOOD, "runtime_version": "0.9.0"}, _PROBE, "runtime_version"),
        ({**_GOOD, "capacity": _host(disk_free_bytes=1_000_000)}, _PROBE, "disk"),
        ({**_GOOD, "capacity": _host(memory_free_bytes=0)}, _PROBE, "royal_reserve"),
        (_GOOD, ProbeReport(_NEEDS.secret_names[:1], _KEY, 0.0), "secrets"),
        (_GOOD, ProbeReport(_NEEDS.secret_names, "cd" * 32, 0.0), "secrets"),
        (_GOOD, ProbeReport(_NEEDS.secret_names, _KEY, 30.0), "clock_skew"),
        (_GOOD, None, "clock_skew"),
        (_GOOD, ProbeReport(_NEEDS.secret_names, _KEY, 0.0, reachable=False), "reachable"),
    ],
)
def test_each_unmet_need_fails_its_own_check(
    node: dict[str, object] | None, probe: ProbeReport | None, failed: str
) -> None:
    outcomes = _outcomes(node, probe)

    assert outcomes[failed] == CheckOutcome.FAILED.value


def test_a_different_identity_key_is_named_as_the_cause() -> None:
    probe = ProbeReport(_NEEDS.secret_names, "cd" * 32, 0.0)
    report = check_candidate(facts_for("n", make_swarm_node(None, None, **_GOOD), probe), _NEEDS)

    [secrets] = [check for check in report.checks if check.name == "secrets"]
    assert secrets.outcome is CheckOutcome.FAILED
    assert "same identity key" not in secrets.detail and "hive.ed25519" in secrets.detail
