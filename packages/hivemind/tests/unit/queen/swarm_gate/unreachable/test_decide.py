"""Tests for hivemind.queen.swarm_gate.unreachable.decide: the pure rules of the hold.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/unreachable/decide.py (codingrules section 3). No clock,
    no Queen, no registry: the table of verdicts and the edges of the two limits, row by row.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.unreachable.decide for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.queen.swarm_gate.unreachable import Verdict, grace_over, offline_over, verdict_for
from hivemind.swarm import RuntimeLevel


@pytest.mark.parametrize(
    ("level", "verdict"),
    [
        (RuntimeLevel.GATEWAY, Verdict.REPLACE),  # Its hands are cut off from a Warden here.
        (RuntimeLevel.COLONIZED, Verdict.REPLACE),  # Its Warden has no model and has clustered.
        (RuntimeLevel.NUC, Verdict.WAIT),  # Its model server keeps its sub-bees working.
    ],
)
def test_each_rung_has_its_verdict(level: RuntimeLevel, verdict: Verdict) -> None:
    assert verdict_for(level) is verdict


def test_every_rung_of_the_ladder_has_a_verdict() -> None:
    for level in RuntimeLevel:
        assert isinstance(verdict_for(level), Verdict)


@pytest.mark.parametrize(
    ("held_s", "grace_s", "over"),
    [
        (0.0, 30.0, False),
        (29.9, 30.0, False),
        (30.0, 30.0, True),  # A limit is reached at its own value, not after it.
        (31.0, 30.0, True),
        (0.0, 0.0, True),  # A grace of 0 decides at once.
    ],
)
def test_the_grace_ends_at_its_own_value(held_s: float, grace_s: float, over: bool) -> None:
    assert grace_over(held_s, grace_s) is over


@pytest.mark.parametrize(
    ("held_s", "limit_s", "over"),
    [
        (299.9, 300.0, False),
        (300.0, 300.0, True),
        (301.0, 300.0, True),
    ],
)
def test_the_offline_limit_ends_at_its_own_value(held_s: float, limit_s: float, over: bool) -> None:
    assert offline_over(held_s, limit_s) is over
