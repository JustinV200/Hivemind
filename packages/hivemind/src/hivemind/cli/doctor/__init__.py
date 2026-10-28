"""Provide `hive doctor`: the pre-flight check of this machine and its Hive Manifest.

Roadmap 14.2. `command` runs every check and folds in roadmap 13.2a's `--supersedure NODE`
candidate test; `host` checks the scratch root, sqlite-vec and Docker; `models` checks the
providers and every slot's model; `config` checks the escalation policy and Forage; `report` holds
the result type and its printing.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli`. Registered as `hive doctor` by
    `hivemind.cli.app`. Calls into this package's own modules only.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - hivemind.cli.supersede for the Supersedure candidate test.

Public API:
    - doctor_command, DoctorDeps, run_checks (command): the command, its seams and every check.
    - Check, Outcome (report): one check's result.
"""

from hivemind.cli.doctor.command import DoctorDeps, doctor_command, run_checks
from hivemind.cli.doctor.report import Check, Outcome

__all__ = ["Check", "DoctorDeps", "Outcome", "doctor_command", "run_checks"]
