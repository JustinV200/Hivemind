"""Test default_gates: six gates, one per GATE_ORDER name, in order.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/gates/defaults.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/gates/defaults.py for the module under test.
"""

from __future__ import annotations

from hivemind.royal_jelly.quarantine_comb import default_gates
from hivemind.royal_jelly.spec import GATE_ORDER


def test_default_gates_follow_gate_order_exactly() -> None:
    assert tuple(gate.name for gate in default_gates()) == GATE_ORDER


def test_only_the_static_gate_is_independent_of_the_runner() -> None:
    assert [gate.name for gate in default_gates() if not gate.uses_runner] == ["static"]
