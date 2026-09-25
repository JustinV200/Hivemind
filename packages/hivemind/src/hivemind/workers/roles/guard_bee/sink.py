"""Define the seam a Guard report is deposited through as C2 Nectar for the Honey browser.

Every Guard Bee report (roadmap step 10.6, ADR-0043) is a `guard.alert` on the trail and also a
`C2` deposit (the Hive's most sensitive clearance, the one every security record carries) on the
Honey browser's path, so the human can read the Guard's findings where they read everything else
the Hive knows. Nectar intake (the Hive's raw material before it ripens into Honey) lands in
phase 7, so this module only names the seam, `GuardReportSink`, and ships an in-memory
implementation, exactly as `hivemind.supervision.capping.audit.sampler.FindingsSink` does for audit
findings; phase 7's intake implements the same Protocol and the composition root swaps it in.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Called by
    `.respond.FindingResponder` once per report; built by the composition root through
    `.bee.build_guard_bee` (in-memory by default). Calls into `hivemind.cell` (HoneyClearance) and
    `hivemind.guard` (GuardReport) only.

Key invariants:
    - A deposit is always `HoneyClearance.C2`: a security finding about the Hive is never read at
      a lower clearance.
    - A deposit's scope is a Honey browser folder derived from the report, never stored twice:
      `/cells/<id>` when it names a Cell, else `/hive`.

See Also:
    - hivemind.supervision.capping.audit.sampler for FindingsSink, the seam this one mirrors.
    - .claude/codingrules.md section 8.11 for the Honey browser's folders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from hivemind.cell import HoneyClearance
from hivemind.guard import GuardReport

HIVE_SCOPE = "/hive"  # The Honey browser folder of a report that names no Cell.

__all__ = ["HIVE_SCOPE", "GuardDeposit", "GuardReportSink", "InMemoryGuardReportSink"]


@dataclass(frozen=True, slots=True)
class GuardDeposit:
    """One Guard report on its way into Nectar: the report, its clearance and its folder."""

    report: GuardReport
    clearance: HoneyClearance = field(default=HoneyClearance.C2)

    @property
    def scope(self) -> str:
        """The Honey browser folder the report belongs in (codingrules 8.11)."""
        cell = self.report.cell_id
        return f"/cells/{cell}" if cell is not None else HIVE_SCOPE


class GuardReportSink(Protocol):
    """Deposit one Guard report as C2 Nectar; phase 7's Nectar intake implements it for real."""

    async def deposit(self, deposit: GuardDeposit) -> None:
        """Deposit one report.

        Args:
            deposit: The report, at C2, with its folder.
        """
        ...


class InMemoryGuardReportSink:
    """An in-memory GuardReportSink: keeps every deposit in a list, for tests and phase 10."""

    def __init__(self) -> None:
        """Build a sink holding nothing yet."""
        self.deposits: list[GuardDeposit] = []

    async def deposit(self, deposit: GuardDeposit) -> None:
        """Append `deposit` to `deposits`.

        Args:
            deposit: The report, at C2, with its folder.
        """
        self.deposits.append(deposit)
