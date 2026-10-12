"""Build the Quarantine Comb's default gate sequence: the six gates, in `GATE_ORDER`.

The Comb takes its gates as a tuple so a test can drop or add one, but production runs exactly
the six ADR-0047 names, in the order the report format requires. Keeping the sequence in one small
module (rather than in the package face) leaves `gates/__init__` as re-exports only and gives the
order a single place to be read and tested against `GATE_ORDER`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Called by `QuarantineComb` for its default and by composition code. Calls into the six gate
    modules beside it.

Key invariants:
    - The returned tuple has one gate per name in `GATE_ORDER`, in that order.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the order and the reasons.
    - hivemind.royal_jelly.spec.report for GATE_ORDER.
"""

from __future__ import annotations

from hivemind.royal_jelly.quarantine_comb.gates.base import Gate
from hivemind.royal_jelly.quarantine_comb.gates.capabilities import CapabilitiesGate
from hivemind.royal_jelly.quarantine_comb.gates.fuzz import FuzzGate
from hivemind.royal_jelly.quarantine_comb.gates.resources import ResourcesGate
from hivemind.royal_jelly.quarantine_comb.gates.schema import SchemaGate
from hivemind.royal_jelly.quarantine_comb.gates.static import StaticGate
from hivemind.royal_jelly.quarantine_comb.gates.tests import TestsGate

__all__ = ["default_gates"]


def default_gates() -> tuple[Gate, ...]:
    """Build the six gates in `GATE_ORDER`.

    Returns:
        Static, tests, schema, fuzz, resources and capabilities, in that order.
    """
    return (
        StaticGate(),
        TestsGate(),
        SchemaGate(),
        FuzzGate(),
        ResourcesGate(),
        CapabilitiesGate(),
    )
