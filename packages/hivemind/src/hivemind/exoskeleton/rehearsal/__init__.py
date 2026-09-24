"""Export verified browser work as a procedure and rehearse it: the rehearsal package.

ADR-0032, "Reusable browser work is rehearsed" (roadmap step 6.7): a verified flight recording is
exported as a `BrowserProcedure` (`procedure`), moved onto a fixture or staging copy of its site
(`rebase`), and replayed there (`rehearse`); the `RehearsalReport` is what the Royal Jelly Lab (9.3)
asks for before a procedure is promoted as a tool, so production runs execute a capped procedure
rather than an improvised one.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Called by the `hive recordings` commands. Calls into the exoskeleton's `attach`, `errors`,
    `recorder` and `surface`, `hivemind.supervision.capping` and waggle.

Key invariants:
    - No procedure and no report ever holds a secret's value.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.

Public API:
    - BrowserProcedure, ProcedureAction, SecretSlot, export_procedure, rebase, origin_of,
      MAX_PROCEDURE_ACTIONS, NAME_PATTERN (procedure).
    - RehearsalReport, RehearsedAction, rehearse (rehearse).
"""

from hivemind.exoskeleton.rehearsal.procedure import (
    MAX_PROCEDURE_ACTIONS,
    NAME_PATTERN,
    BrowserProcedure,
    ProcedureAction,
    SecretSlot,
    export_procedure,
    origin_of,
    rebase,
)
from hivemind.exoskeleton.rehearsal.rehearse import RehearsalReport, RehearsedAction, rehearse

__all__ = [
    "MAX_PROCEDURE_ACTIONS",
    "NAME_PATTERN",
    "BrowserProcedure",
    "ProcedureAction",
    "RehearsalReport",
    "RehearsedAction",
    "SecretSlot",
    "export_procedure",
    "origin_of",
    "rebase",
    "rehearse",
]
