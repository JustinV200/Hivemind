"""Provide the Quarantine Comb: the sandbox and six gates every new tool must pass.

The Quarantine Comb is the proof the Comb Registry (the catalogue of promoted tools) requires
before it will promote a tool a model scaffolded. A package is put into a sandbox (a Virtual Cell
reached through its session, or a temporary directory in development), a runner inside the sandbox
measures it, and six gates judge the result in order: static, tests, schema, fuzz, resources,
capabilities. The first failure ends the run and the `QuarantineReport` names it. The Comb records
nothing on the trail; its caller does. `decide_path` chooses between the sandbox route and the
real-cell route for a target platform.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the royal_jelly package.
    Driven by the Warden of the sandbox Cell (ADR-0047) and by `hive tools quarantine`; calls into
    `hivemind.cell` (sessions), `hivemind.guard` (capability matching), `hivemind.llm` (argument
    validation, inside the runner) and `hivemind.royal_jelly.spec`.

Key invariants:
    - A tool reaches a passing report only through all six gates, in `GATE_ORDER`.
    - A Real Cell is never a sandbox: `SandboxCell` refuses a borrowed Cell.
    - `sandbox_subprocess` is development only and the one module here that starts a process.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the decisions.
    - .claude/roadmap.md steps 9.3a, 9.4 and 9.5.

Public API:
    - QuarantineComb: `run(package, spec, sandbox, limits, path) -> QuarantineReport` (comb).
    - Sandbox, SandboxCell, SandboxSubprocess, SandboxRefusedError: where the Comb runs.
    - decide_path: the sandbox route or the real-cell route for a target platform (path).
    - default_gates, Gate, GateContext: the six checks and what they read (gates).
    - BANNED_MODULES, BANNED_NAMES: what a tool may not import or call (gates.banned).
"""

from hivemind.royal_jelly.quarantine_comb.comb import QuarantineComb, RunnerCall
from hivemind.royal_jelly.quarantine_comb.errors import SandboxPathError, SandboxRefusedError
from hivemind.royal_jelly.quarantine_comb.gates import (
    BANNED_MODULES,
    BANNED_NAMES,
    Gate,
    GateContext,
    default_gates,
)
from hivemind.royal_jelly.quarantine_comb.path import decide_path
from hivemind.royal_jelly.quarantine_comb.sandbox import Sandbox
from hivemind.royal_jelly.quarantine_comb.sandbox_cell import SandboxCell
from hivemind.royal_jelly.quarantine_comb.sandbox_subprocess import SandboxSubprocess

__all__ = [
    "BANNED_MODULES",
    "BANNED_NAMES",
    "Gate",
    "GateContext",
    "QuarantineComb",
    "RunnerCall",
    "Sandbox",
    "SandboxCell",
    "SandboxPathError",
    "SandboxRefusedError",
    "SandboxSubprocess",
    "decide_path",
    "default_gates",
]
