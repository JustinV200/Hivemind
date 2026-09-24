"""Define GuardRequestStore: the persistence seam for the Queen's durable Guard requests.

A Guard request (ADR-0035) is only useful if it survives the Queen: `GuardRequestDoor` is "durable
before it returns", so the request a Guard Bee filed just before a restart is decided after it.
This protocol is that table's whole surface, shaped like the chat log's rather than the goal
requests' state machine: `file` writes a fresh row (idempotently by report id: the Guard Bee's
retry never files one report twice), `pending` reads the rows still waiting for her tick oldest
first, `decide` stamps her decision once, `get` reads one row back, and `holds` and
`release_holds` are the placement holds her Hive Stand fallback leaves and the human's lift
releases. No write here records a trail event: the report's own audit row is the Guard Bee's
`guard.alert`, and her decision is the `queen.decided` she records before she acts, so a crash
between deciding and stamping only ever means the request is decided again, and every action it
leads to is idempotent.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package. Implemented by `.memory` and `.sqlite`; written by the Queen's door
    and her decision on a request; read by her tick, the dispatcher's placement snapshot (holds)
    and the isolation lift. Calls into the sub-package's own model and waggle only.

Key invariants:
    - `pending` returns rows oldest first by `(filed_at, id)`, the order they entered her inbox.
    - A decision is stamped at most once: a second `decide` for the same request changes nothing.
    - A hold is released at most once, and only by `release_holds`.

See Also:
    - hivemind.queen.chat.protocol for ChatLog, whose "unhandled until stamped" shape this mirrors.
    - hivemind.queen.guard_requests.model for the row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hivemind.queen.guard_requests.model import GuardDecision, GuardRequest, PlacementHold
from waggle.ids import CellId

DEFAULT_PENDING_PAGE = 50  # More requests than one tick decides; the Guard Bee caps its own rate.

__all__ = ["DEFAULT_PENDING_PAGE", "GuardRequestStore"]


class GuardRequestStore(Protocol):
    """Persist the Queen's Guard requests and her decisions on them; safe to call concurrently."""

    async def file(self, request: GuardRequest) -> bool:
        """Write a fresh, undecided request; a report already filed is left as it is.

        Args:
            request: The request, `decision` and `hold` unset.

        Returns:
            True when this call wrote the row; False when the report id was already filed.
        """
        ...

    async def get(self, report_id: str) -> GuardRequest | None:
        """Return the request filed under `report_id`, or None.

        Args:
            report_id: A `guardrep_` id.

        Returns:
            The stored request, decided or not.
        """
        ...

    async def pending(self, limit: int = DEFAULT_PENDING_PAGE) -> tuple[GuardRequest, ...]:
        """Return the requests still waiting for a decision, oldest first.

        Args:
            limit: The most rows to return; the rest wait for a later tick.

        Returns:
            At most `limit` undecided requests, by `(filed_at, id)`.
        """
        ...

    async def decide(
        self, report_id: str, decision: GuardDecision, hold: PlacementHold | None = None
    ) -> GuardRequest | None:
        """Stamp the Queen's decision (and any hold it left) on a pending request.

        Idempotent: a request already decided keeps its first decision and is returned as it is.

        Args:
            report_id: The request decided.
            decision: What she decided, on what basis, and what came of it.
            hold: The placement hold the decision left, if any.

        Returns:
            The stored request after the stamp; None when no request has that id.
        """
        ...

    async def holds(self) -> tuple[PlacementHold, ...]:
        """Return every placement hold not yet released, oldest first.

        Returns:
            The active holds; placement reads each as a block on its Cell for its goals.
        """
        ...

    async def release_holds(
        self, cell_id: CellId, released_at: datetime
    ) -> tuple[PlacementHold, ...]:
        """Release every active hold on `cell_id` (the human's lift).

        Args:
            cell_id: The Cell the human lifted.
            released_at: When.

        Returns:
            The holds this call released, as released; empty when none held that Cell.
        """
        ...
