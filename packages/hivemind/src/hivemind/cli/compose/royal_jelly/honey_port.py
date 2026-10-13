"""Adapt the Basket's Honey port to the Honey Store: one Nectar deposit per new blob.

Every blob the Basket takes in is described by exactly one Honey record, the only way a bee
finds it (ADR-0048). The Basket never imports the Honey Store, so it writes that record through
``HoneyRecordPort``; this module is the composition root's side of that port. ``NectarHoneyPort``
turns a ``HoneyRecord`` into a ``NectarSubmission`` for ``NectarIntake`` (a FINDING from a bee,
which Honey scopes to the whole Hive, keyed ``basket:<sha256>`` so a retry is deduplicated by
Honey itself); ``NullHoneyPort`` is the stand-in for a Hive with no Honey Store, which records
nothing and says so.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.cli.compose.royal_jelly``.
    Built by ``build_royal_jelly``; called by ``hivemind.basket.BasketDeposits`` once per new
    blob. Calls into ``hivemind.basket`` (the record and port types), ``hivemind.honey_store``
    (intake and submission), ``hivemind.cell`` and ``hivemind.common.logging`` only.

Key invariants:
    - Exactly one submission per record, with ``source_key`` ``basket:<sha256>``.
    - The Nectar's label is the record's label, which the Basket already raised to its own
      floor; Honey's intake then raises it to Honey's floor, never lowers it.
    - The submission holds the record's text (title, summary and facts), never the blob's bytes.

See Also:
    - hivemind.basket.models for HoneyRecord and HoneyRecordPort.
    - hivemind.honey_store.nectar for NectarIntake.submit.
"""

from __future__ import annotations

from hivemind.basket import HoneyRecord
from hivemind.cell import CombShieldLevel
from hivemind.common.logging import get_logger
from hivemind.honey_store import NectarIntake, NectarOrigin, NectarSubmission
from waggle.ids import CellId
from waggle.messages.honey import NectarKind

SOURCE_KEY_PREFIX = "basket:"  # `basket:<sha256>`: one Nectar per blob, whoever retries.
RECORD_MEDIA_TYPE = "text/markdown"  # The record is rendered with a heading and a bullet list.

__all__ = ["RECORD_MEDIA_TYPE", "SOURCE_KEY_PREFIX", "NectarHoneyPort", "NullHoneyPort"]

log = get_logger(__name__)


class NectarHoneyPort:
    """Write each new blob's Honey record as a Nectar deposit through the Honey intake."""

    def __init__(self, intake: NectarIntake, hive_stand_cell_id: CellId) -> None:
        """Wire the port to the Honey intake and the Hive Stand's Cell id.

        Args:
            intake: The Honey Store's one door for a whole deposit.
            hive_stand_cell_id: The Hive Stand's stable Cell id (``hive_stand_cell_id(node_id)``),
                the one Cell this adapter can tell is borrowed from the record alone.
        """
        self._intake = intake
        self._stand = hive_stand_cell_id

    async def record(self, record: HoneyRecord) -> None:
        """Submit the record as Nectar; see ``HoneyRecordPort.record``."""
        # Local SQLite writes behind intake's own lock; the Basket calls this before it keeps the
        # blob, so a failure here leaves no blob without a record.
        await self._intake.submit(_submission(record, self._stand))


class NullHoneyPort:
    """Record nothing: the port for a Hive that has no Honey Store."""

    async def record(self, record: HoneyRecord) -> None:
        """Log that the record was dropped; the blob is still stored, but no bee will find it."""
        log.warning("royal_jelly.honey_unavailable", sha256=record.sha256)


def _submission(record: HoneyRecord, hive_stand: CellId) -> NectarSubmission:
    """Build the FINDING submission for one record, deduplicated by ``basket:<sha256>``."""
    return NectarSubmission(
        kind=NectarKind.FINDING,
        origin=NectarOrigin.BEE,
        media_type=RECORD_MEDIA_TYPE,
        title=record.title,
        content=_text(record).encode("utf-8"),
        task_id=record.origin_task_id,
        cell_id=record.origin_cell_id,
        bee=record.origin_bee,
        observed_at=record.deposited_at,
        declared=record.clearance,
        # The adapter knows only the record, not the Queen's link table, so it treats the Hive
        # Stand (a borrowed Real Cell) as borrowed and every other origin as not. That can only
        # understate a Real device Cell, and the Basket has already raised record.clearance to
        # its own Real Cell floor, so the Nectar's label is never below the blob's.
        from_borrowed_cell=record.origin_cell_id == hive_stand,
        tier=CombShieldLevel.MEADOW,
        source_key=f"{SOURCE_KEY_PREFIX}{record.sha256}",
    )


def _text(record: HoneyRecord) -> str:
    """Render a record as markdown: the summary first, so a ripened summary keeps it."""
    platforms = ", ".join(tag.value for tag in record.platform_tags) or "any"
    lines = [
        f"# {record.title}",
        "",
        record.summary,
        "",
        f"- Basket blob: sha256 {record.sha256}",
        f"- Kind: {record.media_kind.value}",
        f"- Platforms: {platforms}",
        f"- Clearance: {record.clearance.value}",
        f"- Origin: Cell {record.origin_cell_id}"
        + (f", task {record.origin_task_id}" if record.origin_task_id is not None else "")
        + (f", bee {record.origin_bee}" if record.origin_bee is not None else ""),
        f"- Deposited: {record.deposited_at.isoformat()}",
    ]
    return "\n".join(lines) + "\n"
