"""Hold the in-sandbox runner: the process that measures a tool for the Quarantine Comb.

The runner is the part of the Comb that executes inside the sandbox, next to the untrusted tool.
It runs the tool's tests, checks its schema, fuzzes it, measures what it spent, watches for
forbidden operating-system actions with an audit hook and logs every call the tool makes on its
session, then writes one JSON report. It decides nothing; the Comb's gates read the report. It is
started as `python -m hivemind.royal_jelly.quarantine_comb.runner <package_dir> <report_path>
<limits_json>`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Started by `QuarantineComb` through a `Sandbox`; its report model is also read by the gates.
    Calls into `hivemind.cell` and `hivemind.pheromone` (its session), `hivemind.llm` (argument
    validation) and the standard library; never anything above Layer 3.

Key invariants:
    - The runner is the one place that uses `resource`, `signal`, `sys.addaudithook` and
      `sys._getframe`.
    - It writes its report only after the tool's code has stopped running.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the runner and its attribution rule.
    - hivemind.royal_jelly.quarantine_comb.gates for the readers of its report.

Public API:
    - RUNNER_MODULE: the module name to pass to `python -m`.
    - main: the entry point, `main(argv) -> exit code`.
    - RunnerReport and its sections: the JSON document the runner writes.
"""

from hivemind.royal_jelly.quarantine_comb.runner.main import (
    RUNNER_MODULE,
    SCRATCH_DIR_NAME,
    SCRATCH_ENV,
    main,
)
from hivemind.royal_jelly.quarantine_comb.runner.report import (
    FuzzSection,
    RunnerReport,
    SchemaSection,
    SessionCall,
    UnittestSection,
    UsageSection,
    Violation,
)

__all__ = [
    "RUNNER_MODULE",
    "SCRATCH_DIR_NAME",
    "SCRATCH_ENV",
    "FuzzSection",
    "RunnerReport",
    "SchemaSection",
    "SessionCall",
    "UnittestSection",
    "UsageSection",
    "Violation",
    "main",
]
