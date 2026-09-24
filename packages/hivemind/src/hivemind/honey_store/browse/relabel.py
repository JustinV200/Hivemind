"""Change one Honey row's label by its browser path: the human's own recorded raise or lowering.

A label is a Honey row's `HoneyClearance` (data sensitivity: C0 public, C1 internal, C2 personal
or sensitive). ADR-0031 lets a model raise a label and never lower one; lowering is a separate,
recorded act that only a judge verdict or a human may approve (codingrules 8.9). The provenance
floor makes everything a Drone does on the Hive Stand C2, so the operator reviewing labels is an
expected, ordinary job (`hive honey relabel`). `HoneyRelabeller` is that job, addressed the way
the browser addresses everything, by path: it finds the row as the operator's reader sees it,
then raises it (`honey.label_raised`) or, after `clearance.check_lowering` with a HUMAN approver,
lowers it (`honey.label_lowered`), each event committed with the change. The browser itself stays
read-only: this is the one Honey write reachable from a path, and it is never implicit.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. Called by `hive honey relabel`. Calls into this package's own store,
    clearance rules, identity and retrieval reader, this sub-package's `paths`, `documents` and
    `errors`, and `waggle` only.

Key invariants:
    - A lowering always passes `check_lowering` with approver HUMAN before the store is touched,
      and records that approver, the reason and both labels on its event.
    - Asking for the label a row already has writes nothing and records nothing.
    - Only a row the reader may see can be relabelled; any other path is "not found".

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the labelling rule.
    - hivemind.honey_store.clearance for raise_label and check_lowering.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.documents import visible_honey
from hivemind.honey_store.browse.errors import BrowseInputError, BrowsePathError
from hivemind.honey_store.browse.paths import PathKind, parse_path
from hivemind.honey_store.clearance import LabelApprover, check_lowering, raise_label
from hivemind.honey_store.honey import HoneyReader
from hivemind.honey_store.identity import HoneyIdentity, honey_event
from hivemind.honey_store.models import Honey
from hivemind.honey_store.store import HoneyStore
from hivemind.pheromone import HoneyEvent
from waggle.clock import Clock

LABEL_RAISED_KIND = "honey.label_raised"  # A label moved up.
LABEL_LOWERED_KIND = "honey.label_lowered"  # A label moved down, with its approver.
MAX_RELABEL_REASON_CHARS = 500  # A sentence or two on the trail, like every other reason there.

__all__ = [
    "LABEL_LOWERED_KIND",
    "LABEL_RAISED_KIND",
    "MAX_RELABEL_REASON_CHARS",
    "HoneyRelabeller",
    "RelabelDirection",
    "RelabelOutcome",
    "RelabelRequest",
]


class RelabelDirection(Enum):
    """Which way a relabel moved a row's label."""

    RAISED = "RAISED"  # To a more sensitive label: `honey.label_raised`.
    LOWERED = "LOWERED"  # To a less sensitive label, HUMAN-approved: `honey.label_lowered`.
    UNCHANGED = "UNCHANGED"  # The row already had the asked-for label: nothing written.


@dataclass(frozen=True, slots=True)
class RelabelRequest:
    """One relabel the human asked for: which row, to what label, and why."""

    path: str  # The Honey row's browser path, e.g. "/hive/<honey id>".
    target: HoneyClearance  # The label it should carry.
    reason: str  # Why; recorded on the event, at most MAX_RELABEL_REASON_CHARS.


@dataclass(frozen=True, slots=True)
class RelabelOutcome:
    """What a relabel did: the row as it now stands, its label before, and the direction."""

    honey: Honey  # The row after the change (unchanged for UNCHANGED).
    before: HoneyClearance  # Its label before the request.
    direction: RelabelDirection  # Raised, lowered, or left as it was.


class HoneyRelabeller:
    """Raise or lower one Honey row's label for the human, recorded on the Pheromone Trail."""

    def __init__(self, store: HoneyStore, identity: HoneyIdentity, clock: Clock) -> None:
        """Keep the store to write through and the identity and clock events are stamped with.

        Args:
            store: The Honey Store holding the row.
            identity: The Hive, node and actor ("human") every event is stamped with.
            clock: Mints each event's id and time.
        """
        self._store = store
        self._identity = identity
        self._clock = clock

    async def relabel(self, request: RelabelRequest, reader: HoneyReader) -> RelabelOutcome:
        """Move one visible row's label to `request.target`, recording which way it moved.

        Args:
            request: The row's path, the label it should carry, and why.
            reader: The human's reader: only a row it may see can be relabelled.

        Returns:
            The row as it now stands, its label before, and whether it rose, fell or stayed.

        Raises:
            BrowseInputError: The reason is empty or over MAX_RELABEL_REASON_CHARS.
            BrowsePathError: The path does not parse, or names something other than a Honey row.
            BrowseNotFoundError: No row the reader may see is at the path.
        """
        reason = _checked_reason(request.reason)
        target = parse_path(request.path)
        # Only a Honey row carries a label this command may change; wax and Bee Bread do not.
        if target.kind is not PathKind.HONEY:
            raise BrowsePathError(target.path, "relabel takes one Honey row's path")
        honey = await visible_honey(self._store, target, reader)
        before = honey.clearance
        wanted = request.target
        # Asking for the label a row already carries is not a change: nothing to write or record.
        if wanted.rank == before.rank:
            return RelabelOutcome(honey=honey, before=before, direction=RelabelDirection.UNCHANGED)
        # Up: a raise needs no approver (ADR-0031: any writer may make a label stricter).
        if wanted.rank > before.rank:
            raised = raise_label(before, wanted)
            event = self._event(LABEL_RAISED_KIND, honey, raised, reason)
            # One transaction for the new label and its event; local SQLite, milliseconds.
            updated = await self._store.raise_clearance(honey.id, raised, event)
            return RelabelOutcome(honey=updated, before=before, direction=RelabelDirection.RAISED)
        # The one gate between a lowering and the store (codingrules 8.9): a HUMAN approver.
        check_lowering(before, wanted, LabelApprover.HUMAN)
        event = self._event(LABEL_LOWERED_KIND, honey, wanted, reason)
        # One transaction for the new label and its event; local SQLite, milliseconds.
        updated = await self._store.lower_clearance(honey.id, wanted, event)
        return RelabelOutcome(honey=updated, before=before, direction=RelabelDirection.LOWERED)

    def _event(self, kind: str, honey: Honey, to: HoneyClearance, reason: str) -> HoneyEvent:
        """Mint a raise or lowering event: both labels, the HUMAN approver and the reason."""
        return honey_event(
            self._identity,
            self._clock,
            kind,
            honey.id,
            approver=LabelApprover.HUMAN.value,
            reason=reason,
            **{"from": honey.clearance.value, "to": to.value},
        )


def _checked_reason(reason: str) -> str:
    """Return the stripped reason, refusing an empty or overlong one: a relabel must say why."""
    stripped = reason.strip()
    # The reason is the audit record's whole explanation, so it is required and bounded.
    if not stripped:
        raise BrowseInputError("reason", "a relabel must say why")
    if len(stripped) > MAX_RELABEL_REASON_CHARS:
        raise BrowseInputError("reason", f"it is over {MAX_RELABEL_REASON_CHARS} characters")
    return stripped
