"""Provide MeasuredGradeBook: the grades the evaluation harness measured, kept by the ledger.

Roadmap step 8.5 (ADR-0045): `hive llm eval --update-map` scores each model source (an entry of
the Forage map: one model on one server) against a fixed goal set and writes a grade from 1 to 5.
Those grades live here, in the Queen's ledger, rather than in the manifest, because the manifest
is the operator's hand-edited file and the Hive never writes to it. `build_forage_map` reads
`MeasuredGradeBook.grades` when it builds the next Hive's map and applies each one as the source's
`measured_grade`, which routing, allocation and the spill rule already prefer over the hand-set
`grade`. A third sub-book of `ForageLedger` after `SeatBook` and `DecisionBook`, with the same
shape: an in-memory table written through to the store, restored on start.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    ledger sub-package. Held as `ForageLedger.measured`; written by `hive llm eval` and read by
    the Hive's composition root. Calls into `hivemind.queen.forage.ledger` (model, store_protocol)
    only.

Key invariants:
    - A fresh measurement for a source replaces the earlier one; the book never keeps history.
    - `grades()` returns only grades inside 1 to 5: `MeasuredGrade` refuses anything else.

See Also:
    - docs/adr/0045-provider-routing-forage-aware-fallbacks-and-offline-mode.md.
    - hivemind.queen.forage.ledger.decisions for DecisionBook, the pattern this follows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime

from hivemind.queen.forage.ledger.model import MeasuredGrade
from hivemind.queen.forage.ledger.store_protocol import LedgerStore

__all__ = ["MeasuredGradeBook"]


class MeasuredGradeBook:
    """Own the measured grade of each Forage map source the harness has graded."""

    def __init__(self, store: LedgerStore | None) -> None:
        """Build an empty book.

        Args:
            store: Where a fresh measurement is written through; None keeps the book in memory.
        """
        self._store = store
        self._rows: dict[str, MeasuredGrade] = {}
        self._lock = asyncio.Lock()

    async def record(
        self, source_id: str, grade: int, measured_at: datetime, evidence: str
    ) -> None:
        """Store a source's freshly measured grade, replacing any earlier measurement.

        Args:
            source_id: The `[forage.map.<source_id>]` key that was measured.
            grade: The measured grade, 1 to 5.
            measured_at: When it was measured (timezone-aware).
            evidence: A short plain-text summary of the run behind the grade.

        Raises:
            pydantic.ValidationError: `grade` is outside 1 to 5.
        """
        row = MeasuredGrade(
            source_id=source_id, grade=grade, measured_at=measured_at, evidence=evidence
        )
        async with self._lock:
            self._rows[source_id] = row
            if self._store is not None:
                await self._store.put_measured_grade(source_id, grade, measured_at, evidence)

    def grades(self) -> Mapping[str, int]:
        """Return each measured source's grade, the shape `build_forage_map` takes."""
        return {source_id: row.grade for source_id, row in self._rows.items()}

    def rows(self) -> tuple[MeasuredGrade, ...]:
        """Return every measurement with its time and evidence, for a report."""
        return tuple(self._rows.values())

    async def restore(self) -> None:
        """Rebuild the table from `self._store`; a no-op with no store."""
        if self._store is None:
            return
        async with self._lock:
            self._rows = {row.source_id: row for row in await self._store.list_measured_grades()}
