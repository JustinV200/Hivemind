"""Provide `hive supersede` and `hive doctor --supersedure`: the operator's Supersedure commands.

Roadmap 13.2a, ADR-0065. `command` moves the Hive Stand (as the console, after a step-up, with
the typed phrase) or settles a Supersedure a crash left open; `doctor` runs the candidate test.
This file is the package's face.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli`. Registered by `hivemind.cli.app`. Calls into
    this package's own modules only.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - docs/runbooks/supersedure.md.

Public API:
    - supersede_command, Verdict: `hive supersede` (command).
    - supersedure_doctor: `hive doctor --supersedure`, called by `hivemind.cli.doctor` (doctor).
"""

from hivemind.cli.supersede.command import Verdict, supersede_command
from hivemind.cli.supersede.doctor import supersedure_doctor

__all__ = ["Verdict", "supersede_command", "supersedure_doctor"]
