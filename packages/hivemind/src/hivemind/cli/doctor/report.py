"""Define one `hive doctor` check's result and print a list of them.

Every check `hive doctor` runs ends as one `Check`: a short name, an outcome and one line of why.
PASSED and FAILED mean what they say; WARNED is a Hive that will run, but worse than it could
(sqlite-vec missing, a slot's model not priced by Forage); SKIPPED is a check that does not apply
(no Docker backend configured) or cannot be made from here (a hosted provider that lists no
models). Only a FAILED check makes the command exit 1, so a script can gate on the exit code.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.doctor`. Built by this package's check modules,
    printed by `hivemind.cli.doctor.command`. Calls into pydantic and typer only.

Key invariants:
    - A detail is one line and never holds a secret (an API key is reported present or absent).

See Also:
    - hivemind.cli.doctor.command for the order the checks run in.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

import typer
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Check", "Outcome", "print_checks", "verdict"]


class Outcome(StrEnum):
    """How one check ended; only FAILED fails the command."""

    PASSED = "PASSED"  # Holds.
    WARNED = "WARNED"  # The Hive runs, but degraded or unchecked in a way the operator should know.
    FAILED = "FAILED"  # The Hive cannot run as configured.
    SKIPPED = "SKIPPED"  # Does not apply to this manifest, or cannot be checked from here.


class Check(BaseModel):
    """One `hive doctor` result, as printed and as `--json` emits it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="What was checked, e.g. 'provider local' or 'scratch root'.")
    outcome: Outcome = Field(description="How the check ended.")
    detail: str = Field(description="One line of why; never a secret.")


def verdict(checks: Iterable[Check]) -> bool:
    """Return True when no check FAILED (warnings and skips still pass)."""
    return all(check.outcome is not Outcome.FAILED for check in checks)


def print_checks(checks: Iterable[Check]) -> None:
    """Print one fixed-width line per check: outcome, name, detail."""
    for check in checks:
        typer.echo(f"  {check.outcome:<8} {check.name:<26} {check.detail}")
