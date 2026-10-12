"""Hold the Quarantine Comb's six gates, in order, and the data they share.

A gate is one check a new tool must clear: `static` (read the source), `tests` (run the tool's own
tests), `schema` (the input schema works), `fuzz` (schema-derived inputs), `resources` (stay under
the limits) and `capabilities` (use only what the spec declares). The Comb runs them in that
order and stops at the first failure. `default_gates()` builds the sequence; the module for each
gate is its own file, and `banned` holds the lists the static gate applies.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Built by `QuarantineComb` (its default) and by composition code. Calls into `hivemind.guard`
    (capability matching) and `hivemind.royal_jelly.spec`.

Key invariants:
    - `default_gates()` returns exactly one gate per name in `GATE_ORDER`, in that order.
    - Only `static` is pure of the sandbox; the other five read the runner's report.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the order and the reasons.
    - hivemind.royal_jelly.spec.report for GATE_ORDER and GateResult.

Public API:
    - Gate, GateContext: the protocol and what a gate reads (base).
    - default_gates: the six gates in `GATE_ORDER`.
    - BANNED_MODULES, BANNED_NAMES: what a tool may not import or call (banned).
    - StaticGate, TestsGate, SchemaGate, FuzzGate, ResourcesGate, CapabilitiesGate: the gates.
"""

from hivemind.royal_jelly.quarantine_comb.gates.banned import BANNED_MODULES, BANNED_NAMES
from hivemind.royal_jelly.quarantine_comb.gates.base import Gate, GateContext
from hivemind.royal_jelly.quarantine_comb.gates.capabilities import CapabilitiesGate
from hivemind.royal_jelly.quarantine_comb.gates.defaults import default_gates
from hivemind.royal_jelly.quarantine_comb.gates.fuzz import FuzzGate
from hivemind.royal_jelly.quarantine_comb.gates.resources import ResourcesGate
from hivemind.royal_jelly.quarantine_comb.gates.schema import SchemaGate
from hivemind.royal_jelly.quarantine_comb.gates.static import StaticGate
from hivemind.royal_jelly.quarantine_comb.gates.tests import TestsGate

__all__ = [
    "BANNED_MODULES",
    "BANNED_NAMES",
    "CapabilitiesGate",
    "FuzzGate",
    "Gate",
    "GateContext",
    "ResourcesGate",
    "SchemaGate",
    "StaticGate",
    "TestsGate",
    "default_gates",
]
