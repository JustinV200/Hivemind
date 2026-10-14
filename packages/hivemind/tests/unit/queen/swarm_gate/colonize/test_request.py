"""Tests for hivemind.queen.swarm_gate.colonize.request: what a pending rung asks of the Queen.

The rule is pure, so every case is a row: a node, the rung the operator wants, and the one step
or the one word the Queen answers with. No Queen, registry or clock is built.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/request.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.request for the module under test.
    - tests.unit.swarm.test_level_decide for the colonization decision it defers to.
"""

from __future__ import annotations

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity, make_footprint
from builders.swarm import gate_terms, make_swarm_node

from hivemind.cell import AccessLevel
from hivemind.queen.swarm_gate import decide_request
from hivemind.queen.swarm_gate.colonize import Ask, Step, next_rung
from hivemind.queen.swarm_gate.colonize.request import BUSY_WORD, NOT_ASKED_WORD, NUC_RUNG_WORD
from hivemind.swarm import RuntimeLevel, SwarmNode

G, C, N = RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED, RuntimeLevel.NUC


def _ask(node: SwarmNode, *, is_idle: bool = True) -> Ask:
    """What the Queen answers for `node`, with the test's usual terms and footprint."""
    return decide_request(node, gate_terms(), make_footprint(), is_idle=is_idle)


@pytest.mark.parametrize(
    ("level", "wanted", "next_one"),
    [
        (G, C, C),
        (G, N, C),  # One rung at a time: the Warden always moves first.
        (C, N, N),
        (C, G, G),
        (N, C, C),
        (N, G, C),
        (G, G, G),  # Already there.
    ],
)
def test_the_next_rung_is_one_step_toward_the_one_wanted(
    level: RuntimeLevel, wanted: RuntimeLevel, next_one: RuntimeLevel
) -> None:
    assert next_rung(level, wanted) is next_one


def test_a_node_that_asks_for_nothing_is_not_asked_anything() -> None:
    node = make_swarm_node(level=G, desired_level=None)

    assert _ask(node) == Ask(None, NOT_ASKED_WORD)


@pytest.mark.parametrize("wanted", [C, N])
def test_a_gateway_device_that_can_carry_a_warden_is_promoted_for_the_operator(
    wanted: RuntimeLevel,
) -> None:
    node = make_swarm_node(level=G, desired_level=wanted, access_level=AccessLevel.FULL)

    assert _ask(node) == Ask(Step.PROMOTE, "operator")  # NUC at GATEWAY is the same first step.


@pytest.mark.parametrize(
    ("node", "word"),
    [
        (
            make_swarm_node(
                level=G,
                desired_level=C,
                access_level=AccessLevel.FULL,
                capabilities=make_capabilities(python_version="3.11.9"),
            ),
            "python_too_old",
        ),
        (
            make_swarm_node(
                level=G,
                desired_level=C,
                access_level=AccessLevel.FULL,
                capabilities=make_capabilities(python_version=None),
            ),
            "python_unknown",
        ),
        (
            make_swarm_node(level=G, desired_level=C, access_level=AccessLevel.READ_ONLY),
            "access_too_low",
        ),
        (
            make_swarm_node(
                level=G,
                desired_level=C,
                access_level=AccessLevel.FULL,
                capacity=make_capacity(max_sub_bees=0),
            ),
            "forage_too_small",
        ),
    ],
)
def test_a_gateway_device_that_cannot_carry_a_warden_is_refused_with_one_word(
    node: SwarmNode, word: str
) -> None:
    assert _ask(node) == Ask(None, word)


def test_an_idle_colonized_warden_is_withdrawn_when_the_operator_asks() -> None:
    node = make_swarm_node(level=C, desired_level=G)

    assert _ask(node, is_idle=True) == Ask(Step.WITHDRAW, "operator")


def test_a_busy_colonized_warden_is_refused_a_withdrawal() -> None:
    node = make_swarm_node(level=C, desired_level=G)

    assert _ask(node, is_idle=False) == Ask(None, BUSY_WORD)


@pytest.mark.parametrize(("level", "wanted"), [(C, N), (N, C), (N, G)])
def test_a_rung_that_touches_a_nuc_is_refused_until_the_nuc_promotion_exists(
    level: RuntimeLevel, wanted: RuntimeLevel
) -> None:
    node = make_swarm_node(level=level, desired_level=wanted)

    assert _ask(node) == Ask(None, NUC_RUNG_WORD)
